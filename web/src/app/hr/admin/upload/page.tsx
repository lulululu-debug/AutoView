"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import {
  ApiError,
  api,
  type CompetencyId,
} from "@/lib/api";

/**
 * Sprint upload: HR 端 md 文件上传页。
 *
 * 表单字段:
 *   dataset_id, topic, description, category (knowledge/scenario),
 *   role_family, competency_id, auto_approve, files[]
 *
 * 提交 → POST /admin/upload-knowledge multipart, 立即返回 → 跳转到
 * /hr/admin/{dataset_id} 自动轮询看进度.
 */

type RoleFamily = "backend" | "frontend" | "data_science" | "product" | "hr";

const ROLE_FAMILIES: { value: RoleFamily; label: string }[] = [
  { value: "backend", label: "后端" },
  { value: "frontend", label: "前端" },
  { value: "data_science", label: "数据/算法" },
  { value: "product", label: "产品" },
  { value: "hr", label: "HR/非技术" },
];

const COMPETENCIES: { value: CompetencyId; label: string }[] = [
  { value: "comp:tech", label: "技术深度" },
  { value: "comp:comm", label: "沟通协作" },
];

const CATEGORIES: { value: "knowledge" | "scenario"; label: string; desc: string }[] = [
  {
    value: "knowledge",
    label: "知识题 (knowledge)",
    desc: "考察基础知识 / 原理 — \"什么是 X / X 的实现\"",
  },
  {
    value: "scenario",
    label: "场景题 (scenario)",
    desc: "给具体情境让候选人现场决策 — \"线上 X 出错怎么办\"",
  },
];


export default function UploadKnowledgePage() {
  const router = useRouter();

  const [datasetId, setDatasetId] = useState("");
  const [topic, setTopic] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState<"knowledge" | "scenario">("knowledge");
  const [roleFamily, setRoleFamily] = useState<RoleFamily>("backend");
  const [competency, setCompetency] = useState<CompetencyId>("comp:tech");
  const [autoApprove, setAutoApprove] = useState(true);
  const [files, setFiles] = useState<File[]>([]);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const list = e.target.files;
    if (!list) return setFiles([]);
    setFiles(Array.from(list).filter((f) => f.name.toLowerCase().endsWith(".md")));
  }

  function totalMB() {
    const bytes = files.reduce((acc, f) => acc + f.size, 0);
    return (bytes / 1024 / 1024).toFixed(2);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!datasetId.trim()) return setError("dataset_id 不能为空");
    if (!topic.trim()) return setError("topic 不能为空");
    if (files.length === 0) return setError("至少要选 1 个 .md 文件");

    setSubmitting(true);
    try {
      const res = await api.uploadKnowledge({
        dataset_id: datasetId.trim(),
        topic: topic.trim(),
        description: description.trim(),
        category,
        role_family: roleFamily,
        competency_id: competency,
        auto_approve: autoApprove,
        files,
      });
      router.replace(
        `/hr/admin/${encodeURIComponent(res.dataset_id)}?competency=${competency}`,
      );
    } catch (e) {
      setSubmitting(false);
      setError(errMessage(e));
    }
  }

  return (
    <main className="max-w-3xl mx-auto px-4 sm:px-6 py-8">
      <header className="mb-6">
        <Link
          href="/hr/admin"
          className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          ← 返回数据集列表
        </Link>
        <h1 className="text-2xl font-semibold mt-2">上传知识库 md</h1>
        <p className="text-sm text-zinc-500 mt-1">
          上传 .md 文件 → 自动 chunk → LLM 反向出题 → (可选) 自动审核入题库.
          上传后跳转到数据集页面看进度.
        </p>
      </header>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div className="rounded-md border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4 space-y-4">
          <Field
            label="dataset_id"
            hint="数据集 id, 英文 + 短横, 唯一. e.g. javaguide-scenario-spring"
            required
          >
            <input
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
              placeholder="例: company-handbook-2026"
              className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm font-mono"
            />
          </Field>

          <Field
            label="topic (主题词)"
            hint="自由中文, 进 Milvus 召回时用. e.g. JAVA 基础, Spring 场景题"
            required
          >
            <input
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="例: Spring 场景题"
              className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
            />
          </Field>

          <Field label="description (可选)" hint="数据集详情备注">
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="例: 公司内部 Spring 实战手册, 高并发/事务/启动失败场景"
              className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
            />
          </Field>
        </div>

        <div className="rounded-md border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4 space-y-4">
          <Field label="category (题目风格)" hint="决定 LLM 反向出题用哪套 prompt + Planner 召回 stage 命中">
            <div className="space-y-2">
              {CATEGORIES.map((c) => (
                <label
                  key={c.value}
                  className={
                    "flex items-start gap-2 cursor-pointer rounded border px-3 py-2 " +
                    (category === c.value
                      ? "border-zinc-900 dark:border-zinc-100 bg-zinc-50 dark:bg-zinc-800"
                      : "border-zinc-200 dark:border-zinc-700")
                  }
                >
                  <input
                    type="radio"
                    name="category"
                    value={c.value}
                    checked={category === c.value}
                    onChange={() => setCategory(c.value)}
                    className="mt-0.5"
                  />
                  <div>
                    <div className="text-sm font-medium">{c.label}</div>
                    <div className="text-xs text-zinc-500 mt-0.5">{c.desc}</div>
                  </div>
                </label>
              ))}
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="role_family">
              <select
                value={roleFamily}
                onChange={(e) => setRoleFamily(e.target.value as RoleFamily)}
                className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
              >
                {ROLE_FAMILIES.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="competency (考察维度)">
              <select
                value={competency}
                onChange={(e) => setCompetency(e.target.value as CompetencyId)}
                className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm"
              >
                {COMPETENCIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={autoApprove}
              onChange={(e) => setAutoApprove(e.target.checked)}
              className="mt-0.5"
            />
            <div>
              <div className="font-medium">跑完自动审核通过, 直接入题库</div>
              <div className="text-xs text-zinc-500 mt-0.5">
                不勾就落 pending 队列等 HR 在 chunk 审核页处理.
              </div>
            </div>
          </label>
        </div>

        <div className="rounded-md border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4 space-y-3">
          <Field label="md 文件 (多选)" required>
            <input
              type="file"
              multiple
              accept=".md,text/markdown"
              onChange={onPickFiles}
              className="block w-full text-sm file:mr-3 file:rounded file:border-0 file:bg-zinc-100 file:dark:bg-zinc-800 file:px-3 file:py-1.5 file:text-zinc-700 file:dark:text-zinc-300"
            />
          </Field>
          {files.length > 0 && (
            <div className="text-xs text-zinc-500">
              已选 <strong>{files.length}</strong> 个文件, 共{" "}
              <strong>{totalMB()} MB</strong>:
              <ul className="mt-1 list-disc list-inside font-mono">
                {files.slice(0, 8).map((f) => (
                  <li key={f.name} className="truncate">
                    {f.name}
                  </li>
                ))}
                {files.length > 8 && <li>... 还有 {files.length - 8} 个</li>}
              </ul>
            </div>
          )}
        </div>

        {error && (
          <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 px-3 py-2 text-sm text-red-700 dark:text-red-300">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-3">
          <Link
            href="/hr/admin"
            className="rounded-md border border-zinc-300 dark:border-zinc-700 px-4 py-2 text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800"
          >
            取消
          </Link>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-4 py-2 text-sm font-medium hover:opacity-90 disabled:opacity-50"
          >
            {submitting ? "上传中…" : "开始上传 + 处理"}
          </button>
        </div>
      </form>
    </main>
  );
}


function Field({
  label, hint, required, children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-sm font-medium mb-1">
        {label}
        {required && <span className="text-red-500 ml-1">*</span>}
      </label>
      {hint && <p className="text-xs text-zinc-500 mb-1.5">{hint}</p>}
      {children}
    </div>
  );
}


function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
