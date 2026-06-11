"use client";

import { use } from "react";

/**
 * 占位页 (Sprint 4-3 留口子, Sprint 4-4 实现):
 * - 轮询 GET /jobs/{jobId}/candidates/{candidateId}/plan
 * - plan 就绪 -> 显示 "开始面试" 按钮, 自动 / 手动跳到 Q&A 页
 * - plan 失败 (轮询超过 N 秒还 404) -> 提示重新上传
 *
 * 当前: 仅显示候选人 id, 让 4-3 的"上传后跳转"链路有落地点。
 */
export default function WaitingPage({
  params,
}: {
  params: Promise<{ jobId: string; candidateId: string }>;
}) {
  const { jobId, candidateId } = use(params);
  return (
    <main className="min-h-screen flex items-center justify-center bg-zinc-50 dark:bg-black p-8">
      <div className="w-full max-w-xl text-center">
        <h1 className="text-2xl font-semibold mb-3">面试准备中</h1>
        <p className="text-zinc-600 dark:text-zinc-400 mb-8">
          系统正在根据 JD 和你的简历生成面试计划...
        </p>

        <div className="inline-block w-12 h-12 mb-6">
          <div className="w-full h-full rounded-full border-2 border-zinc-300 border-t-zinc-700 dark:border-zinc-700 dark:border-t-zinc-100 animate-spin" />
        </div>

        <p className="text-xs text-zinc-400 mb-2">
          (Sprint 4-4 会接 plan 轮询 + 自动跳转)
        </p>
        <p className="text-xs text-zinc-300 font-mono">
          job_id={jobId.slice(0, 10)}... · candidate_id={candidateId.slice(0, 10)}
          ...
        </p>
      </div>
    </main>
  );
}
