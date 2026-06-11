"use client";

import { use, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, api } from "@/lib/api";

/**
 * Plan 轮询页:
 * - 进入时启动 2s 一次的 GET /candidates/{id}/plan 轮询
 * - 200 -> 跳 session 页 (Sprint 4-4b)
 * - 404 -> 继续轮询 (Planner 后台还在跑)
 * - 60s 超时 -> 提示重新上传, BG planner 可能失败了
 * - 其他错误 -> 直接显示错误 + 重新上传
 *
 * cancelledRef 守住组件卸载: 防止用户提早离开页面时还在 setTimeout 后台
 * 触发 setState (Strict mode 下会 console warn)。
 */

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 60000;
const REDIRECT_DELAY_MS = 500;

type State =
  | { kind: "pending"; elapsed_ms: number }
  | { kind: "ready" }
  | { kind: "timeout" }
  | { kind: "error"; message: string };

export default function WaitingPage({
  params,
}: {
  params: Promise<{ jobId: string; candidateId: string }>;
}) {
  const { jobId, candidateId } = use(params);
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "pending", elapsed_ms: 0 });
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    const startedAt = Date.now();

    async function pollOnce() {
      if (cancelledRef.current) return;
      const elapsed = Date.now() - startedAt;
      if (elapsed >= POLL_TIMEOUT_MS) {
        setState({ kind: "timeout" });
        return;
      }
      try {
        await api.getCandidatePlan(jobId, candidateId);
        if (cancelledRef.current) return;
        setState({ kind: "ready" });
        // 让"准备完成"文案有点时间显示一下再跳, UX 平滑
        setTimeout(() => {
          if (!cancelledRef.current) {
            router.push(`/interview/${jobId}/${candidateId}/session`);
          }
        }, REDIRECT_DELAY_MS);
      } catch (e: unknown) {
        if (cancelledRef.current) return;
        if (e instanceof ApiError && e.status === 404) {
          // 还没好, 继续等
          setState({ kind: "pending", elapsed_ms: elapsed });
          setTimeout(pollOnce, POLL_INTERVAL_MS);
          return;
        }
        const message =
          e instanceof ApiError
            ? `${e.status}: ${e.message}`
            : e instanceof Error
              ? e.message
              : String(e);
        setState({ kind: "error", message });
      }
    }

    pollOnce();
    return () => {
      cancelledRef.current = true;
    };
  }, [jobId, candidateId, router]);

  return (
    <main className="min-h-screen flex items-center justify-center bg-zinc-50 dark:bg-black p-6">
      <div className="w-full max-w-xl text-center">
        {state.kind === "pending" && (
          <PendingView elapsedSec={Math.floor(state.elapsed_ms / 1000)} />
        )}
        {state.kind === "ready" && <ReadyView />}
        {state.kind === "timeout" && (
          <FailureView
            title="准备超时"
            body={`Planner 在 ${POLL_TIMEOUT_MS / 1000} 秒内仍未完成。后台 LLM 调用可能失败了, 请重新上传简历再试一次。`}
            jobId={jobId}
            router={router}
          />
        )}
        {state.kind === "error" && (
          <FailureView
            title="出错了"
            body={state.message}
            jobId={jobId}
            router={router}
          />
        )}
      </div>
    </main>
  );
}

function PendingView({ elapsedSec }: { elapsedSec: number }) {
  return (
    <>
      <h1 className="text-2xl font-semibold mb-3">面试准备中</h1>
      <p className="text-zinc-600 dark:text-zinc-400 mb-8">
        系统正在根据 JD 和你的简历生成面试计划...
      </p>
      <div className="inline-block w-12 h-12 mb-6">
        <div className="w-full h-full rounded-full border-2 border-zinc-300 border-t-zinc-700 dark:border-zinc-700 dark:border-t-zinc-100 animate-spin" />
      </div>
      <p className="text-xs text-zinc-400">已等待 {elapsedSec} 秒</p>
    </>
  );
}

function ReadyView() {
  return (
    <>
      <div className="inline-flex w-12 h-12 mb-4 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-950">
        <span className="text-emerald-600 dark:text-emerald-400 text-2xl">
          ✓
        </span>
      </div>
      <h1 className="text-2xl font-semibold mb-2">准备完成</h1>
      <p className="text-zinc-600 dark:text-zinc-400">正在进入面试...</p>
    </>
  );
}

function FailureView({
  title,
  body,
  jobId,
  router,
}: {
  title: string;
  body: string;
  jobId: string;
  router: ReturnType<typeof useRouter>;
}) {
  return (
    <>
      <h1 className="text-2xl font-semibold mb-3 text-red-600 dark:text-red-400">
        {title}
      </h1>
      <p className="text-zinc-600 dark:text-zinc-400 mb-6 font-mono text-sm">
        {body}
      </p>
      <button
        onClick={() => router.push(`/interview/${jobId}`)}
        className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-4 py-2 text-sm font-medium hover:opacity-90"
      >
        重新上传简历
      </button>
    </>
  );
}
