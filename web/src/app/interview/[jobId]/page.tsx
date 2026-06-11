"use client";

import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, api, type JobContext } from "@/lib/api";

type JobState =
  | { kind: "loading" }
  | { kind: "ok"; job: JobContext }
  | { kind: "error"; message: string; status?: number };

type SubmitState =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string };

const MIN_RESUME_CHARS = 20;
const JD_PREVIEW_MAX = 400;

export default function InterviewLandingPage({
  params,
}: {
  // Next.js 15+: dynamic params 是 Promise, 客户端组件用 React 19 的 use() 解
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = use(params);
  const router = useRouter();
  const [jobState, setJobState] = useState<JobState>({ kind: "loading" });
  const [resume, setResume] = useState("");
  const [submitState, setSubmitState] = useState<SubmitState>({ kind: "idle" });

  useEffect(() => {
    api
      .getJob(jobId)
      .then((job) => setJobState({ kind: "ok", job }))
      .catch((e: unknown) => {
        if (e instanceof ApiError) {
          setJobState({ kind: "error", status: e.status, message: e.message });
        } else {
          setJobState({ kind: "error", message: errMessage(e) });
        }
      });
  }, [jobId]);

  async function handleSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    const text = resume.trim();
    if (text.length < MIN_RESUME_CHARS) {
      setSubmitState({
        kind: "error",
        message: `Resume 太短 (${text.length} 字), 至少需要 ${MIN_RESUME_CHARS} 字`,
      });
      return;
    }
    setSubmitState({ kind: "submitting" });
    try {
      const result = await api.createCandidate(jobId, {
        resume: text,
        projects: [],
      });
      router.push(`/interview/${jobId}/${result.candidate_id}/waiting`);
    } catch (e: unknown) {
      const msg =
        e instanceof ApiError ? `${e.status}: ${e.message}` : errMessage(e);
      setSubmitState({ kind: "error", message: msg });
    }
  }

  if (jobState.kind === "loading") {
    return (
      <PageShell>
        <p className="text-zinc-500">加载职位信息...</p>
      </PageShell>
    );
  }

  if (jobState.kind === "error") {
    return (
      <PageShell>
        <h1 className="text-2xl font-semibold mb-2">
          {jobState.status === 404 ? "无效的面试链接" : "出错了"}
        </h1>
        {jobState.status === 404 ? (
          <p className="text-zinc-600 dark:text-zinc-400">
            找不到这个职位 (job_id={" "}
            <code className="font-mono text-xs">{jobId}</code>
            )。请向 HR 确认你的面试链接。
          </p>
        ) : (
          <p className="text-zinc-600 dark:text-zinc-400 font-mono text-sm">
            {jobState.message}
          </p>
        )}
      </PageShell>
    );
  }

  const { job } = jobState;
  const jdPreview =
    job.jd.length > JD_PREVIEW_MAX
      ? job.jd.slice(0, JD_PREVIEW_MAX) + "..."
      : job.jd;

  return (
    <PageShell>
      <div className="mb-8">
        <p className="text-xs text-zinc-500 uppercase tracking-wide mb-2">
          AI 面试
        </p>
        <h1 className="text-2xl font-semibold mb-3">{job.title}</h1>
        <p className="text-zinc-600 dark:text-zinc-400 whitespace-pre-line text-sm leading-relaxed">
          {jdPreview}
        </p>
        {job.requirements.length > 0 && (
          <ul className="mt-3 text-sm text-zinc-600 dark:text-zinc-400 list-disc list-inside space-y-1">
            {job.requirements.map((req, i) => (
              <li key={i}>{req}</li>
            ))}
          </ul>
        )}
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label
            htmlFor="resume"
            className="block text-sm font-medium mb-2"
          >
            请粘贴你的简历
          </label>
          <textarea
            id="resume"
            value={resume}
            onChange={(e) => setResume(e.target.value)}
            disabled={submitState.kind === "submitting"}
            rows={14}
            placeholder={
              "例如:\n张三 / 后端工程师 / 4 年经验\n- 2024-2025  某公司 高级后端\n  负责订单与支付链路稳定性..."
            }
            className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 p-3 text-sm font-mono disabled:opacity-60 focus:outline-none focus:ring-2 focus:ring-zinc-400"
          />
          <p className="text-xs text-zinc-500 mt-1">
            当前 {resume.length} 字 · 建议 200 字以上, 系统会针对项目深挖
          </p>
        </div>

        {submitState.kind === "error" && (
          <div className="rounded-md bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 p-3 text-sm text-red-700 dark:text-red-300">
            {submitState.message}
          </div>
        )}

        <button
          type="submit"
          disabled={
            submitState.kind === "submitting" || resume.trim().length === 0
          }
          className="w-full rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black py-2.5 text-sm font-medium hover:opacity-90 disabled:opacity-50 transition"
        >
          {submitState.kind === "submitting"
            ? "上传中..."
            : "上传简历, 准备面试"}
        </button>

        <p className="text-xs text-zinc-400 text-center">
          上传后系统会基于 JD + 你的简历生成面试题, 大约需要 10-30 秒
        </p>
      </form>
    </PageShell>
  );
}

function PageShell({ children }: { children: React.ReactNode }) {
  return (
    <main className="min-h-screen flex items-start justify-center bg-zinc-50 dark:bg-black p-6">
      <div className="w-full max-w-2xl mt-12 mb-12">{children}</div>
    </main>
  );
}

function errMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
