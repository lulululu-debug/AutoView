"use client";

import { useEffect, useState } from "react";

import { API_BASE, ApiError, api, type Health } from "@/lib/api";

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; data: Health }
  | { kind: "error"; message: string };

export default function Home() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });

  useEffect(() => {
    api
      .health()
      .then((data) => setHealth({ kind: "ok", data }))
      .catch((e) => {
        const message =
          e instanceof ApiError
            ? `${e.status}: ${e.message}`
            : e instanceof Error
              ? e.message
              : String(e);
        setHealth({ kind: "error", message });
      });
  }, []);

  return (
    <main className="min-h-screen flex items-center justify-center bg-zinc-50 dark:bg-black p-8">
      <div className="w-full max-w-xl">
        <h1 className="text-3xl font-semibold tracking-tight mb-2">
          AI Interview Platform
        </h1>
        <p className="text-zinc-600 dark:text-zinc-400 mb-8">
          多 agent 的 AI 面试基础设施 · 候选人端开发中 (Sprint 4)
        </p>

        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 p-6 bg-white dark:bg-zinc-900">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
              后端 API 状态
            </h2>
            <code className="text-xs text-zinc-400">{API_BASE}</code>
          </div>

          {health.kind === "loading" && (
            <p className="text-zinc-500">检查中...</p>
          )}

          {health.kind === "ok" && (
            <p className="text-emerald-600 dark:text-emerald-400">
              <span className="font-medium">✓ 正常</span>
              <span className="text-zinc-500 dark:text-zinc-400 ml-2">
                {health.data.service} v{health.data.version}
              </span>
            </p>
          )}

          {health.kind === "error" && (
            <div className="text-red-600 dark:text-red-400">
              <p className="font-medium">✗ 连不上 API</p>
              <p className="text-sm mt-1 text-red-500 dark:text-red-300 font-mono">
                {health.message}
              </p>
              <p className="text-xs mt-3 text-zinc-500 dark:text-zinc-400">
                确认后端在 <code className="font-mono">{API_BASE}</code> 上跑了:
                <br />
                <code className="font-mono">
                  uvicorn api.main:app --reload
                </code>
              </p>
            </div>
          )}
        </div>

        <p className="text-xs text-zinc-400 mt-8">
          下一步: Resume 上传页 / 面试 Q&amp;A 界面
        </p>
      </div>
    </main>
  );
}
