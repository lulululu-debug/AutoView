"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { use, useEffect, useState } from "react";

import {
  ApiError,
  api,
  type ChunkWithDraftStats,
  type CompetencyId,
} from "@/lib/api";

/**
 * Sprint C 数据集详情 — chunk 列表 (审核入口)。
 *
 * - 拉 GET /admin/datasets/{ds}/chunks
 * - 表格按 pending 倒序、长度倒序 (后端已排好), HR 从最重的 chunk 开干
 * - 每行点击 → /hr/admin/{ds}/{chunkId}?competency=...
 *   query 透传 competency, 让 chunk 审核页知道 bulk-approve 用哪个维度
 */

type State =
  | { kind: "loading" }
  | { kind: "ok"; chunks: ChunkWithDraftStats[] }
  | { kind: "error"; message: string };

const QUALITY_TAG_LABEL: Record<string, string> = {
  ok: "ok",
  oversize: "超大",
  navigation: "导航",
  low_value: "低值",
};

const QUALITY_TAG_COLOR: Record<string, string> = {
  ok: "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400",
  oversize: "bg-amber-100 dark:bg-amber-900 text-amber-800 dark:text-amber-200",
  navigation: "bg-zinc-200 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400",
  low_value: "bg-zinc-200 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400",
};

export default function DatasetChunksPage({
  params,
}: {
  params: Promise<{ dataset: string }>;
}) {
  const { dataset } = use(params);
  const datasetId = decodeURIComponent(dataset);
  const searchParams = useSearchParams();
  const competencyRaw = searchParams.get("competency");
  const competency: CompetencyId =
    competencyRaw === "comp:comm" ? "comp:comm" : "comp:tech";

  const [state, setState] = useState<State>({ kind: "loading" });
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkBanner, setBulkBanner] = useState<string | null>(null);

  async function refresh() {
    try {
      const chunks = await api.listDatasetChunks(datasetId);
      setState({ kind: "ok", chunks });
    } catch (e) {
      setState({ kind: "error", message: errMessage(e) });
    }
  }

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    (async () => {
      try {
        const chunks = await api.listDatasetChunks(datasetId);
        if (cancelled) return;
        setState({ kind: "ok", chunks });
      } catch (e) {
        if (cancelled) return;
        setState({ kind: "error", message: errMessage(e) });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const totalPending =
    state.kind === "ok"
      ? state.chunks.reduce((acc, c) => acc + c.n_pending, 0)
      : 0;

  async function handleBulkAll() {
    if (totalPending === 0) return;
    const compLabel = competency === "comp:tech" ? "技术深度" : "沟通协作";
    if (
      !window.confirm(
        `将该数据集下所有 ${totalPending} 道 pending 题一次审核通过, 维度 = ${compLabel}.
后台任务跑完会自动入 SeedQuestion + Milvus. 操作不可撤销 (撤回 approve 需 SQL 手动).
继续?`,
      )
    ) {
      return;
    }
    setBulkRunning(true);
    setBulkBanner("后台任务已启动, 正在审核 + embed + Milvus 入库, 每 30s 自动刷新...");
    try {
      await api.bulkApproveDatasetAll(datasetId, {
        competency_id: competency,
        role_family: "backend",
      });
    } catch (e) {
      setBulkBanner(`触发失败: ${errMessage(e)}`);
      setBulkRunning(false);
      return;
    }

    // 轮询 chunk 列表直到 pending=0 或超时 (max 10 分钟)
    const startedAt = Date.now();
    const TIMEOUT_MS = 10 * 60 * 1000;
    while (Date.now() - startedAt < TIMEOUT_MS) {
      await new Promise((r) => setTimeout(r, 30_000));
      await refresh();
      // refresh 之后 state 立即更新, 但闭包里 state 是旧的, 直接拉一次新数据看 pending
      try {
        const chunks = await api.listDatasetChunks(datasetId);
        const pending = chunks.reduce((acc, c) => acc + c.n_pending, 0);
        if (pending === 0) {
          setBulkBanner("✓ 全部审核完成, pending=0");
          setBulkRunning(false);
          return;
        }
        setBulkBanner(`正在审核中... 剩余 pending=${pending}`);
      } catch {
        // 拉失败就下次再试
      }
    }
    setBulkBanner("⚠ 轮询超时 (10 分钟), 后台仍可能在跑, 手动刷新看进度");
    setBulkRunning(false);
  }

  const compLabel = competency === "comp:tech" ? "技术深度" : "沟通协作";

  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8">
      <header className="mb-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <Link
              href="/hr/admin"
              className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
            >
              ← 返回数据集列表
            </Link>
            <h1 className="text-2xl font-semibold mt-2 break-all">
              {datasetId}
            </h1>
            <p className="text-sm text-zinc-500 mt-1">
              默认维度:{" "}
              <strong className="text-zinc-700 dark:text-zinc-300">
                {compLabel}
              </strong>{" "}
              · 想换{" "}
              <Link
                href="/hr/admin"
                className="underline hover:text-zinc-700 dark:hover:text-zinc-200"
              >
                返回主页改
              </Link>
              。按"待审数 → chunk 字数"倒序, HR 先啃大块。
            </p>
          </div>
          {totalPending > 0 && (
            <button
              onClick={handleBulkAll}
              disabled={bulkRunning}
              className="shrink-0 rounded-md bg-emerald-600 text-white px-3 py-2 text-sm font-medium hover:bg-emerald-700 disabled:opacity-50"
            >
              {bulkRunning ? "审核中…" : `一键全审 ${totalPending} 道`}
            </button>
          )}
        </div>
        {bulkBanner && (
          <div className="mt-3 rounded-md border border-emerald-200 dark:border-emerald-900 bg-emerald-50 dark:bg-emerald-950/40 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-300">
            {bulkBanner}
          </div>
        )}
      </header>

      {state.kind === "loading" && (
        <p className="text-sm text-zinc-500">加载中…</p>
      )}
      {state.kind === "error" && (
        <div className="rounded-md border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 p-4 text-sm">
          <p className="text-red-700 dark:text-red-300">{state.message}</p>
        </div>
      )}
      {state.kind === "ok" && (
        <ChunkTable
          chunks={state.chunks}
          datasetId={datasetId}
          competency={competency}
        />
      )}
    </main>
  );
}

function ChunkTable({
  chunks,
  datasetId,
  competency,
}: {
  chunks: ChunkWithDraftStats[];
  datasetId: string;
  competency: CompetencyId;
}) {
  if (chunks.length === 0) {
    return <p className="text-sm text-zinc-500">该数据集没有 chunk。</p>;
  }
  return (
    <div className="overflow-x-auto rounded-md border border-zinc-200 dark:border-zinc-800">
      <table className="w-full text-sm">
        <thead className="bg-zinc-50 dark:bg-zinc-900 text-zinc-500 text-xs uppercase tracking-wide">
          <tr>
            <th className="text-left px-3 py-2">chunk</th>
            <th className="text-right px-3 py-2">字数</th>
            <th className="text-center px-3 py-2">tag</th>
            <th className="text-right px-3 py-2 text-amber-700 dark:text-amber-400">待审</th>
            <th className="text-right px-3 py-2 text-emerald-700 dark:text-emerald-400">通过</th>
            <th className="text-right px-3 py-2 text-zinc-500">驳回</th>
            <th className="w-px"></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
          {chunks.map((c) => (
            <ChunkRow
              key={c.chunk_id}
              chunk={c}
              datasetId={datasetId}
              competency={competency}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChunkRow({
  chunk,
  datasetId,
  competency,
}: {
  chunk: ChunkWithDraftStats;
  datasetId: string;
  competency: CompetencyId;
}) {
  const leaf =
    chunk.heading_path.length > 0
      ? chunk.heading_path[chunk.heading_path.length - 1]
      : "(无标题)";
  const breadcrumb =
    chunk.heading_path.length > 1
      ? chunk.heading_path.slice(0, -1).join(" > ")
      : "";
  const tagLabel = QUALITY_TAG_LABEL[chunk.quality_tag] ?? chunk.quality_tag;
  const tagColor =
    QUALITY_TAG_COLOR[chunk.quality_tag] ??
    "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400";
  const hasPending = chunk.n_pending > 0;
  const href =
    `/hr/admin/${encodeURIComponent(datasetId)}/${chunk.chunk_id}` +
    `?competency=${competency}`;

  return (
    <tr
      className={
        hasPending
          ? "hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
          : "opacity-60 hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
      }
    >
      <td className="px-3 py-2 max-w-md">
        <div className="flex items-baseline gap-2">
          {chunk.is_starred && (
            <span title="作者精选" className="text-amber-500">⭐️</span>
          )}
          <span className="font-medium truncate" title={leaf}>{leaf}</span>
        </div>
        <div className="text-xs text-zinc-500 truncate" title={chunk.file_path}>
          {breadcrumb ? `${breadcrumb} · ` : ""}
          {chunk.file_path}
        </div>
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-500">
        {chunk.char_count}
      </td>
      <td className="px-3 py-2 text-center">
        <span className={`text-xs px-1.5 py-0.5 rounded ${tagColor}`}>
          {tagLabel}
        </span>
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-amber-700 dark:text-amber-400 font-medium">
        {chunk.n_pending || ""}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-emerald-700 dark:text-emerald-400">
        {chunk.n_approved || ""}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-zinc-500">
        {chunk.n_rejected || ""}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        <Link
          href={href}
          className="text-xs text-zinc-700 dark:text-zinc-300 hover:underline"
        >
          {hasPending ? "审核 →" : "查看 →"}
        </Link>
      </td>
    </tr>
  );
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
