"use client";

import { useEffect } from "react";

/**
 * 全局错误边界 (Next.js 约定: app/error.tsx 自动捕获 client 抛错)。
 * useEffect 走 console.error, dev 期看堆栈; prod 可换接监控 SDK。
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[GlobalError]", error);
  }, [error]);

  return (
    <main className="min-h-screen flex items-center justify-center bg-zinc-50 dark:bg-black p-6">
      <div className="w-full max-w-xl text-center">
        <h1 className="text-2xl font-semibold mb-3 text-red-600 dark:text-red-400">
          出错了
        </h1>
        <p className="text-zinc-600 dark:text-zinc-400 mb-6">
          页面渲染异常。如果反复出现请联系招聘方。
        </p>
        {error.digest && (
          <p className="text-xs text-zinc-400 font-mono mb-4">
            错误编号: {error.digest}
          </p>
        )}
        <button
          onClick={reset}
          className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-4 py-2 text-sm font-medium hover:opacity-90"
        >
          重试
        </button>
      </div>
    </main>
  );
}
