"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ApiError,
  api,
  type CompetencyId,
  type DatasetSummary,
} from "@/lib/api";

/**
 * Sprint C 知识库审核主页 — dataset 总览。
 *
 * - 拉 GET /admin/datasets, 列出每个 dataset 的 chunk/draft/seed 数
 * - 每个 dataset 可选默认 competency_id (localStorage 持久化, key per-dataset),
 *   进入审核页时 URL query 传过去, 避免每个 chunk 重复选
 * - "进入审核" 按钮跳 /hr/admin/{dataset}
 */

type ListState =
  | { kind: "loading" }
  | { kind: "ok"; datasets: DatasetSummary[] }
  | { kind: "error"; message: string };

const COMPETENCIES: { value: CompetencyId; label: string }[] = [
  { value: "comp:tech", label: "技术深度" },
  { value: "comp:comm", label: "沟通协作" },
];

function defaultCompetencyKey(datasetId: string): string {
  return `admin.defaultCompetency.${datasetId}`;
}

function readDefaultCompetency(datasetId: string): CompetencyId {
  if (typeof window === "undefined") return "comp:tech";
  const v = window.localStorage.getItem(defaultCompetencyKey(datasetId));
  if (v === "comp:tech" || v === "comp:comm") return v;
  return "comp:tech";
}

function writeDefaultCompetency(datasetId: string, v: CompetencyId): void {
  window.localStorage.setItem(defaultCompetencyKey(datasetId), v);
}

export default function AdminDatasetsPage() {
  const [list, setList] = useState<ListState>({ kind: "loading" });

  async function refresh() {
    setList({ kind: "loading" });
    try {
      const datasets = await api.listDatasets();
      setList({ kind: "ok", datasets });
    } catch (e) {
      setList({ kind: "error", message: errMessage(e) });
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
      <header className="mb-6 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/hr"
            className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
          >
            ← 返回职位列表
          </Link>
          <h1 className="text-2xl font-semibold mt-2">题库审核</h1>
          <p className="text-sm text-zinc-500 mt-1">
            知识库 chunk → LLM 反向出题 → 人工审核 → 入 Planner 召回池。
            每个 dataset 的默认 competency 仅本浏览器保存。
          </p>
        </div>
        <Link
          href="/hr/admin/upload"
          className="shrink-0 rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-3 py-2 text-sm font-medium hover:opacity-90"
        >
          + 上传新数据集
        </Link>
      </header>

      <DatasetList list={list} onRetry={refresh} />
    </main>
  );
}

function DatasetList({
  list,
  onRetry,
}: {
  list: ListState;
  onRetry: () => void;
}) {
  if (list.kind === "loading") {
    return <p className="text-sm text-zinc-500">加载中…</p>;
  }
  if (list.kind === "error") {
    return (
      <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 p-4 text-sm">
        <p className="text-red-700 dark:text-red-300">{list.message}</p>
        <button
          onClick={onRetry}
          className="mt-2 text-red-700 dark:text-red-300 underline"
        >
          重试
        </button>
      </div>
    );
  }
  if (list.datasets.length === 0) {
    return (
      <p className="text-sm text-zinc-500">
        还没有数据集。先跑{" "}
        <code className="text-xs px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800">
          scripts/ingest_md_corpus.py
        </code>{" "}
        入 chunk, 再跑{" "}
        <code className="text-xs px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800">
          scripts/derive_questions.py
        </code>{" "}
        出 draft。
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {list.datasets.map((d) => (
        <DatasetRow key={d.dataset_id} dataset={d} />
      ))}
    </div>
  );
}

function DatasetRow({ dataset }: { dataset: DatasetSummary }) {
  const [competency, setCompetency] = useState<CompetencyId>(() =>
    readDefaultCompetency(dataset.dataset_id),
  );

  function onChangeCompetency(v: CompetencyId) {
    setCompetency(v);
    writeDefaultCompetency(dataset.dataset_id, v);
  }

  return (
    <div className="rounded-md border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h2 className="text-base font-medium truncate">
              {dataset.topic || dataset.dataset_id}
            </h2>
            <span
              className={
                "text-xs px-1.5 py-0.5 rounded font-medium " +
                (dataset.category === "scenario"
                  ? "bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300"
                  : "bg-sky-100 dark:bg-sky-900 text-sky-700 dark:text-sky-300")
              }
            >
              {dataset.category === "scenario" ? "场景题" : "知识题"}
            </span>
            {dataset.topic && (
              <span className="text-xs text-zinc-400 font-mono">
                {dataset.dataset_id}
              </span>
            )}
            {!dataset.topic && (
              <span className="text-xs text-amber-600 dark:text-amber-400">
                ⚠ 缺 topic 元数据
              </span>
            )}
          </div>
          {dataset.description && (
            <p className="mt-1 text-xs text-zinc-500 line-clamp-2">
              {dataset.description}
            </p>
          )}
          {(dataset.source_repo || dataset.source_commit) && (
            <p className="mt-1 text-xs text-zinc-400 font-mono truncate">
              {dataset.source_repo}
              {dataset.source_commit && ` @ ${dataset.source_commit.slice(0, 8)}`}
            </p>
          )}
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-600 dark:text-zinc-400">
            <span>chunks: <strong>{dataset.n_chunks}</strong></span>
            <span>
              待审: <strong className="text-amber-600 dark:text-amber-400">
                {dataset.n_pending}
              </strong>
            </span>
            <span>
              已通过: <strong className="text-emerald-600 dark:text-emerald-400">
                {dataset.n_approved}
              </strong>
            </span>
            <span>
              已驳回: <strong className="text-zinc-500">{dataset.n_rejected}</strong>
            </span>
            <span>已入题库: <strong>{dataset.n_seed}</strong></span>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <label className="text-xs text-zinc-500 flex items-center gap-2">
            默认维度
            <select
              value={competency}
              onChange={(e) => onChangeCompetency(e.target.value as CompetencyId)}
              className="rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-2 py-1 text-xs"
            >
              {COMPETENCIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          {dataset.n_chunks > 0 ? (
            <Link
              href={`/hr/admin/${encodeURIComponent(dataset.dataset_id)}?competency=${competency}`}
              className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-3 py-1.5 text-xs font-medium hover:opacity-90"
            >
              进入审核 →
            </Link>
          ) : (
            <span className="text-xs text-zinc-400">无 chunk</span>
          )}
        </div>
      </div>
    </div>
  );
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
