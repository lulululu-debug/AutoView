"use client";

/**
 * HR Dashboard 占位 (Sprint 5-3): 仅证明 layout + guard 链路通了。
 * Sprint 5-4 会接进职位列表 + 创建表单。
 */
export default function HrDashboardPage() {
  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-10">
      <h1 className="text-2xl font-semibold mb-2">HR Dashboard</h1>
      <p className="text-zinc-600 dark:text-zinc-400 text-sm mb-6">
        欢迎回来。Sprint 5-4 会把职位列表 / 候选人列表 / 报告 / 复核 都接进来。
      </p>
      <div className="rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 p-6 text-center text-sm text-zinc-500">
        待实现: GET /hr/jobs · POST /jobs · 候选人列表 · 报告与复核
      </div>
    </main>
  );
}
