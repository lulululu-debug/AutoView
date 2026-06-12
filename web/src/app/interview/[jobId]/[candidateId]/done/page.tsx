"use client";

import { use, useEffect } from "react";

import { api } from "@/lib/api";

/**
 * 面试完成页:
 * - 显示感谢 + 提醒结果由 HR 查阅
 * - 不展示报告内容: 合规(ARCHITECTURE.md §7) 候选人不应看到自己的 AI 评估,
 *   即便在网络层面也不应让报告 JSON 经过候选人浏览器
 * - Sprint 5-3: 触发 POST /interviews/{id}/finalize 让后端归档, 但只回 204
 *   不带 report 数据 -> HR 端 list_candidates 才能尽快看到 completed 状态
 * - 清掉 localStorage 的 session_id, 用户按回退键不再误进 session 页
 */
export default function DonePage({
  params,
}: {
  params: Promise<{ jobId: string; candidateId: string }>;
}) {
  const { candidateId } = use(params);

  useEffect(() => {
    const key = `interview_session_${candidateId}`;
    let sid: string | null = null;
    try {
      sid = localStorage.getItem(key);
    } catch {
      /* 静默 */
    }

    if (sid) {
      // fire-and-forget; 已经 finalize 过 (404) 或网络故障都不阻塞感谢页
      api.finalizeInterview(sid).catch(() => {
        /* 静默 */
      });
    }

    try {
      localStorage.removeItem(key);
    } catch {
      /* 静默 */
    }
  }, [candidateId]);

  return (
    <main className="min-h-screen flex items-center justify-center bg-zinc-50 dark:bg-black p-6">
      <div className="w-full max-w-xl text-center">
        <div className="inline-flex w-16 h-16 mb-6 items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-950">
          <span className="text-emerald-600 dark:text-emerald-400 text-3xl">
            ✓
          </span>
        </div>
        <h1 className="text-2xl font-semibold mb-3">面试已完成</h1>
        <p className="text-zinc-600 dark:text-zinc-400 mb-2">
          感谢你的参与。
        </p>
        <p className="text-zinc-600 dark:text-zinc-400">
          面试结果将由招聘方查阅, 不会在此直接展示。
        </p>
      </div>
    </main>
  );
}
