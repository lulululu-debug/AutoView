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

type ParseState =
  | { kind: "idle" }
  | { kind: "parsing"; filename: string; isImage: boolean }
  | { kind: "error"; message: string };

const MIN_RESUME_CHARS = 20;
const JD_PREVIEW_MAX = 400;
const ACCEPT_TYPES = ".pdf,.docx,.png,.jpg,.jpeg,.webp";
const IMAGE_EXT_RE = /\.(png|jpe?g|webp)$/i;

// Sprint F Phase 2: 分段编辑器
type EditSection = { key: number; type: string; title: string; text: string };

const SECTION_TYPE_OPTIONS: [string, string][] = [
  ["personal_info", "个人信息"],
  ["education", "教育经历"],
  ["project", "项目"],
  ["internship", "实习"],
  ["work", "工作经历"],
  ["skills", "专业技能"],
  ["award", "获奖荣誉"],
  ["other", "其他"],
];
// 会被单独出深挖题的段类型 (与后端 RESUME_DEEPDIVE_TYPES 对齐)
const DEEPDIVE_TYPES = new Set(["project", "internship", "work"]);

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
  const [parseState, setParseState] = useState<ParseState>({ kind: "idle" });
  // Sprint F Phase 2: 分段编辑器状态。mode=sections 时 sections 是真理之源,
  // resume 字符串在提交时由段文本拼接; mode=text 走旧纯文本流程。
  const [sections, setSections] = useState<EditSection[]>([]);
  const [mode, setMode] = useState<"text" | "sections">("text");
  const [nextKey, setNextKey] = useState(0);

  const joinedSectionText = sections.map((s) => s.text).join("\n\n");
  const effectiveLength =
    mode === "sections" ? joinedSectionText.trim().length : resume.trim().length;

  async function handleFileChange(ev: React.ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    if (!file) return;
    // 重置 file input value, 让相同文件能再次触发 (用户改文本后想重新解析)
    ev.target.value = "";
    const isImage = IMAGE_EXT_RE.test(file.name);
    setParseState({ kind: "parsing", filename: file.name, isImage });
    setSubmitState({ kind: "idle" });
    try {
      const { parsed_text, sections: parsed } = await api.parseResume(
        jobId,
        file,
      );
      setResume(parsed_text);
      // 至少切出 2 段才值得进分段编辑器; whole_text 兜底段 = 没切出来
      const usable = (parsed ?? []).filter((s) => s.source !== "whole_text");
      if (usable.length >= 2) {
        setSections(
          usable.map((s, i) => ({
            key: i,
            type: s.type,
            title: s.title,
            text: s.text,
          })),
        );
        setNextKey(usable.length);
        setMode("sections");
      } else {
        setSections([]);
        setMode("text");
      }
      setParseState({ kind: "idle" });
    } catch (e: unknown) {
      const msg =
        e instanceof ApiError ? `${e.status}: ${e.message}` : errMessage(e);
      setParseState({ kind: "error", message: msg });
    }
  }

  function updateSection(key: number, patch: Partial<EditSection>) {
    setSections((prev) =>
      prev.map((s) => (s.key === key ? { ...s, ...patch } : s)),
    );
  }

  function removeSection(key: number) {
    setSections((prev) => prev.filter((s) => s.key !== key));
  }

  function moveSection(key: number, delta: -1 | 1) {
    setSections((prev) => {
      const i = prev.findIndex((s) => s.key === key);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const next = [...prev];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  function addSection() {
    setSections((prev) => [
      ...prev,
      { key: nextKey, type: "project", title: "", text: "" },
    ]);
    setNextKey((k) => k + 1);
  }

  function switchToTextMode() {
    // 单向转换: 分段 → 纯文本 (拼接)。想回分段模式重新上传文件再解析。
    setResume(joinedSectionText);
    setSections([]);
    setMode("text");
  }

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
    const inSections = mode === "sections" && sections.length > 0;
    const text = (inSections ? joinedSectionText : resume).trim();
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
        ...(inSections
          ? {
              sections: sections
                .filter((s) => s.text.trim().length > 0)
                .map(({ type, title, text: t }) => ({
                  type,
                  title,
                  text: t,
                })),
            }
          : {}),
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
        <div className="rounded-md border border-dashed border-zinc-300 dark:border-zinc-700 p-3">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <span className="text-xs px-2 py-1 rounded bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black font-medium">
              选择文件
            </span>
            <span className="text-zinc-500 text-xs">
              支持 PDF、docx、图片截图/照片 (png/jpg/webp, ≤5MB); 解析后可编辑
            </span>
            <input
              type="file"
              accept={ACCEPT_TYPES}
              onChange={handleFileChange}
              disabled={
                parseState.kind === "parsing" ||
                submitState.kind === "submitting"
              }
              className="hidden"
            />
          </label>
          {parseState.kind === "parsing" && (
            <p className="text-xs text-zinc-500 mt-2">
              解析中: {parseState.filename}...
              {parseState.isImage && " (图片识别较慢, 请稍候)"}
            </p>
          )}
          {parseState.kind === "error" && (
            <p className="text-xs text-red-600 dark:text-red-400 mt-2">
              解析失败: {parseState.message} · 你也可以直接粘贴文本到下方
            </p>
          )}
        </div>

        {mode === "sections" ? (
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-sm font-medium">
                简历分段确认 (共 {sections.length} 段)
              </label>
              <button
                type="button"
                onClick={switchToTextMode}
                className="text-xs text-zinc-500 underline hover:text-zinc-700"
              >
                改用纯文本编辑
              </button>
            </div>
            <p className="text-xs text-zinc-500 mb-3">
              AI 已自动分段, 请检查项目/实习是否切分正确 —— 系统会针对每个
              「项目/实习/工作经历」段单独出深挖题。
            </p>
            <div className="space-y-3">
              {sections.map((s, i) => (
                <div
                  key={s.key}
                  className={`rounded-md border p-3 space-y-2 ${
                    DEEPDIVE_TYPES.has(s.type)
                      ? "border-emerald-300 dark:border-emerald-800 bg-emerald-50/40 dark:bg-emerald-950/20"
                      : "border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <select
                      value={s.type}
                      onChange={(e) =>
                        updateSection(s.key, { type: e.target.value })
                      }
                      disabled={submitState.kind === "submitting"}
                      className="text-xs rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-1.5 py-1"
                    >
                      {SECTION_TYPE_OPTIONS.map(([v, label]) => (
                        <option key={v} value={v}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <input
                      value={s.title}
                      onChange={(e) =>
                        updateSection(s.key, { title: e.target.value })
                      }
                      disabled={submitState.kind === "submitting"}
                      placeholder="段标题 (如项目名)"
                      className="flex-1 text-sm rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1"
                    />
                    <button
                      type="button"
                      onClick={() => moveSection(s.key, -1)}
                      disabled={i === 0}
                      className="text-xs text-zinc-400 hover:text-zinc-600 disabled:opacity-30 px-1"
                      title="上移"
                    >
                      ↑
                    </button>
                    <button
                      type="button"
                      onClick={() => moveSection(s.key, 1)}
                      disabled={i === sections.length - 1}
                      className="text-xs text-zinc-400 hover:text-zinc-600 disabled:opacity-30 px-1"
                      title="下移"
                    >
                      ↓
                    </button>
                    <button
                      type="button"
                      onClick={() => removeSection(s.key)}
                      className="text-xs text-red-400 hover:text-red-600 px-1"
                      title="删除该段"
                    >
                      ✕
                    </button>
                  </div>
                  <textarea
                    value={s.text}
                    onChange={(e) =>
                      updateSection(s.key, { text: e.target.value })
                    }
                    disabled={submitState.kind === "submitting"}
                    rows={Math.min(
                      10,
                      Math.max(3, s.text.split("\n").length),
                    )}
                    className="w-full rounded border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-950 p-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-zinc-400"
                  />
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={addSection}
              disabled={submitState.kind === "submitting"}
              className="mt-3 text-xs text-zinc-500 border border-dashed border-zinc-300 dark:border-zinc-700 rounded-md px-3 py-1.5 hover:text-zinc-700 hover:border-zinc-400"
            >
              ＋ 添加一段
            </button>
            <ResumeLengthHint length={joinedSectionText.trim().length} />
          </div>
        ) : (
          <div>
            <label
              htmlFor="resume"
              className="block text-sm font-medium mb-2"
            >
              简历内容 (可上传文件自动填充, 也可直接粘贴)
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
            <ResumeLengthHint length={resume.length} />
          </div>
        )}

        {submitState.kind === "error" && (
          <div className="rounded-md bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 p-3 text-sm text-red-700 dark:text-red-300">
            {submitState.message}
          </div>
        )}

        <button
          type="submit"
          disabled={
            submitState.kind === "submitting" || effectiveLength === 0
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

/**
 * Resume 长度反馈三档:
 *   0 字            zinc 中性
 *   1-199 字        zinc + "建议 200 字以上"
 *   200-499 字      emerald + "可以了"
 *   500+ 字         emerald + "详细, 系统能深挖更多"
 */
function ResumeLengthHint({ length }: { length: number }) {
  const tier =
    length === 0 ? "empty" : length < 200 ? "short" : length < 500 ? "ok" : "rich";
  const klass =
    tier === "ok" || tier === "rich"
      ? "text-emerald-600 dark:text-emerald-400"
      : "text-zinc-500";
  const hint =
    tier === "empty"
      ? "粘贴简历后开始"
      : tier === "short"
        ? `当前 ${length} 字, 建议 200 字以上, 系统才能针对项目深挖`
        : tier === "ok"
          ? `当前 ${length} 字 · 可以了`
          : `当前 ${length} 字 · 内容详细, 系统能深挖更多细节`;
  return <p className={`text-xs mt-1 ${klass}`}>{hint}</p>;
}

function errMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
