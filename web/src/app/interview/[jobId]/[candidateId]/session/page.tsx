"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ApiError,
  FILLER_COUNT,
  api,
  transcribeWsUrl,
  type InterviewPlan,
  type TurnResult,
} from "@/lib/api";
import {
  AiBadge,
  ConsentGate,
  InterviewerPanel,
  SelfView,
  useCandidateMedia,
  type AvatarState,
} from "./media";
import { SpeechCapture } from "./stt";

/**
 * 面试 Q&A 主界面:
 * - Consent 门 (Sprint 6-1): 进面试前先过知情同意, 选择开摄像头或仅文字;
 *   拒绝授权 / getUserMedia 失败 → 降级纯文字面试, 流程不断
 * - TTS 播报 (Sprint 6-2): AV 模式下每个新 turn 拉 GET .../turns/{ref_id}/audio
 *   播放面试官语音; 204/失败/自动播放被拦一律静默, 文字永远是主路径
 * - 语音作答 (Sprint 6-4): /media/config 探测 STT 后显示麦克风入口, 录音走
 *   WS 转写, partial 实时预览, final 追加进 textarea 可编辑再提交 ——
 *   文本框是唯一真相源, STT 挂了退打字
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
  | { kind: "consent" }
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
  // Sprint 5.5: plan 是多 round stage 序列, 跨所有 round flatten 走总进度
  const questions = plan.rounds.flatMap((r) => r.questions);
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

/**
 * Sprint 5.5 task 4: 判"提交完当前答案后, 后端是否会触发 project 题 lazy gen"。
 * 用来把按钮文案从 "提交中..." 换成 "思考中... (准备项目题, 约需 3-5 秒)"。
 *
 * 启发式: 当前 ref_id 是某 round 的"最后一题"且下一个 round 是 PROJECT stage,
 * 且下一个 round 至少一道题 lazy && text 为空 (尚未 resolve)。
 * followup 答案我们不当作"最后一题" (refId 不在 question 列表里), 所以不会误判。
 *
 * 误差: 当前题的 Interviewer 启发式可能触发追问 -> 实际下一个不是 project 而是
 * followup. 这种情况按钮先显示"思考中..." 但实际是普通 follow-up 延迟, 用户感受
 * 上无害 —— 加载状态不会让人困惑, 反而是个温和的高估。
 */
function isNextTurnLazyProject(
  plan: InterviewPlan,
  refId: string | null,
): boolean {
  if (!refId) return false;
  const roundIdx = plan.rounds.findIndex((r) =>
    r.questions.some((q) => q.question_id === refId),
  );
  if (roundIdx < 0) return false;
  const currentRound = plan.rounds[roundIdx];
  const last = currentRound.questions[currentRound.questions.length - 1];
  if (!last || last.question_id !== refId) return false;
  const nextRound = plan.rounds[roundIdx + 1];
  if (!nextRound || nextRound.stage !== "project") return false;
  return nextRound.questions.some((q) => q.lazy && !q.text);
}

export default function SessionPage({
  params,
}: {
  params: Promise<{ jobId: string; candidateId: string }>;
}) {
  const { jobId, candidateId } = use(params);
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "consent" });
  const [answer, setAnswer] = useState("");
  // Sprint 6-1: consent 门过了才 init; 媒体授权失败降级纯文字并提示
  const [consented, setConsented] = useState(false);
  const [mediaNotice, setMediaNotice] = useState<string | null>(null);
  const media = useCandidateMedia();
  const mediaState = media.state;

  // Sprint 6-2: TTS 播放。avModeRef 在 consent 时定型, 供 init effect 的闭包读
  // (mediaState 不能进 init deps, 否则媒体状态变化会重跑 init)。
  const avModeRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);

  // Sprint 6-3: avatar 三态状态机。talking=TTS 播报中, thinking=提交后空档,
  // idle=聆听。状态只在这个组件里推, InterviewerPanel 纯展示。
  const [avatarState, setAvatarState] = useState<AvatarState>("idle");

  /** 播放一段音频 blob, 自动停旧段 + 释放旧 object URL (防叠音/泄漏)。 */
  const playBlob = useCallback(
    (blob: Blob, opts?: { onStart?: () => void; onEnd?: () => void }) => {
      audioRef.current?.pause();
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      const url = URL.createObjectURL(blob);
      audioUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => opts?.onEnd?.();
      audio
        .play()
        .then(() => opts?.onStart?.())
        .catch(() => {
          /* 浏览器自动播放策略拦截等: 静默, 文字仍在 */
        });
    },
    [],
  );

  /** 拉当前 turn 的 TTS 音频播报; 成功切 talking, 播完回 idle; 失败静默退文字。 */
  const playPrompt = useCallback(
    async (sessionId: string, refId: string) => {
      if (!avModeRef.current) return;
      const blob = await api.fetchTurnAudio(sessionId, refId);
      if (!blob) {
        setAvatarState("idle");
        return;
      }
      playBlob(blob, {
        onStart: () => setAvatarState("talking"),
        onEnd: () => setAvatarState("idle"),
      });
    },
    [playBlob],
  );

  // Sprint 6-3: 过渡语音。进面试后预取一次 (每句 ~几十 KB), 提交时按已答
  // 题数轮换播放, 遮蔽 Assessor + lazy project gen 的 3-8s 思考空档。
  const fillerBlobsRef = useRef<Blob[]>([]);
  const prefetchFillers = useCallback(async (sessionId: string) => {
    if (!avModeRef.current || fillerBlobsRef.current.length > 0) return;
    const blobs = await Promise.all(
      Array.from({ length: FILLER_COUNT }, (_, i) =>
        api.fetchFillerAudio(sessionId, i),
      ),
    );
    fillerBlobsRef.current = blobs.filter((b): b is Blob => b !== null);
  }, []);

  /** 提交后播一句过渡语; avatar 停在 thinking (由 handleSubmit 设置)。 */
  const playFiller = useCallback(
    (turnIndex: number) => {
      if (!avModeRef.current) return;
      const pool = fillerBlobsRef.current;
      if (pool.length === 0) return;
      playBlob(pool[turnIndex % pool.length]);
    },
    [playBlob],
  );

  // Sprint 6-4: 语音作答。sttEnabled 由 /media/config 探测, rec 是录音三态。
  const [sttEnabled, setSttEnabled] = useState(false);
  const [rec, setRec] = useState<"off" | "recording" | "finalizing">("off");
  const [liveText, setLiveText] = useState("");
  const captureRef = useRef<SpeechCapture | null>(null);

  const teardownCapture = useCallback(() => {
    captureRef.current?.dispose();
    captureRef.current = null;
    setRec("off");
    setLiveText("");
  }, []);

  // 卸载 (含跳 done 页): 停播 + 释放 object URL + 拆录音管线
  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      captureRef.current?.dispose();
    };
  }, []);

  useEffect(() => {
    if (!consented) return;
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
        // Sprint 6-2: 首题 / 恢复的当前题播报; 6-3: 顺手预取过渡语音
        if (turn.ref_id) {
          playPrompt(turn.session_id, turn.ref_id);
        }
        prefetchFillers(turn.session_id);
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "error", message: errMessage(e) });
      }
    }

    init();
    return () => {
      cancelled = true;
    };
  }, [consented, jobId, candidateId, router, playPrompt, prefetchFillers]);

  // 会话终态 (过期/出错) 不再需要摄像头、播报和录音, 立刻释放免得红点常亮。
  // 只做资源释放不 setState (react-hooks/set-state-in-effect):
  // 终态下答题 UI 已不渲染, rec/liveText 留旧值无害。
  const stopMedia = media.stop;
  useEffect(() => {
    if (state.kind === "expired" || state.kind === "error") {
      stopMedia();
      audioRef.current?.pause();
      captureRef.current?.dispose();
      captureRef.current = null;
    }
  }, [state.kind, stopMedia]);

  async function handleConsent(withMedia: boolean) {
    if (withMedia) {
      const ok = await media.request();
      avModeRef.current = ok;
      if (ok) {
        // Sprint 6-4: 探测部署是否配了 STT, 决定麦克风入口显隐
        api
          .getMediaConfig()
          .then((c) => setSttEnabled(c.stt_enabled))
          .catch(() => {});
      } else {
        setMediaNotice("未获得摄像头/麦克风权限, 已切换为纯文字作答");
      }
    }
    setState({ kind: "starting" });
    setConsented(true);
  }

  // ---- Sprint 6-4: 录音 -> 转写 -> 落 textarea ----

  async function startRecording() {
    if (state.kind !== "answering" || mediaState.kind !== "granted") return;
    if (captureRef.current) return;
    const sc = new SpeechCapture();
    captureRef.current = sc;
    setRec("recording");
    setLiveText("");
    const sessionId = state.turn.session_id;
    const refId = state.turn.ref_id;
    try {
      await sc.start(mediaState.stream, transcribeWsUrl(sessionId), {
        onPartial: (t) => setLiveText(t),
        onFinal: (t) => appendTranscript(t, sessionId, refId),
        onDone: () => teardownCapture(),
        onError: () => {
          teardownCapture();
          setMediaNotice("语音转写暂不可用, 请打字作答");
        },
      });
    } catch {
      teardownCapture();
      setMediaNotice("语音转写暂不可用, 请打字作答");
    }
  }

  function stopRecording() {
    if (!captureRef.current) return;
    setRec("finalizing");
    captureRef.current.finish();
  }

  /** final 转写追加进答案 (换行分隔), 同步草稿; 候选人可继续编辑再提交。 */
  function appendTranscript(
    text: string,
    sessionId: string,
    refId: string | null,
  ) {
    const t = text.trim();
    setLiveText("");
    if (!t) return;
    setAnswer((prev) => {
      const next = prev ? `${prev}\n${t}` : t;
      if (refId) writeDraft(sessionId, refId, next);
      return next;
    });
  }

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
    // Sprint 6-3: 思考态 + 过渡语音, 遮蔽 Assessor / lazy gen 空档
    setAvatarState("thinking");
    playFiller(prev.answered_count);
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
      // Sprint 6-2: 新题 / 追问播报 (playPrompt 内部管 avatar talking/idle)
      if (next.ref_id) {
        playPrompt(next.session_id, next.ref_id);
      } else {
        setAvatarState("idle");
      }
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
      <div
        className={`w-full mt-8 sm:mt-12 mb-12 ${
          mediaState.kind === "granted" ? "max-w-3xl" : "max-w-2xl"
        }`}
      >
        {state.kind === "consent" && (
          <ConsentGate
            requesting={mediaState.kind === "requesting"}
            onAccept={() => handleConsent(true)}
            onTextOnly={() => handleConsent(false)}
          />
        )}

        {state.kind === "starting" && (
          <p className="text-zinc-500 text-center mt-20">进入面试中...</p>
        )}

        {(state.kind === "answering" || state.kind === "submitting") && (
          <>
            <ProgressHeader
              progress={state.progress}
              answered={state.answered_count}
            />

            {/* Sprint 6-1 三区布局: 面试官区 + 自拍 PiP (开摄像头时) */}
            {mediaState.kind === "granted" && (
              <div className="flex gap-3 mb-4">
                <div className="flex-1 min-w-0">
                  <InterviewerPanel state={avatarState} />
                </div>
                <div className="w-36 sm:w-44 shrink-0 self-end rounded-lg overflow-hidden bg-black aspect-video border border-zinc-200 dark:border-zinc-800">
                  <SelfView stream={mediaState.stream} />
                </div>
              </div>
            )}

            {mediaNotice && (
              <p className="mb-4 rounded-md bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-900 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                {mediaNotice}
              </p>
            )}

            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 sm:p-6 mb-4">
              <div className="flex items-center gap-2 mb-2">
                <p className="text-xs text-zinc-500">面试官</p>
                {/* 纯文字模式没有面试官区, AI 标识挂在题目卡片上 */}
                {mediaState.kind !== "granted" && <AiBadge />}
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

            {/* Sprint 6-4: 录音实时转写预览 (final 后并入 textarea) */}
            {rec !== "off" && (
              <div className="mb-4 rounded-md border border-red-200 dark:border-red-900 bg-white dark:bg-zinc-900 px-3 py-2">
                <p className="flex items-center gap-1.5 text-xs text-zinc-500 mb-1">
                  <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                  {rec === "recording" ? "正在聆听..." : "转写定稿中..."}
                </p>
                <p className="text-sm text-zinc-700 dark:text-zinc-300 whitespace-pre-line min-h-5">
                  {liveText || " "}
                </p>
              </div>
            )}

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
                <div className="flex items-center gap-2 shrink-0">
                  {/* Sprint 6-4: STT 配了 + 摄像头模式才显示麦克风入口 */}
                  {sttEnabled && mediaState.kind === "granted" && (
                    <button
                      type="button"
                      onClick={rec === "off" ? startRecording : stopRecording}
                      disabled={
                        state.kind === "submitting" || rec === "finalizing"
                      }
                      className="rounded-md border border-zinc-300 dark:border-zinc-700 px-4 py-2 text-sm font-medium text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-50"
                    >
                      {rec === "off"
                        ? "语音回答"
                        : rec === "recording"
                          ? "说完了"
                          : "转写中..."}
                    </button>
                  )}
                  <button
                    type="submit"
                    disabled={
                      state.kind === "submitting" ||
                      rec !== "off" ||
                      answer.trim().length < MIN_ANSWER_CHARS
                    }
                    className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-5 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
                  >
                    {state.kind === "submitting"
                      ? isNextTurnLazyProject(state.plan, state.turn.ref_id)
                        ? "分析中... (评估 + 准备项目题, 约 5-8 秒)"
                        : "分析中... (评估回答)"
                      : "提交回答"}
                  </button>
                </div>
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
