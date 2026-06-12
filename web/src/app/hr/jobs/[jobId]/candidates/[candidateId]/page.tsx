"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  api,
  type CandidateWithStatus,
  type DimensionOverride,
  type DimensionScore,
  type EvaluationReport,
  type InterviewPlan,
  type JobContext,
  type ReviewDecision,
  type ReviewRecord,
} from "@/lib/api";

/**
 * HR 候选人详情 + 报告 + 复核 UI - Sprint 5 收官。
 *
 * 流程:
 * - 并行拉 candidate + job (必拉)
 * - 若 status >= completed, 再并行拉 report + review + plan
 *   plan 用来把 competency_id 翻成可读的维度名 + 描述
 *
 * 报告合规分区 (ARCHITECTURE.md §7):
 * - content_scores 主区: 内容维度, 进总分
 * - performance_observations 副区: 软信号, "仅参考, 不进总分"
 * - rag_context_chunk_ids: 评估时召回的 JD/公司资料 chunks (审计用)
 *
 * 复核表单:
 * - 每个 content score 一行 "覆盖" 输入: 留空 = 不覆盖
 * - decision 三选一 + comments
 * - 已复核状态回显, 可重新提交 (MVP 覆盖语义)
 */

type FullData = {
  candidate: CandidateWithStatus;
  job: JobContext;
  // 仅 completed/reviewed 时存在:
  report: EvaluationReport | null;
  review: ReviewRecord | null;
  plan: InterviewPlan | null;
};

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; data: FullData }
  | { kind: "error"; message: string };

export default function CandidateDetailPage({
  params,
}: {
  params: Promise<{ jobId: string; candidateId: string }>;
}) {
  const { jobId, candidateId } = use(params);
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  async function loadAll() {
    setState({ kind: "loading" });
    try {
      const [candidate, job] = await Promise.all([
        api.getHrCandidate(jobId, candidateId),
        api.getJob(jobId),
      ]);
      let report: EvaluationReport | null = null;
      let review: ReviewRecord | null = null;
      let plan: InterviewPlan | null = null;
      if (
        (candidate.status === "completed" || candidate.status === "reviewed") &&
        candidate.report_id
      ) {
        [report, review, plan] = await Promise.all([
          api.getReport(candidate.report_id),
          api.getReview(candidate.report_id),
          // plan 拉失败不致命: 没有 plan 时, competency 名退到 id 显示
          api
            .getCandidatePlan(jobId, candidateId)
            .catch(() => null as InterviewPlan | null),
        ]);
      }
      setState({
        kind: "ok",
        data: { candidate, job, report, review, plan },
      });
    } catch (e) {
      setState({ kind: "error", message: errMessage(e) });
    }
  }

  useEffect(() => {
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, candidateId]);

  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
      <Link
        href={`/hr/jobs/${jobId}`}
        className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        ← 返回候选人列表
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
        <Detail
          data={state.data}
          onReviewSubmitted={(newReview) =>
            setState({
              kind: "ok",
              data: {
                ...state.data,
                review: newReview,
                candidate: {
                  ...state.data.candidate,
                  status: "reviewed",
                  review_decision: newReview.decision,
                },
              },
            })
          }
        />
      )}
    </main>
  );
}

function Detail({
  data,
  onReviewSubmitted,
}: {
  data: FullData;
  onReviewSubmitted: (review: ReviewRecord) => void;
}) {
  const { candidate, job, report, review, plan } = data;

  // 把 plan 里的 competency 信息编成 id -> {name, description, weight} 映射
  const competencyById = useMemo(() => {
    const m = new Map<
      string,
      { name: string; description: string; weight: number }
    >();
    if (!plan) return m;
    for (const round of plan.rounds) {
      for (const c of round.competencies) {
        m.set(c.competency_id, {
          name: c.name,
          description: c.description,
          weight: c.weight,
        });
      }
    }
    return m;
  }, [plan]);

  return (
    <div className="mt-4">
      <header className="mb-6">
        <p className="text-xs text-zinc-500 mb-1">
          {job.title} · {job.role_family}
        </p>
        <h1 className="text-2xl font-semibold mb-2">
          候选人 {candidate.candidate_id.slice(0, 12)}...
        </h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed">
          {candidate.resume_excerpt}
        </p>
      </header>

      {candidate.status === "plan_pending" && (
        <NoticeCard
          tone="zinc"
          title="等待面试计划生成"
          body="后台 Planner 还在跑 (或失败), 等候选人有 plan 后再来。"
        />
      )}

      {candidate.status === "ready" && (
        <NoticeCard
          tone="amber"
          title="面试未完成"
          body="候选人尚未答完面试, 或答完但没触发归档。Sprint 4 之后的版本会在候选人 done 页自动归档, 等几秒刷新即可。"
        />
      )}

      {(candidate.status === "completed" || candidate.status === "reviewed") &&
        report && (
          <>
            <ReportView report={report} competencyById={competencyById} />
            <ReviewSection
              report={report}
              existingReview={review}
              competencyById={competencyById}
              onSubmitted={onReviewSubmitted}
            />
          </>
        )}
    </div>
  );
}

// ---------- Report 区 ----------

function ReportView({
  report,
  competencyById,
}: {
  report: EvaluationReport;
  competencyById: Map<string, { name: string; description: string; weight: number }>;
}) {
  const [showRag, setShowRag] = useState(false);
  return (
    <section className="mb-8">
      <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-6 mb-4">
        <div className="flex items-baseline gap-4">
          <div>
            <p className="text-xs text-zinc-500 uppercase tracking-wide mb-1">
              综合得分
            </p>
            <p className="text-4xl font-semibold tabular-nums">
              {report.overall.toFixed(1)}
              <span className="text-base text-zinc-400 font-normal"> / 100</span>
            </p>
          </div>
          {report.needs_human_review && (
            <span className="text-xs px-2 py-0.5 rounded bg-amber-100 dark:bg-amber-950 text-amber-700 dark:text-amber-300">
              建议人工复核
            </span>
          )}
        </div>
        <div className="mt-4 text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed whitespace-pre-line">
          {report.summary}
        </div>
      </div>

      <h2 className="text-sm font-medium text-zinc-500 uppercase tracking-wide mb-3">
        内容维度
      </h2>
      <div className="space-y-3 mb-6">
        {report.content_scores.map((s) => (
          <ContentScoreRow
            key={s.competency_id}
            score={s}
            meta={competencyById.get(s.competency_id)}
          />
        ))}
      </div>

      {report.performance_observations.length > 0 && (
        <>
          <h2 className="text-sm font-medium text-zinc-500 uppercase tracking-wide mb-1">
            表现维度
          </h2>
          <p className="text-xs text-zinc-500 mb-3">
            软信号, 仅作参考, 不进总分 (ARCHITECTURE.md §7)
          </p>
          <div className="space-y-2 mb-6">
            {report.performance_observations.map((o, i) => (
              <PerfObservationRow key={i} obs={o} />
            ))}
          </div>
        </>
      )}

      {report.rag_context_chunk_ids.length > 0 && (
        <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-900/50 p-3 mb-6">
          <button
            onClick={() => setShowRag((v) => !v)}
            className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 w-full text-left flex items-center justify-between"
          >
            <span>
              评估上下文 ({report.rag_context_chunk_ids.length} 个 RAG 片段)
            </span>
            <span>{showRag ? "收起 ↑" : "展开 ↓"}</span>
          </button>
          {showRag && (
            <ul className="mt-2 space-y-1 font-mono text-xs text-zinc-500">
              {report.rag_context_chunk_ids.map((cid) => (
                <li key={cid}>{cid}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

function ContentScoreRow({
  score,
  meta,
}: {
  score: DimensionScore;
  meta?: { name: string; description: string; weight: number };
}) {
  return (
    <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <div className="flex items-baseline justify-between gap-3 mb-2">
        <div>
          <p className="font-medium">
            {meta?.name ?? score.competency_id.slice(0, 8)}
          </p>
          {meta?.description && (
            <p className="text-xs text-zinc-500 mt-0.5">{meta.description}</p>
          )}
        </div>
        <div className="text-right shrink-0">
          <p className="text-2xl font-semibold tabular-nums">
            {score.score.toFixed(1)}
          </p>
          {meta && (
            <p className="text-xs text-zinc-400">权重 ×{meta.weight}</p>
          )}
        </div>
      </div>
      {score.evidence.length > 0 && (
        <ul className="mt-3 text-xs text-zinc-600 dark:text-zinc-400 space-y-1">
          {score.evidence.map((ev, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-zinc-300 dark:text-zinc-700">·</span>
              <span className="flex-1">{ev}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PerfObservationRow({
  obs,
}: {
  obs: { kind: string; observation: string; confidence: number; note: string };
}) {
  return (
    <div className="rounded-md border border-zinc-200 dark:border-zinc-800 p-3 text-sm bg-white dark:bg-zinc-900">
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs uppercase tracking-wide text-zinc-500">
          {obs.kind}
        </span>
        <span className="text-xs text-zinc-400">
          置信度 {(obs.confidence * 100).toFixed(0)}%
        </span>
      </div>
      <p className="text-zinc-700 dark:text-zinc-300">{obs.observation}</p>
      {obs.note && (
        <p className="text-xs text-zinc-500 mt-1 italic">{obs.note}</p>
      )}
    </div>
  );
}

// ---------- Review 区 ----------

type OverrideRow = {
  competency_id: string;
  score: string; // 留空 = 不覆盖
  note: string;
};

type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string };

function ReviewSection({
  report,
  existingReview,
  competencyById,
  onSubmitted,
}: {
  report: EvaluationReport;
  existingReview: ReviewRecord | null;
  competencyById: Map<string, { name: string; description: string; weight: number }>;
  onSubmitted: (r: ReviewRecord) => void;
}) {
  const [comments, setComments] = useState(existingReview?.comments ?? "");
  const [decision, setDecision] = useState<ReviewDecision | "">(
    existingReview?.decision ?? "",
  );
  const [overrides, setOverrides] = useState<OverrideRow[]>(() =>
    report.content_scores.map((s) => {
      const ex = existingReview?.dimension_overrides.find(
        (o) => o.competency_id === s.competency_id,
      );
      return {
        competency_id: s.competency_id,
        score: ex ? String(ex.score) : "",
        note: ex?.note ?? "",
      };
    }),
  );
  const [state, setState] = useState<SubmitState>({ kind: "idle" });

  function updateOverride(idx: number, patch: Partial<OverrideRow>) {
    setOverrides((rs) => rs.map((r, i) => (i === idx ? { ...r, ...patch } : r)));
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!decision) {
      setState({ kind: "error", message: "请选择最终建议" });
      return;
    }
    const dimension_overrides: DimensionOverride[] = [];
    for (const row of overrides) {
      const trimmed = row.score.trim();
      if (!trimmed && !row.note.trim()) continue; // 全空 = 没覆盖
      const score = Number(trimmed);
      if (!trimmed || Number.isNaN(score) || score < 0 || score > 100) {
        setState({
          kind: "error",
          message:
            "覆盖分数必须是 0~100 的数字; 只想加备注也要先填上 AI 原分。",
        });
        return;
      }
      dimension_overrides.push({
        competency_id: row.competency_id,
        score,
        note: row.note.trim(),
      });
    }

    setState({ kind: "submitting" });
    try {
      const submitted = await api.submitReview(report.report_id, {
        comments: comments.trim(),
        dimension_overrides,
        decision,
      });
      setState({ kind: "idle" });
      onSubmitted(submitted);
    } catch (e) {
      setState({ kind: "error", message: errMessage(e) });
    }
  }

  return (
    <section className="mb-8">
      <h2 className="text-sm font-medium text-zinc-500 uppercase tracking-wide mb-3">
        {existingReview ? "复核 (已提交, 可重新覆盖)" : "提交复核"}
      </h2>
      {existingReview && (
        <p className="text-xs text-zinc-500 mb-3">
          上次复核: {new Date(existingReview.reviewed_at).toLocaleString("zh-CN")}
        </p>
      )}

      <form
        onSubmit={handleSubmit}
        className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 space-y-5"
      >
        <div>
          <label className="block text-xs text-zinc-500 mb-1">
            综合备注 (HR 视角的判断, 不展示给候选人)
          </label>
          <textarea
            value={comments}
            onChange={(e) => setComments(e.target.value)}
            disabled={state.kind === "submitting"}
            rows={4}
            placeholder="例如: 技术深度突出, 但对沟通场景的描述偏抽象..."
            className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60"
          />
        </div>

        <div>
          <p className="text-xs text-zinc-500 mb-2">
            维度评分覆盖 (留空 = 沿用 AI 评分)
          </p>
          <div className="space-y-3">
            {overrides.map((row, idx) => {
              const score = report.content_scores.find(
                (s) => s.competency_id === row.competency_id,
              );
              const meta = competencyById.get(row.competency_id);
              return (
                <div
                  key={row.competency_id}
                  className="rounded-md border border-zinc-200 dark:border-zinc-800 p-3"
                >
                  <div className="flex items-baseline justify-between mb-2">
                    <div className="text-sm font-medium">
                      {meta?.name ?? row.competency_id.slice(0, 8)}
                    </div>
                    <div className="text-xs text-zinc-500">
                      AI 给分:{" "}
                      <span className="tabular-nums font-mono text-zinc-700 dark:text-zinc-300">
                        {score?.score.toFixed(1) ?? "—"}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      max="100"
                      placeholder="覆盖分"
                      value={row.score}
                      onChange={(e) =>
                        updateOverride(idx, { score: e.target.value })
                      }
                      disabled={state.kind === "submitting"}
                      className="w-24 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60 tabular-nums"
                    />
                    <input
                      type="text"
                      placeholder="备注 (理由 / 加权解释)"
                      value={row.note}
                      onChange={(e) =>
                        updateOverride(idx, { note: e.target.value })
                      }
                      disabled={state.kind === "submitting"}
                      className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60"
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div>
          <p className="text-xs text-zinc-500 mb-2">最终建议</p>
          <div className="flex items-center gap-2 flex-wrap">
            <DecisionRadio
              value="recommend"
              label="推荐"
              current={decision}
              onChange={setDecision}
              klass="bg-emerald-50 dark:bg-emerald-950/40 border-emerald-300 dark:border-emerald-800 text-emerald-700 dark:text-emerald-300"
              disabled={state.kind === "submitting"}
            />
            <DecisionRadio
              value="borderline"
              label="边界"
              current={decision}
              onChange={setDecision}
              klass="bg-amber-50 dark:bg-amber-950/40 border-amber-300 dark:border-amber-800 text-amber-700 dark:text-amber-300"
              disabled={state.kind === "submitting"}
            />
            <DecisionRadio
              value="reject"
              label="拒绝"
              current={decision}
              onChange={setDecision}
              klass="bg-red-50 dark:bg-red-950/40 border-red-300 dark:border-red-800 text-red-700 dark:text-red-300"
              disabled={state.kind === "submitting"}
            />
          </div>
        </div>

        {state.kind === "error" && (
          <div className="rounded-md bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 p-2 text-sm text-red-700 dark:text-red-300">
            {state.message}
          </div>
        )}

        <button
          type="submit"
          disabled={state.kind === "submitting"}
          className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {state.kind === "submitting"
            ? "提交中..."
            : existingReview
              ? "覆盖复核"
              : "提交复核"}
        </button>
      </form>
    </section>
  );
}

function DecisionRadio({
  value,
  label,
  current,
  onChange,
  klass,
  disabled,
}: {
  value: ReviewDecision;
  label: string;
  current: ReviewDecision | "";
  onChange: (v: ReviewDecision) => void;
  klass: string;
  disabled: boolean;
}) {
  const selected = current === value;
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(value)}
      className={`px-3 py-1 rounded border text-sm transition disabled:opacity-50 ${
        selected
          ? klass
          : "border-zinc-300 dark:border-zinc-700 text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
      }`}
    >
      {label}
    </button>
  );
}

// ---------- 通用 ----------

function NoticeCard({
  tone,
  title,
  body,
}: {
  tone: "zinc" | "amber";
  title: string;
  body: string;
}) {
  const klass =
    tone === "amber"
      ? "bg-amber-50 dark:bg-amber-950/40 border-amber-200 dark:border-amber-900 text-amber-800 dark:text-amber-300"
      : "bg-zinc-100 dark:bg-zinc-800/40 border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300";
  return (
    <div className={`rounded-lg border p-4 ${klass}`}>
      <p className="font-medium text-sm mb-1">{title}</p>
      <p className="text-sm">{body}</p>
    </div>
  );
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
