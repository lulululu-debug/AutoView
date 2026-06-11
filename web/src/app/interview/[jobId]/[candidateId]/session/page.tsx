"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, api, type TurnResult } from "@/lib/api";

/**
 * 面试 Q&A 主界面:
 * - 首次进入: POST /interviews 启动 session, session_id 写 localStorage
 * - 已有 localStorage: GET /interviews/{id} 中断恢复, 拉回当前待答提示
 * - 提交答案: POST /interviews/{id}/answers, 显示下一句; done=true 跳 /done
 * - session 过期 (Redis TTL): 提示重新上传, 清 localStorage
 *
 * 进度展示: 只显示"已答 N 题", 不显示总数 (追问会让总轮次在 4~8 间浮动,
 * 给候选人看具体上限反而焦虑)。
 */

const MIN_ANSWER_CHARS = 5;
const SESSION_KEY = (cid: string) => `interview_session_${cid}`;

type State =
  | { kind: "starting" }
  | { kind: "answering"; turn: TurnResult; answered_count: number }
  | { kind: "submitting"; turn: TurnResult; answered_count: number }
  | { kind: "expired" }
  | { kind: "error"; message: string };

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
              // session 已过期 / 已 finalize, 重新启动
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
        setState({ kind: "answering", turn, answered_count: 0 });
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

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (state.kind !== "answering") return;
    const text = answer.trim();
    if (text.length < MIN_ANSWER_CHARS) return;

    const prev = state;
    setState({
      kind: "submitting",
      turn: prev.turn,
      answered_count: prev.answered_count,
    });
    try {
      const next = await api.submitAnswer(prev.turn.session_id, text);
      setAnswer("");
      if (next.done) {
        router.push(`/interview/${jobId}/${candidateId}/done`);
        return;
      }
      setState({
        kind: "answering",
        turn: next,
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
    <main className="min-h-screen flex items-start justify-center bg-zinc-50 dark:bg-black p-6">
      <div className="w-full max-w-2xl mt-12 mb-12">
        {state.kind === "starting" && (
          <p className="text-zinc-500 text-center mt-20">进入面试中...</p>
        )}

        {(state.kind === "answering" || state.kind === "submitting") && (
          <>
            <div className="flex items-center justify-between mb-6">
              <p className="text-xs text-zinc-500 uppercase tracking-wide">
                AI 面试 · 进行中
              </p>
              <p className="text-xs text-zinc-500">
                已答 {state.answered_count} 题
              </p>
            </div>

            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-6 mb-4">
              <p className="text-xs text-zinc-500 mb-2">面试官</p>
              <p className="text-base leading-relaxed whitespace-pre-line">
                {state.turn.prompt}
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-3">
              <textarea
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                disabled={state.kind === "submitting"}
                rows={10}
                placeholder="你的回答..."
                className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-3 text-sm disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-zinc-400"
                autoFocus
              />
              <div className="flex items-center justify-between">
                <p className="text-xs text-zinc-500">
                  {answer.length} 字 · 答得越具体, AI 越能跟着深挖
                </p>
                <button
                  type="submit"
                  disabled={
                    state.kind === "submitting" ||
                    answer.trim().length < MIN_ANSWER_CHARS
                  }
                  className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-5 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
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
  } catch {
    /* private mode 等失败时静默, 用户回退后没法恢复但能继续 */
  }
}

function clearSession(candidateId: string) {
  try {
    localStorage.removeItem(SESSION_KEY(candidateId));
  } catch {
    /* 同上 */
  }
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
