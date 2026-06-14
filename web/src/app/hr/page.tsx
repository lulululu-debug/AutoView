"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ApiError,
  api,
  type CompletionPolicy,
  type FollowUpPolicy,
  type JobContext,
  type Track,
} from "@/lib/api";

/**
 * HR Dashboard 主页:
 * - 拉 GET /hr/jobs 列所有职位 (created_at 倒序)
 * - 提供"新建职位"折叠表单 (POST /jobs)
 * - 每行点击进 /hr/jobs/[id] (候选人列表)
 */

type ListState =
  | { kind: "loading" }
  | { kind: "ok"; jobs: JobContext[] }
  | { kind: "error"; message: string };

export default function HrDashboardPage() {
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [creating, setCreating] = useState(false);

  async function refresh() {
    setList({ kind: "loading" });
    try {
      const jobs = await api.listJobs();
      setList({ kind: "ok", jobs });
    } catch (e) {
      setList({ kind: "error", message: errMessage(e) });
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
      <header className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">职位</h1>
          <p className="text-sm text-zinc-500 mt-1">
            HR 端的入口: 创建职位、查看候选人面试进度、复核评估报告。
          </p>
        </div>
        <button
          onClick={() => setCreating((v) => !v)}
          className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-3 py-1.5 text-sm font-medium hover:opacity-90"
        >
          {creating ? "取消" : "新建职位"}
        </button>
      </header>

      {creating && (
        <CreateJobForm
          onSuccess={() => {
            setCreating(false);
            refresh();
          }}
          onCancel={() => setCreating(false)}
        />
      )}

      <JobList list={list} onRetry={refresh} />
    </main>
  );
}

function JobList({
  list,
  onRetry,
}: {
  list: ListState;
  onRetry: () => void;
}) {
  if (list.kind === "loading") {
    return <p className="text-sm text-zinc-500 py-6">加载中...</p>;
  }
  if (list.kind === "error") {
    return (
      <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/40 p-4 text-sm">
        <p className="text-red-700 dark:text-red-300 mb-2">
          加载失败: {list.message}
        </p>
        <button
          onClick={onRetry}
          className="text-red-700 dark:text-red-300 underline"
        >
          重试
        </button>
      </div>
    );
  }
  if (list.jobs.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-zinc-300 dark:border-zinc-700 p-8 text-center text-sm text-zinc-500">
        还没有职位, 点上面"新建职位"开始。
      </div>
    );
  }
  return (
    <ul className="space-y-2">
      {list.jobs.map((job) => (
        <li key={job.job_id}>
          <Link
            href={`/hr/jobs/${job.job_id}`}
            className="block rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4 hover:border-zinc-400 dark:hover:border-zinc-600 transition"
          >
            <div className="flex items-center justify-between gap-3 mb-1">
              <h2 className="font-medium">{job.title}</h2>
              <div className="flex items-center gap-2">
                <span
                  className={`text-xs px-1.5 py-0.5 rounded uppercase tracking-wide ${
                    job.track === "campus"
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
                      : "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300"
                  }`}
                >
                  {job.track === "campus" ? "校招" : "社招"}
                </span>
                <span className="text-xs text-zinc-400 font-mono">
                  {job.role_family}
                </span>
              </div>
            </div>
            <p className="text-sm text-zinc-600 dark:text-zinc-400 line-clamp-2">
              {job.jd}
            </p>
            {job.requirements.length > 0 && (
              <p className="text-xs text-zinc-500 mt-2">
                {job.requirements.slice(0, 3).join(" · ")}
                {job.requirements.length > 3 && " · ..."}
              </p>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}

type CreateState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string };

function CreateJobForm({
  onSuccess,
  onCancel,
}: {
  onSuccess: () => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [jd, setJd] = useState("");
  const [requirements, setRequirements] = useState<string[]>([""]);
  const [companyMaterials, setCompanyMaterials] = useState("");
  const [track, setTrack] = useState<Track>("lateral");
  const [state, setState] = useState<CreateState>({ kind: "idle" });
  // Sprint 5.7: 高级折叠区. 默认 collapsed + 字段为 schema 默认值; 提交时
  // 仅当 advanced 展开过且字段动过, 才把 policy 对象传给后端 (空 -> null);
  // 大多数 HR 不动这块, 后端用默认。
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxTotalQ, setMaxTotalQ] = useState("15");
  const [minCoverage, setMinCoverage] = useState("0.7");
  const [minSufficiency, setMinSufficiency] = useState("0.7");
  const [minConfidence, setMinConfidence] = useState("0.5");
  // Sprint 5.8: 一刀切 max_followups_per_question; HR 不动 = 后端 null =
  // stage 默认仍生效 (self_intro=0/knowledge=1/project=2/scenario=2);
  // 即使 HR 设 max=2, Interviewer 的 SELF_INTRO 类别二次保护永远 0 次。
  const [maxFollowups, setMaxFollowups] = useState("1");

  function addRequirement() {
    setRequirements((rs) => [...rs, ""]);
  }
  function removeRequirement(idx: number) {
    setRequirements((rs) => rs.filter((_, i) => i !== idx));
  }
  function updateRequirement(idx: number, val: string) {
    setRequirements((rs) => rs.map((r, i) => (i === idx ? val : r)));
  }

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    if (!title.trim() || !jd.trim()) return;
    setState({ kind: "submitting" });
    try {
      // Sprint 5.7: 只有 advanced 展开过才传 policy; 后端 null = 用默认。
      // 这样避免误传 "覆盖" 让 HR 以为是默认却实际写了死值。
      let followup_policy: FollowUpPolicy | null = null;
      let completion_policy: CompletionPolicy | null = null;
      if (advancedOpen) {
        const f = parseFloat;
        completion_policy = {
          min_competency_coverage: f(minCoverage),
          max_total_questions: parseInt(maxTotalQ, 10),
          mandatory_competencies: [],
        };
        followup_policy = {
          max_followups_per_question: parseInt(maxFollowups, 10),
          min_sufficiency_to_stop: f(minSufficiency),
          min_confidence_to_stop: f(minConfidence),
        };
      }
      await api.createJob({
        title: title.trim(),
        jd: jd.trim(),
        requirements: requirements.map((r) => r.trim()).filter(Boolean),
        company_materials: companyMaterials.trim(),
        track,
        followup_policy,
        completion_policy,
      });
      onSuccess();
    } catch (e) {
      setState({ kind: "error", message: errMessage(e) });
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 mb-6 space-y-4"
    >
      <h2 className="font-medium">新建职位</h2>

      <div>
        <label className="block text-xs text-zinc-500 mb-1">职位标题 *</label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={state.kind === "submitting"}
          placeholder="例如: 后端工程师"
          className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60"
        />
      </div>

      <div>
        <label className="block text-xs text-zinc-500 mb-1">招聘类型 *</label>
        <div className="flex items-center gap-2">
          {(["lateral", "campus"] as const).map((opt) => (
            <label
              key={opt}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm cursor-pointer ${
                track === opt
                  ? "border-zinc-900 dark:border-zinc-100 bg-zinc-100 dark:bg-zinc-800"
                  : "border-zinc-300 dark:border-zinc-700 hover:border-zinc-400"
              } ${state.kind === "submitting" ? "opacity-60 cursor-not-allowed" : ""}`}
            >
              <input
                type="radio"
                name="track"
                value={opt}
                checked={track === opt}
                onChange={() => setTrack(opt)}
                disabled={state.kind === "submitting"}
                className="accent-zinc-900 dark:accent-zinc-100"
              />
              {opt === "campus" ? "校招" : "社招"}
            </label>
          ))}
        </div>
        <p className="text-xs text-zinc-400 mt-1">
          影响面试 stage 序列: 校招重知识 + 项目轻; 社招重项目 + 场景。
        </p>
      </div>

      <div>
        <label className="block text-xs text-zinc-500 mb-1">JD 原文 *</label>
        <textarea
          value={jd}
          onChange={(e) => setJd(e.target.value)}
          disabled={state.kind === "submitting"}
          rows={6}
          placeholder="负责..."
          className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60"
        />
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="block text-xs text-zinc-500">岗位要求</label>
          <button
            type="button"
            onClick={addRequirement}
            disabled={state.kind === "submitting"}
            className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
          >
            + 添加一行
          </button>
        </div>
        <div className="space-y-2">
          {requirements.map((req, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <input
                value={req}
                onChange={(e) => updateRequirement(idx, e.target.value)}
                disabled={state.kind === "submitting"}
                placeholder="例如: 3 年以上分布式系统经验"
                className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60"
              />
              {requirements.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeRequirement(idx)}
                  disabled={state.kind === "submitting"}
                  className="text-zinc-400 hover:text-red-600 text-xs px-2"
                >
                  删除
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      <div>
        <label className="block text-xs text-zinc-500 mb-1">
          公司资料 (可选)
        </label>
        <textarea
          value={companyMaterials}
          onChange={(e) => setCompanyMaterials(e.target.value)}
          disabled={state.kind === "submitting"}
          rows={4}
          placeholder="公司业务、技术栈、文化等; 会被切片入 Milvus 给评估时做 RAG 上下文"
          className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 p-2 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60"
        />
      </div>

      <div className="border-t border-zinc-200 dark:border-zinc-800 pt-3">
        <button
          type="button"
          onClick={() => setAdvancedOpen((v) => !v)}
          className="text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 flex items-center gap-1"
        >
          <span>{advancedOpen ? "▼" : "▶"}</span> 高级 (面试策略阈值)
        </button>
        {advancedOpen && (
          <div className="mt-3 space-y-3 rounded-md bg-zinc-50 dark:bg-zinc-800/30 p-3">
            <p className="text-xs text-zinc-500">
              影响 Interviewer 何时提前结束、Evaluator 何时标"证据不充分"。
              留默认即可, 大多数 HR 不需要调。
            </p>
            <div className="grid sm:grid-cols-2 gap-3">
              <NumberField
                label="题数硬上限 (含追问)"
                value={maxTotalQ}
                onChange={setMaxTotalQ}
                step="1"
                disabled={state.kind === "submitting"}
              />
              <NumberField
                label="每维度证据充分度门槛 (0-1)"
                value={minCoverage}
                onChange={setMinCoverage}
                step="0.05"
                disabled={state.kind === "submitting"}
              />
              <NumberField
                label="Assessor 充分度停止阈值 (0-1)"
                value={minSufficiency}
                onChange={setMinSufficiency}
                step="0.05"
                disabled={state.kind === "submitting"}
              />
              <NumberField
                label="Assessor 置信度停止阈值 (0-1)"
                value={minConfidence}
                onChange={setMinConfidence}
                step="0.05"
                disabled={state.kind === "submitting"}
              />
              <NumberField
                label="每题最多追问次数 (0-3)"
                value={maxFollowups}
                onChange={setMaxFollowups}
                step="1"
                disabled={state.kind === "submitting"}
              />
            </div>
            <p className="text-xs text-zinc-500 mt-2">
              注: 即使设为 ≥1, 自我介绍 (self_intro) 永远不追问 (Interviewer
              类别二次保护)。
            </p>
          </div>
        )}
      </div>

      {state.kind === "error" && (
        <div className="rounded-md bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 p-2 text-sm text-red-700 dark:text-red-300">
          {state.message}
        </div>
      )}

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={
            state.kind === "submitting" || !title.trim() || !jd.trim()
          }
          className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {state.kind === "submitting" ? "提交中..." : "创建"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={state.kind === "submitting"}
          className="rounded-md border border-zinc-300 dark:border-zinc-700 px-4 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-60"
        >
          取消
        </button>
      </div>
    </form>
  );
}

function NumberField({
  label,
  value,
  onChange,
  step,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  step: string;
  disabled: boolean;
}) {
  return (
    <div>
      <label className="block text-xs text-zinc-500 mb-1">{label}</label>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-950 p-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-400 disabled:opacity-60 tabular-nums"
      />
    </div>
  );
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
