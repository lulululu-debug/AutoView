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
  type InterviewSessionDetail,
  type JobContext,
  type PlanTrace,
  type ResumeChunk,
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
  // Sprint 5.7: 含 assessments / answers / intro_text, 拉得到才有
  session: InterviewSessionDetail | null;
  // Sprint E: resume 切片原文, 出题过程视图对照 project 题溯源用
  resumeChunks: ResumeChunk[];
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
      let session: InterviewSessionDetail | null = null;
      // Sprint 5.5: plan 单独拉 (plan_pending 状态下 plan 未生成会 404, 静默吞);
      // 用于 HR 看面试 stage 视图 + report 里 competency 名映射。
      // Sprint E: 换 HR 端接口, 多返 trace (出题过程审计)。
      const plan: InterviewPlan | null = await api
        .getHrPlan(jobId, candidateId)
        .catch(() => null as InterviewPlan | null);
      // Sprint E: resume 切片 (trace 视图对照 project 题溯源); 失败静默空
      const resumeChunks: ResumeChunk[] = await api
        .getResumeChunks(candidateId)
        .catch(() => [] as ResumeChunk[]);
      if (
        (candidate.status === "completed" || candidate.status === "reviewed") &&
        candidate.report_id
      ) {
        [report, review] = await Promise.all([
          api.getReport(candidate.report_id),
          api.getReview(candidate.report_id),
        ]);
      }
      // Sprint 5.7: 拉 session (含 assessments) 让 AssessmentView 渲染面试过程;
      // session_id 来自 candidate 列表, 没 session (面试未启动) 时 null 跳过。
      if (candidate.session_id) {
        session = await api
          .getHrSession(candidate.session_id)
          .catch(() => null as InterviewSessionDetail | null);
      }
      setState({
        kind: "ok",
        data: { candidate, job, report, review, plan, session, resumeChunks },
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
  const { candidate, job, report, review, plan, session, resumeChunks } = data;
  // 开发者预览: lazy project 题的内存 resolve 结果 (后端不落库)。
  // 非 null 时 StageView 换用预览 plan; 只影响本页展示, 不影响正式面试。
  const [previewPlan, setPreviewPlan] = useState<InterviewPlan | null>(null);

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

      {plan && (
        <StageView
          plan={previewPlan ?? plan}
          previewActive={previewPlan !== null}
          onPreview={
            hasUnresolvedLazy(plan) && !previewPlan
              ? async () => {
                  const resolved = await api.getPlanPreview(
                    candidate.job_id,
                    candidate.candidate_id,
                  );
                  setPreviewPlan(resolved);
                }
              : undefined
          }
        />
      )}

      {plan?.trace && (
        <PlanTraceView
          trace={plan.trace}
          plan={plan}
          resumeChunks={resumeChunks}
        />
      )}

      {session && plan && (
        <AssessmentView session={session} plan={plan} />
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

// ---------- 面试阶段视图 (Sprint 5.5 task 5) ----------

const STAGE_LABEL: Record<string, string> = {
  self_intro: "自我介绍",
  knowledge: "基础知识",
  project: "项目深挖",
  scenario: "场景题",
};

const STAGE_BADGE_COLOR: Record<string, string> = {
  self_intro:
    "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  knowledge:
    "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300",
  project:
    "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
  scenario:
    "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
};

/**
 * 给一道题计算来源标签。每类 category 各自的溯源字段:
 * - knowledge / scenario: source_question_id 指向 SeedQuestion 题库行
 * - project_experience:   source_chunk_ids 指向 Resume 切片
 * - self_intro:           Planner 固定模板, 无溯源
 */
function describeSource(q: {
  category: string;
  source_question_id: string | null;
  source_chunk_ids: string[];
  lazy: boolean;
  text: string;
}): { label: string; tone: "muted" | "info" } {
  if (q.category === "self_intro") {
    return { label: "固定模板", tone: "muted" };
  }
  if (q.category === "knowledge" || q.category === "scenario") {
    if (q.source_question_id) {
      return {
        label: `题库 ${q.source_question_id.slice(0, 10)}`,
        tone: "info",
      };
    }
    return { label: "LLM 生成 (无题库匹配)", tone: "muted" };
  }
  if (q.category === "project_experience") {
    if (q.source_chunk_ids.length > 0) {
      return {
        label: `Resume 切片 ×${q.source_chunk_ids.length}`,
        tone: "info",
      };
    }
    if (q.lazy && !q.text) {
      return { label: "待懒生成", tone: "muted" };
    }
    return { label: "Resume 全文 fallback", tone: "muted" };
  }
  return { label: q.category, tone: "muted" };
}

function hasUnresolvedLazy(plan: InterviewPlan): boolean {
  return plan.rounds.some((r) => r.questions.some((q) => q.lazy && !q.text));
}

function StageView({
  plan,
  previewActive,
  onPreview,
}: {
  plan: InterviewPlan;
  previewActive?: boolean;
  onPreview?: () => Promise<void>;
}) {
  const [previewState, setPreviewState] = useState<
    "idle" | "loading" | "error"
  >("idle");
  const [previewError, setPreviewError] = useState("");

  async function handlePreview() {
    if (!onPreview) return;
    setPreviewState("loading");
    try {
      await onPreview();
      setPreviewState("idle");
    } catch (e) {
      setPreviewError(errMessage(e));
      setPreviewState("error");
    }
  }

  return (
    <section className="mb-8">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-zinc-500 uppercase tracking-wide">
          面试阶段 ({plan.rounds.length} 个 stage,{" "}
          {plan.rounds.reduce((n, r) => n + r.questions.length, 0)} 道题)
        </h2>
        {onPreview && (
          <button
            onClick={handlePreview}
            disabled={previewState === "loading"}
            className="text-xs px-2 py-1 rounded border border-zinc-300 dark:border-zinc-700 text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 disabled:opacity-50"
          >
            {previewState === "loading"
              ? "生成预览中..."
              : "预览项目题 (dev)"}
          </button>
        )}
      </div>
      {previewState === "error" && (
        <p className="text-xs text-red-600 dark:text-red-400 mb-3">
          预览失败: {previewError}
        </p>
      )}
      {previewActive && (
        <p className="text-xs text-amber-700 dark:text-amber-300 mb-3">
          ⚠ 开发者预览: 项目题为无自我介绍时的模拟生成, 正式面试会结合候选人
          intro 重新生成, 题面不会逐字一致; 该预览不落库、不影响正式面试。
        </p>
      )}
      <div className="space-y-3">
        {plan.rounds.map((round) => (
          <div
            key={round.round_id}
            className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4"
          >
            <div className="flex items-center gap-2 mb-3">
              <span
                className={`text-xs px-2 py-0.5 rounded uppercase tracking-wide ${
                  STAGE_BADGE_COLOR[round.stage] ?? STAGE_BADGE_COLOR.knowledge
                }`}
              >
                {STAGE_LABEL[round.stage] ?? round.stage}
              </span>
              <span className="text-sm text-zinc-600 dark:text-zinc-400">
                {round.questions.length} 题
              </span>
            </div>
            <ul className="space-y-2">
              {round.questions.map((q, idx) => {
                const src = describeSource(q);
                return (
                  <li
                    key={q.question_id}
                    className="border-l-2 border-zinc-200 dark:border-zinc-700 pl-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-sm text-zinc-800 dark:text-zinc-200 leading-relaxed">
                        <span className="text-zinc-400 tabular-nums mr-2">
                          {idx + 1}.
                        </span>
                        {q.text || (
                          <span className="text-zinc-400 italic">
                            (待生成 — 候选人进入此 stage 时由 Resume RAG 现场生成)
                          </span>
                        )}
                      </p>
                      <span
                        className={`text-xs whitespace-nowrap px-1.5 py-0.5 rounded shrink-0 font-mono ${
                          src.tone === "info"
                            ? "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"
                            : "text-zinc-400"
                        }`}
                      >
                        {src.label}
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------- 出题过程视图 (Sprint E) ----------
//
// 展示 plan.trace: topic 匹配全过程 (aspect query + resume 技能 → matched
// topics) + 每题来源路径 + project 题命中的 resume 切片原文。
// 默认折叠 —— 这是审计/调试信息, 不是 HR 日常主路径。

const PATH_LABEL: Record<string, string> = {
  self_intro: "固定模板",
  rag_refined: "题库召回 + LLM 精修",
  llm_direct_knowledge: "纯 LLM 出题 (维度+技能)",
  llm_direct_scenario: "纯 LLM 场景题 (维度+技能)",
  rag_direct: "题库原文 (LLM 不可用)",
  llm_generated: "纯 LLM 生成 (题库未命中)",
  fallback_template: "兜底模板",
  lazy_pending: "待懒生成",
  resume_section: "简历分段定向深挖",
  resume_rag: "Resume 切片 RAG + LLM",
  resume_llm: "Resume 全文 + LLM",
};

function PlanTraceView({
  trace,
  plan,
  resumeChunks,
}: {
  trace: PlanTrace;
  plan: InterviewPlan;
  resumeChunks: ResumeChunk[];
}) {
  const [open, setOpen] = useState(false);
  const [expandedChunks, setExpandedChunks] = useState<Set<string>>(new Set());

  const questionById = useMemo(
    () =>
      new Map(
        plan.rounds.flatMap((r) => r.questions).map((q) => [q.question_id, q]),
      ),
    [plan],
  );
  const chunkById = useMemo(
    () => new Map(resumeChunks.map((c) => [c.document_id, c])),
    [resumeChunks],
  );

  function toggleChunk(id: string) {
    setExpandedChunks((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <section className="mb-8">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-sm font-medium text-zinc-500 uppercase tracking-wide mb-3 flex items-center gap-2 hover:text-zinc-900 dark:hover:text-zinc-100"
      >
        出题过程 (trace) {open ? "▲" : "▼"}
      </button>

      {open && (
        <div className="space-y-4">
          {/* 1. topic 匹配过程 */}
          <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
            <p className="text-xs text-zinc-400 uppercase tracking-wide mb-2">
              第一步 · topic 匹配 (query = HR 考察维度 + 简历技能, 与题库 topic
              做 embedding 语义匹配)
            </p>
            <div className="space-y-1 text-sm">
              {[...trace.aspect_queries, ...trace.extracted_skills].map((q) => {
                const isSkill = trace.extracted_skills.includes(q);
                const hits = trace.matches[q] ?? [];
                return (
                  <div key={q} className="flex items-baseline gap-2 flex-wrap">
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded shrink-0 ${
                        isSkill
                          ? "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300"
                          : "bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300"
                      }`}
                    >
                      {isSkill ? "简历技能" : "考察维度"}
                    </span>
                    <span className="text-zinc-700 dark:text-zinc-300">
                      {q.length > 40 ? q.slice(0, 40) + "…" : q}
                    </span>
                    <span className="text-zinc-400">→</span>
                    {hits.length > 0 ? (
                      <>
                        {hits.map((t) => (
                          <span
                            key={t}
                            className="text-xs px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                          >
                            {t}
                          </span>
                        ))}
                        {(trace.llm_matched_skills ?? []).includes(q) && (
                          <span className="text-xs px-1 py-0.5 rounded border border-amber-300 dark:border-amber-700 text-amber-600 dark:text-amber-400">
                            LLM 归类
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-xs text-zinc-400">无匹配</span>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="mt-3 pt-3 border-t border-zinc-100 dark:border-zinc-800 text-sm flex flex-wrap gap-x-6 gap-y-1">
              <span className="text-zinc-500">
                命中 topic 并集:{" "}
                {trace.matched_topics.length > 0 ? (
                  <span className="text-emerald-700 dark:text-emerald-300">
                    {trace.matched_topics.join("、")}
                  </span>
                ) : (
                  <span className="text-zinc-400">
                    无 (knowledge 题走全题库向量检索)
                  </span>
                )}
              </span>
              {trace.unmatched_skills.length > 0 && (
                <span className="text-zinc-500">
                  未匹配技能 (已记 skill_backlog):{" "}
                  <span className="text-amber-700 dark:text-amber-300">
                    {trace.unmatched_skills.join("、")}
                  </span>
                </span>
              )}
            </div>
          </div>

          {/* 2. 每题来源路径 */}
          <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
            <p className="text-xs text-zinc-400 uppercase tracking-wide mb-2">
              第二步 · 逐题来源 (topic/难度分配 → 题库检索或生成路径)
            </p>
            <ul className="space-y-2">
              {trace.questions.map((qt) => {
                const q = questionById.get(qt.question_id);
                return (
                  <li
                    key={qt.question_id}
                    className="border-l-2 border-zinc-200 dark:border-zinc-700 pl-3 text-sm"
                  >
                    <div className="flex items-center gap-2 flex-wrap text-xs mb-0.5">
                      <span
                        className={`px-1.5 py-0.5 rounded uppercase ${
                          STAGE_BADGE_COLOR[qt.stage] ?? ""
                        }`}
                      >
                        {STAGE_LABEL[qt.stage] ?? qt.stage}
                      </span>
                      <span className="text-zinc-600 dark:text-zinc-300 font-medium">
                        {PATH_LABEL[qt.path] ?? qt.path}
                      </span>
                      {qt.topic && (
                        <span className="px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                          {qt.topic}
                        </span>
                      )}
                      {qt.section_title && (
                        <span className="px-1.5 py-0.5 rounded bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
                          {qt.section_title}
                        </span>
                      )}
                      {qt.difficulty && (
                        <span className="text-zinc-400">{qt.difficulty}</span>
                      )}
                      {qt.source_question_id && (
                        <span className="font-mono text-zinc-400">
                          seed:{qt.source_question_id.slice(0, 10)}
                        </span>
                      )}
                    </div>
                    <p className="text-zinc-800 dark:text-zinc-200 leading-relaxed">
                      {q?.text || (
                        <span className="text-zinc-400 italic">(待生成)</span>
                      )}
                    </p>
                    {qt.source_chunk_ids.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {qt.source_chunk_ids.map((cid) => (
                          <button
                            key={cid}
                            onClick={() => toggleChunk(cid)}
                            className="text-xs font-mono px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 hover:bg-zinc-200 dark:hover:bg-zinc-700"
                          >
                            切片 {cid.slice(-8)}{" "}
                            {expandedChunks.has(cid) ? "▲" : "▼"}
                          </button>
                        ))}
                      </div>
                    )}
                    {qt.source_chunk_ids
                      .filter((cid) => expandedChunks.has(cid))
                      .map((cid) => {
                        const chunk = chunkById.get(cid);
                        return (
                          <div
                            key={cid}
                            className="mt-1 rounded bg-zinc-50 dark:bg-zinc-800/40 p-2 text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed whitespace-pre-wrap"
                          >
                            {chunk
                              ? chunk.text
                              : "(切片不在 Milvus 中, 可能已过期)"}
                          </div>
                        );
                      })}
                  </li>
                );
              })}
            </ul>
          </div>

          {/* 3. resume 切片总览 */}
          {resumeChunks.length > 0 && (
            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
              <p className="text-xs text-zinc-400 uppercase tracking-wide mb-2">
                Resume 切片总览 ({resumeChunks.length} 片, project 题从中按
                考察维度语义召回 top-3)
              </p>
              <div className="space-y-1">
                {resumeChunks.map((c) => (
                  <div key={c.document_id}>
                    <button
                      onClick={() => toggleChunk(c.document_id)}
                      className="text-xs font-mono text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
                    >
                      #{c.chunk_index} {c.document_id.slice(-10)}{" "}
                      {expandedChunks.has(c.document_id) ? "▲" : "▼"}
                    </button>
                    {expandedChunks.has(c.document_id) && (
                      <div className="mt-1 mb-2 rounded bg-zinc-50 dark:bg-zinc-800/40 p-2 text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed whitespace-pre-wrap">
                        {c.text}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ---------- 面试过程视图 (Sprint 5.7 task 5) ----------
//
// 展示每题 + 候选人回答 + AnswerAssessment 的自然语言字段。
// **合规约束**: 绝不渲染 sufficiency / confidence 数字, 那是 LLM-as-judge 中间
// 产物校准前不可信; 只展示自然语言 (missing_signals / strengths / concerns /
// followup_goal), HR 看得到"为什么追问"+"信号缺什么" 但看不到数字。
//
// 数据流: session.history 是完整对话, session.assessments 是 Assessor 每题打的;
// followup turn 不在 session.assessments 里有专属 entry, 而是用其 parent question
// 的 assessment.followup_goal 解释。所以下面把 session 按"题分组": 主题 + 该题
// 的 assessment + 该题之后的所有 followup turns + 候选人对它们的回答。

function AssessmentView({
  session,
  plan,
}: {
  session: InterviewSessionDetail;
  plan: InterviewPlan;
}) {
  // 把 plan 题平摊到 question_id -> Question 索引
  const questionById = new Map(
    plan.rounds.flatMap((r) => r.questions).map((q) => [q.question_id, q]),
  );
  const assessmentByQid = new Map(
    session.assessments.map((a) => [a.question_id, a]),
  );

  // 把 session.history 按"interviewer turn -> 跟随的 candidate turn"配对
  type Pair = {
    interviewer_text: string;
    interviewer_ref_id: string | null;
    candidate_text: string | null;
  };
  const pairs: Pair[] = [];
  for (const t of session.history) {
    if (t.role === "interviewer") {
      pairs.push({
        interviewer_text: t.text,
        interviewer_ref_id: t.ref_id,
        candidate_text: null,
      });
    } else if (t.role === "candidate" && pairs.length > 0) {
      pairs[pairs.length - 1].candidate_text = t.text;
    }
  }

  if (pairs.length === 0) {
    return null;
  }

  return (
    <section className="mb-8">
      <h2 className="text-sm font-medium text-zinc-500 uppercase tracking-wide mb-3">
        面试过程 ({pairs.length} 个回合)
      </h2>
      <ol className="space-y-4">
        {pairs.map((p, idx) => {
          const isMainQuestion =
            p.interviewer_ref_id !== null &&
            questionById.has(p.interviewer_ref_id);
          const assessment =
            isMainQuestion && p.interviewer_ref_id
              ? assessmentByQid.get(p.interviewer_ref_id) ?? null
              : null;
          return (
            <li
              key={idx}
              className={`rounded-lg border bg-white dark:bg-zinc-900 p-4 ${
                isMainQuestion
                  ? "border-zinc-200 dark:border-zinc-800"
                  : "border-amber-200 dark:border-amber-900 bg-amber-50/30 dark:bg-amber-950/20"
              }`}
            >
              <div className="flex items-baseline gap-2 mb-2">
                <span className="text-xs uppercase tracking-wide text-zinc-400">
                  {isMainQuestion ? `Q${idx + 1}` : "追问"}
                </span>
              </div>
              <p className="text-sm text-zinc-800 dark:text-zinc-200 leading-relaxed mb-2">
                {p.interviewer_text}
              </p>
              {p.candidate_text && (
                <div className="bg-zinc-50 dark:bg-zinc-800/40 rounded p-2 mb-3">
                  <p className="text-xs text-zinc-400 mb-1">候选人回答</p>
                  <p className="text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed">
                    {p.candidate_text}
                  </p>
                </div>
              )}
              {assessment && (
                <AssessmentBlock assessment={assessment} />
              )}
              {!isMainQuestion && (
                <FollowupGoalBlock
                  pairs={pairs}
                  currentIdx={idx}
                  assessmentByQid={assessmentByQid}
                  questionById={questionById}
                />
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function AssessmentBlock({
  assessment,
}: {
  assessment: NonNullable<ReturnType<Map<string, never>["get"]>> | {
    missing_signals: string[];
    strengths: string[];
    concerns: string[];
    followup_goal: string;
    stop_reason: string;
  };
}) {
  // 上面 type 写得有点冗长, 是为了 narrow 而不引入新顶层 type alias
  const a = assessment as {
    missing_signals: string[];
    strengths: string[];
    concerns: string[];
    followup_goal: string;
    stop_reason: string;
  };
  const hasAny =
    a.missing_signals.length > 0 ||
    a.strengths.length > 0 ||
    a.concerns.length > 0 ||
    a.followup_goal ||
    a.stop_reason;
  if (!hasAny) return null;
  return (
    <div className="grid sm:grid-cols-2 gap-3 mt-2 text-xs">
      <SignalList
        label="缺失信号"
        items={a.missing_signals}
        tone="amber"
      />
      <SignalList label="亮点" items={a.strengths} tone="emerald" />
      <SignalList label="疑虑" items={a.concerns} tone="rose" />
      {a.followup_goal && (
        <div>
          <p className="text-zinc-400 uppercase tracking-wide mb-1">
            追问意图
          </p>
          <p className="text-zinc-700 dark:text-zinc-300">{a.followup_goal}</p>
        </div>
      )}
      {a.stop_reason && (
        <div>
          <p className="text-zinc-400 uppercase tracking-wide mb-1">
            不追问原因
          </p>
          <p className="text-zinc-700 dark:text-zinc-300 font-mono">
            {a.stop_reason}
          </p>
        </div>
      )}
    </div>
  );
}

function SignalList({
  label,
  items,
  tone,
}: {
  label: string;
  items: string[];
  tone: "amber" | "emerald" | "rose";
}) {
  if (items.length === 0) return null;
  const colorClass = {
    amber: "text-amber-700 dark:text-amber-300",
    emerald: "text-emerald-700 dark:text-emerald-300",
    rose: "text-rose-700 dark:text-rose-300",
  }[tone];
  return (
    <div>
      <p className={`uppercase tracking-wide mb-1 ${colorClass}`}>{label}</p>
      <ul className="space-y-0.5 text-zinc-700 dark:text-zinc-300">
        {items.map((s, i) => (
          <li key={i}>· {s}</li>
        ))}
      </ul>
    </div>
  );
}

function FollowupGoalBlock({
  pairs,
  currentIdx,
  assessmentByQid,
  questionById,
}: {
  pairs: { interviewer_ref_id: string | null }[];
  currentIdx: number;
  assessmentByQid: Map<string, { followup_goal: string }>;
  questionById: Map<string, unknown>;
}) {
  // 向前找最近的主题 (ref_id 在 questionById), 取它的 followup_goal 解释这条追问
  for (let i = currentIdx - 1; i >= 0; i--) {
    const refId = pairs[i].interviewer_ref_id;
    if (refId && questionById.has(refId)) {
      const a = assessmentByQid.get(refId);
      if (a?.followup_goal) {
        return (
          <p className="text-xs text-amber-700 dark:text-amber-300 mt-2">
            <span className="uppercase tracking-wide text-amber-500 mr-1">
              为什么追问:
            </span>
            {a.followup_goal}
          </p>
        );
      }
      break;
    }
  }
  return null;
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
