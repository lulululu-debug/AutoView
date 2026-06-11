"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, api, type InterviewPlan, type TurnResult } from "@/lib/api";

/**
 * 面试 Q&A 主界面:
 * - 首次进入: POST /interviews 启动 session, session_id 写 localStorage
 * - 已有 localStorage: GET /interviews/{id} 中断恢复, 拉回当前待答提示
 * - 提交答案: POST /interviews/{id}/answers, 显示下一句; done=true 跳 /done
 * - session 过期 (Redis TTL): 提示重新上传, 清 localStorage
 *
 * 进度展示 (Sprint 4-5): 拉一次 plan, 用当前 turn.ref_id 在 plan.questions
 * 里匹配出"第 M/N 题"; 匹配不到 -> 是 followup, 题号不变, 加"追问"徽章。
 *
 * 答题草稿 (Sprint 4-5): 输入时按 (session_id, ref_id) 存 localStorage,
 * 刷新页面后恢复; 提交成功后清掉本道题的草稿。
 */

const MIN_ANSWER_CHARS = 5;
const SESSION_KEY = (cid: string) => `interview_session_${cid}`;
const DRAFT_KEY = (sid: string, refId: string) =>
  `interview_draft_${sid}_${refId}`;

type Progress = {
  current_q_index: number; // 1-based 题号
  total_q: number;
  is_followup: boolean;
};

type State =
  | { kind: "starting" }
  | {
      kind: "answering";
      turn: TurnResult;
      plan: InterviewPlan;
      progress: Progress;
      answered_count: number;
    }
  | {
      kind: "submitting";
      turn: TurnResult;
      plan: InterviewPlan;
      progress: Progress;
      answered_count: number;
    }
  | { kind: "expired" }
  | { kind: "error"; message: string };

function computeProgress(
  plan: InterviewPlan,
  refId: string | null,
  prevIndex: number,
): Progress {
  const questions = plan.rounds[0]?.questions ?? [];
  const total = questions.length;
  if (!refId) {
    return { current_q_index: 1, total_q: total, is_followup: false };
  }
  const idx = questions.findIndex((q) => q.question_id === refId);
  if (idx >= 0) {
    return { current_q_index: idx + 1, total_q: total, is_followup: false };
  }
  // ref_id 不在 plan.questions 里 -> 是上一道题的 followup
  return { current_q_index: prevIndex, total_q: total, is_followup: true };
}

export default function SessionPage({
  params,
}: {
  params: Promise<{ jobId: string; candidateId: string }>;
}) {
  const { jobId, candidateId } = use(params);
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "starting" });
  const [answer, setAnswer] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        const existing = readSession(candidateId);
        let turn: TurnResult;
        if (existing) {
          try {
            turn = await api.resumeInterview(existing);
          } catch (e) {
            if (e instanceof ApiError && e.status === 404) {
              clearSession(candidateId);
              turn = await api.startInterview(candidateId);
              writeSession(candidateId, turn.session_id);
            } else throw e;
          }
        } else {
          turn = await api.startInterview(candidateId);
          writeSession(candidateId, turn.session_id);
        }
        if (cancelled) return;
        if (turn.done) {
          router.push(`/interview/${jobId}/${candidateId}/done`);
          return;
        }

        // Sprint 4-5: 拉 plan 算进度. plan 在 BG planner 跑完才会存在,
        // waiting 页轮询到 200 才会跳来这里, 所以这里不会 404。
        // 但保险起见 404 也走 fallback (没 plan 时不显示进度, 只显示题目)。
        const plan = await api.getCandidatePlan(jobId, candidateId);
        if (cancelled) return;
        const progress = computeProgress(plan, turn.ref_id, 1);

        // 草稿恢复
        if (turn.ref_id) {
          const draft = readDraft(turn.session_id, turn.ref_id);
          if (draft) setAnswer(draft);
        }

        setState({
          kind: "answering",
          turn,
          plan,
          progress,
          answered_count: 0,
        });
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "error", message: errMessage(e) });
      }
    }

    init();
    return () => {
      cancelled = true;
    };
  }, [jobId, candidateId, router]);

  function handleAnswerChange(ev: React.ChangeEvent<HTMLTextAreaElement>) {
    const val = ev.target.value;
    setAnswer(val);
    // 草稿自动存
    if (state.kind === "answering" && state.turn.ref_id) {
      writeDraft(state.turn.session_id, state.turn.ref_id, val);
    }
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (state.kind !== "answering") return;
    const text = answer.trim();
    if (text.length < MIN_ANSWER_CHARS) return;

    const prev = state;
    setState({
      kind: "submitting",
      turn: prev.turn,
      plan: prev.plan,
      progress: prev.progress,
      answered_count: prev.answered_count,
    });
    try {
      const next = await api.submitAnswer(prev.turn.session_id, text);
      // 本题草稿用完了, 清掉
      if (prev.turn.ref_id) {
        clearDraft(prev.turn.session_id, prev.turn.ref_id);
      }
      setAnswer("");
      if (next.done) {
        router.push(`/interview/${jobId}/${candidateId}/done`);
        return;
      }
      const nextProgress = computeProgress(
        prev.plan, next.ref_id, prev.progress.current_q_index,
      );
      // 新 prompt 的草稿恢复 (一般是空, 但万一用户之前误进过这道题…)
      if (next.ref_id) {
        const draft = readDraft(next.session_id, next.ref_id);
        if (draft) setAnswer(draft);
      }
      setState({
        kind: "answering",
        turn: next,
        plan: prev.plan,
        progress: nextProgress,
        answered_count: prev.answered_count + 1,
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        clearSession(candidateId);
        setState({ kind: "expired" });
        return;
      }
      setState({ kind: "error", message: errMessage(e) });
    }
  }

  return (
    <main className="min-h-screen flex items-start justify-center bg-zinc-50 dark:bg-black p-4 sm:p-6">
      <div className="w-full max-w-2xl mt-8 sm:mt-12 mb-12">
        {state.kind === "starting" && (
          <p className="text-zinc-500 text-center mt-20">进入面试中...</p>
        )}

        {(state.kind === "answering" || state.kind === "submitting") && (
          <>
            <ProgressHeader
              progress={state.progress}
              answered={state.answered_count}
            />

            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 sm:p-6 mb-4">
              <div className="flex items-center gap-2 mb-2">
                <p className="text-xs text-zinc-500">面试官</p>
                {state.progress.is_followup && (
                  <span className="text-xs px-2 py-0.5 rounded bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-300">
                    追问
                  </span>
                )}
              </div>
              <p className="text-base leading-relaxed whitespace-pre-line">
                {state.turn.prompt}
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-3">
              <textarea
                value={answer}
                onChange={handleAnswerChange}
                disabled={state.kind === "submitting"}
                rows={10}
                placeholder="你的回答..."
                className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-3 text-sm disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-zinc-400"
                autoFocus
              />
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs text-zinc-500">
                  {answer.length} 字 · 答得越具体, AI 越能跟着深挖
                </p>
                <button
                  type="submit"
                  disabled={
                    state.kind === "submitting" ||
                    answer.trim().length < MIN_ANSWER_CHARS
                  }
                  className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-5 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50 shrink-0"
                >
                  {state.kind === "submitting" ? "提交中..." : "提交回答"}
                </button>
              </div>
            </form>
          </>
        )}

        {state.kind === "expired" && (
          <FailureView
            title="会话已过期"
            body="面试 session 超过保留时间或已结束, 请重新上传简历开始新的面试。"
            onAction={() => router.push(`/interview/${jobId}`)}
            actionLabel="重新上传简历"
          />
        )}

        {state.kind === "error" && (
          <FailureView
            title="出错了"
            body={state.message}
            onAction={() => router.push(`/interview/${jobId}`)}
            actionLabel="重新开始"
          />
        )}
      </div>
    </main>
  );
}

function ProgressHeader({
  progress,
  answered,
}: {
  progress: Progress;
  answered: number;
}) {
  return (
    <div className="flex items-center justify-between mb-5">
      <p className="text-xs text-zinc-500 uppercase tracking-wide">
        AI 面试 · 进行中
      </p>
      <div className="flex items-center gap-3 text-xs text-zinc-500">
        <span>
          第 <span className="font-medium text-zinc-700 dark:text-zinc-300">
            {progress.current_q_index}
          </span>
          /{progress.total_q} 题
        </span>
        <span className="text-zinc-300 dark:text-zinc-700">·</span>
        <span>已答 {answered}</span>
      </div>
    </div>
  );
}

function FailureView({
  title,
  body,
  onAction,
  actionLabel,
}: {
  title: string;
  body: string;
  onAction: () => void;
  actionLabel: string;
}) {
  return (
    <div className="text-center mt-20">
      <h1 className="text-2xl font-semibold mb-3 text-red-600 dark:text-red-400">
        {title}
      </h1>
      <p className="text-zinc-600 dark:text-zinc-400 mb-6 font-mono text-sm">
        {body}
      </p>
      <button
        onClick={onAction}
        className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-4 py-2 text-sm font-medium hover:opacity-90"
      >
        {actionLabel}
      </button>
    </div>
  );
}

// ---- localStorage helpers (try/catch 兜底 SSR / private mode) ----

function readSession(candidateId: string): string | null {
  try {
    return localStorage.getItem(SESSION_KEY(candidateId));
  } catch {
    return null;
  }
}
function writeSession(candidateId: string, sessionId: string) {
  try {
    localStorage.setItem(SESSION_KEY(candidateId), sessionId);
  } catch {}
}
function clearSession(candidateId: string) {
  try {
    localStorage.removeItem(SESSION_KEY(candidateId));
  } catch {}
}

function readDraft(sessionId: string, refId: string): string | null {
  try {
    return localStorage.getItem(DRAFT_KEY(sessionId, refId));
  } catch {
    return null;
  }
}
function writeDraft(sessionId: string, refId: string, val: string) {
  try {
    if (val) {
      localStorage.setItem(DRAFT_KEY(sessionId, refId), val);
    } else {
      localStorage.removeItem(DRAFT_KEY(sessionId, refId));
    }
  } catch {}
}
function clearDraft(sessionId: string, refId: string) {
  try {
    localStorage.removeItem(DRAFT_KEY(sessionId, refId));
  } catch {}
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
