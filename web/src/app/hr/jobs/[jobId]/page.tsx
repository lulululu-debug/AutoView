"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";

import {
  ApiError,
  api,
  type CandidateStatus,
  type CandidateWithStatus,
  type JobContext,
} from "@/lib/api";

/**
 * HR 单个 job 的候选人列表页:
 * - 顶部展示 job 基本信息 (从 GET /jobs/{id} 拉)
 * - 列表展示候选人 + 状态徽章 (GET /hr/jobs/{id}/candidates)
 * - 每行 Resume 200 字预览, 进入详情走 Sprint 5-5 (报告 + 复核 UI)
 */

type State =
  | { kind: "loading" }
  | { kind: "ok"; job: JobContext; candidates: CandidateWithStatus[] }
  | { kind: "error"; message: string };

export default function HrJobPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = use(params);
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [job, candidates] = await Promise.all([
          api.getJob(jobId),
          api.listCandidates(jobId),
        ]);
        if (cancelled) return;
        setState({ kind: "ok", job, candidates });
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "error", message: errMessage(e) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
      <Link
        href="/hr"
        className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        ← 返回职位列表
      </Link>

      {state.kind === "loading" && (
        <p className="text-sm text-zinc-500 mt-8">加载中...</p>
      )}

      {state.kind === "error" && (
        <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/40 p-4 text-sm text-red-700 dark:text-red-300 mt-6">
          加载失败: {state.message}
        </div>
      )}

      {state.kind === "ok" && (
        <>
          <header className="mt-4 mb-8">
            <h1 className="text-2xl font-semibold mb-1">{state.job.title}</h1>
            <p className="text-xs text-zinc-500 mb-3 font-mono">
              {state.job.role_family}
            </p>
            <p className="text-sm text-zinc-600 dark:text-zinc-400 whitespace-pre-line leading-relaxed">
              {state.job.jd}
            </p>
            {state.job.requirements.length > 0 && (
              <ul className="text-sm text-zinc-600 dark:text-zinc-400 list-disc list-inside mt-3 space-y-1">
                {state.job.requirements.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            )}
            <div className="mt-5 rounded-md bg-zinc-100 dark:bg-zinc-900 p-3 text-xs text-zinc-500 font-mono break-all">
              <div className="mb-1 text-zinc-400">候选人邀请链接(本机 dev):</div>
              http://localhost:3000/interview/{state.job.job_id}
            </div>
          </header>

          <section>
            <h2 className="text-sm font-medium text-zinc-500 uppercase tracking-wide mb-3">
              候选人 ({state.candidates.length})
            </h2>
            <CandidateList list={state.candidates} jobId={jobId} />
          </section>
        </>
      )}
    </main>
  );
}

function CandidateList({
  list,
  jobId,
}: {
  list: CandidateWithStatus[];
  jobId: string;
}) {
  if (list.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 p-8 text-center text-sm text-zinc-500">
        还没有候选人, 把邀请链接发出去就有了。
      </div>
    );
  }
  return (
    <ul className="space-y-2">
      {list.map((c) => (
        <li key={c.candidate_id}>
          <Link
            href={`/hr/jobs/${jobId}/candidates/${c.candidate_id}`}
            className="block rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4 hover:border-zinc-400 dark:hover:border-zinc-600 transition"
          >
            <div className="flex items-start justify-between gap-3 mb-2">
              <div className="flex-1 min-w-0">
                <p className="text-xs text-zinc-400 font-mono mb-1">
                  {c.candidate_id.slice(0, 12)}...
                </p>
                <p className="text-sm text-zinc-700 dark:text-zinc-300 line-clamp-2">
                  {c.resume_excerpt}
                </p>
              </div>
              <StatusBadge status={c.status} decision={c.review_decision} />
            </div>
            <div className="flex items-center gap-3 text-xs text-zinc-400">
              <span>{formatDate(c.created_at)}</span>
              {c.report_id && (
                <>
                  <span>·</span>
                  <span className="font-mono">
                    报告 {c.report_id.slice(0, 8)}...
                  </span>
                </>
              )}
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}

const STATUS_META: Record<
  CandidateStatus,
  { label: string; klass: string }
> = {
  plan_pending: {
    label: "等待计划",
    klass:
      "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300",
  },
  ready: {
    label: "可面试 / 进行中",
    klass:
      "bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-300",
  },
  completed: {
    label: "待复核",
    klass:
      "bg-blue-100 dark:bg-blue-950 text-blue-700 dark:text-blue-300",
  },
  reviewed: {
    label: "已复核",
    klass:
      "bg-emerald-100 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-300",
  },
};

const DECISION_LABEL: Record<string, string> = {
  recommend: "推荐",
  borderline: "边界",
  reject: "拒绝",
};

function StatusBadge({
  status,
  decision,
}: {
  status: CandidateStatus;
  decision: string | null;
}) {
  const meta = STATUS_META[status];
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <span
        className={`text-xs px-2 py-0.5 rounded uppercase tracking-wide ${meta.klass}`}
      >
        {meta.label}
      </span>
      {status === "reviewed" && decision && (
        <span className="text-xs px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300">
          {DECISION_LABEL[decision] ?? decision}
        </span>
      )}
    </div>
  );
}

function formatDate(s: string): string {
  // server 返 ISO; 浏览器本地化展示
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
