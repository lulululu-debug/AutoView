"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { use, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  api,
  type ChunkWithDraftsResponse,
  type CompetencyId,
  type KnowledgeChunk,
  type QuestionDraft,
} from "@/lib/api";

/**
 * Sprint C 单 chunk 审核页 —— 打包审核 N 道 draft。
 *
 * 上半: chunk 上下文 (heading_path + 全文 + ⭐️/tag) 让 HR 看着原文判题。
 * 下半: 该 chunk 所有 drafts. 每道 inline 编辑 + approve/reject。
 * 底栏: 整 chunk bulk-approve (一次 N 道 pending).
 *
 * 父组件持有 drafts 数组, DraftCard 通过 onUpdate 回写; 单题操作不刷整页,
 * bulk-approve 后 refetch 同步 chunk-level stats. competency 从 URL query
 * 透传, 不在本页改 (避免跨 chunk 漂移; 想改回主页改默认)。
 */

const COMP_LABEL: Record<CompetencyId, string> = {
  "comp:tech": "技术深度",
  "comp:comm": "沟通协作",
};

const DIFFICULTY_LABEL: Record<string, string> = {
  easy: "简单",
  medium: "中等",
  hard: "困难",
};

const DIFFICULTY_COLOR: Record<string, string> = {
  easy: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  medium: "bg-sky-50 text-sky-700 dark:bg-sky-950 dark:text-sky-300",
  hard: "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
};

const QTYPE_LABEL: Record<string, string> = {
  concept: "概念",
  compare: "对比",
  scenario: "场景",
  followup: "深挖",
};

type State =
  | { kind: "loading" }
  | { kind: "ok"; chunk: KnowledgeChunk; drafts: QuestionDraft[] }
  | { kind: "error"; message: string };


export default function ChunkReviewPage({
  params,
}: {
  params: Promise<{ dataset: string; chunkId: string }>;
}) {
  const { dataset, chunkId } = use(params);
  const datasetId = decodeURIComponent(dataset);
  const searchParams = useSearchParams();
  const competencyRaw = searchParams.get("competency");
  const competency: CompetencyId =
    competencyRaw === "comp:comm" ? "comp:comm" : "comp:tech";

  const [state, setState] = useState<State>({ kind: "loading" });
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkError, setBulkError] = useState<string | null>(null);

  async function fetchChunk() {
    setState({ kind: "loading" });
    try {
      const data: ChunkWithDraftsResponse = await api.getChunkDrafts(chunkId);
      setState({ kind: "ok", chunk: data.chunk, drafts: data.drafts });
    } catch (e) {
      setState({ kind: "error", message: errMessage(e) });
    }
  }

  useEffect(() => {
    fetchChunk();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chunkId]);

  function updateDraft(updated: QuestionDraft) {
    if (state.kind !== "ok") return;
    setState({
      ...state,
      drafts: state.drafts.map((d) =>
        d.draft_id === updated.draft_id ? updated : d,
      ),
    });
  }

  async function handleBulkApprove() {
    if (state.kind !== "ok") return;
    const nPending = state.drafts.filter((d) => d.review_status === "pending").length;
    if (nPending === 0) return;
    if (
      !window.confirm(
        `将通过 ${nPending} 道 pending 题, 维度 = ${COMP_LABEL[competency]}, 立即入 Planner 召回池。继续?`,
      )
    ) {
      return;
    }
    setBulkRunning(true);
    setBulkError(null);
    try {
      await api.bulkApproveChunk(chunkId, {
        competency_id: competency,
        role_family: "backend",
      });
      await fetchChunk();
    } catch (e) {
      setBulkError(errMessage(e));
    } finally {
      setBulkRunning(false);
    }
  }

  const backHref = `/hr/admin/${encodeURIComponent(datasetId)}?competency=${competency}`;

  return (
    <main className="max-w-5xl mx-auto px-4 sm:px-6 py-8 pb-32">
      <header className="mb-6">
        <Link
          href={backHref}
          className="text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"
        >
          ← 返回 chunk 列表
        </Link>
        <h1 className="text-xl font-semibold mt-2">单 chunk 审核</h1>
        <p className="text-sm text-zinc-500 mt-1">
          维度: <strong className="text-zinc-700 dark:text-zinc-300">
            {COMP_LABEL[competency]}
          </strong>
        </p>
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
        <>
          <ChunkContextCard chunk={state.chunk} />
          <h2 className="text-base font-medium mt-8 mb-3">
            该 chunk 派生的题 ({state.drafts.length})
          </h2>
          <div className="space-y-3">
            {state.drafts.map((d) => (
              <DraftCard
                key={d.draft_id}
                draft={d}
                competency={competency}
                onUpdate={updateDraft}
              />
            ))}
          </div>

          <BulkBar
            pendingCount={state.drafts.filter((d) => d.review_status === "pending").length}
            running={bulkRunning}
            error={bulkError}
            competency={competency}
            onBulkApprove={handleBulkApprove}
          />
        </>
      )}
    </main>
  );
}


function ChunkContextCard({ chunk }: { chunk: KnowledgeChunk }) {
  return (
    <section className="rounded-md border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-4">
      <div className="flex flex-wrap items-baseline gap-2">
        {chunk.is_starred && <span className="text-amber-500">⭐️</span>}
        <h2 className="text-lg font-medium">
          {chunk.heading_path.length > 0
            ? chunk.heading_path.join(" › ")
            : "(无标题)"}
        </h2>
      </div>
      <div className="mt-1 text-xs text-zinc-500 flex flex-wrap gap-x-3 gap-y-1">
        <span>{chunk.file_path}</span>
        <span>· {chunk.char_count} 字</span>
        <span>· tag: {chunk.quality_tag}</span>
        {chunk.doc_title && <span>· 出处: {chunk.doc_title}</span>}
      </div>
      <pre className="mt-3 text-sm whitespace-pre-wrap font-sans text-zinc-700 dark:text-zinc-300 max-h-96 overflow-y-auto bg-zinc-50 dark:bg-zinc-950 rounded p-3 border border-zinc-100 dark:border-zinc-800">
        {chunk.text}
      </pre>
    </section>
  );
}


type DraftRowState =
  | { mode: "view"; error: string | null; busy: boolean }
  | { mode: "edit"; questionText: string; keyPointsText: string; saving: boolean; error: string | null };


function DraftCard({
  draft,
  competency,
  onUpdate,
}: {
  draft: QuestionDraft;
  competency: CompetencyId;
  onUpdate: (d: QuestionDraft) => void;
}) {
  const [rs, setRs] = useState<DraftRowState>({
    mode: "view", error: null, busy: false,
  });
  const isDone = draft.review_status !== "pending";

  async function handleApprove() {
    if (rs.mode !== "view") return;
    setRs({ ...rs, busy: true, error: null });
    try {
      await api.approveDraft(draft.draft_id, {
        competency_id: competency, role_family: "backend",
      });
      onUpdate({ ...draft, review_status: "approved" });
      setRs({ mode: "view", busy: false, error: null });
    } catch (e) {
      setRs({ mode: "view", busy: false, error: errMessage(e) });
    }
  }

  async function handleReject() {
    if (rs.mode !== "view") return;
    setRs({ ...rs, busy: true, error: null });
    try {
      const updated = await api.rejectDraft(draft.draft_id);
      onUpdate(updated);
      setRs({ mode: "view", busy: false, error: null });
    } catch (e) {
      setRs({ mode: "view", busy: false, error: errMessage(e) });
    }
  }

  function startEdit() {
    setRs({
      mode: "edit",
      questionText: draft.question_text,
      keyPointsText: draft.key_points.join("\n"),
      saving: false, error: null,
    });
  }

  function cancelEdit() {
    setRs({ mode: "view", busy: false, error: null });
  }

  async function saveEdit() {
    if (rs.mode !== "edit") return;
    setRs({ ...rs, saving: true, error: null });
    try {
      const updated = await api.editDraft(draft.draft_id, {
        question_text: rs.questionText.trim(),
        key_points: rs.keyPointsText
          .split("\n")
          .map((s) => s.trim())
          .filter((s) => s.length > 0),
      });
      onUpdate(updated);
      setRs({ mode: "view", busy: false, error: null });
    } catch (e) {
      setRs({ ...rs, saving: false, error: errMessage(e) });
    }
  }

  return (
    <div
      className={
        "rounded-md border bg-white dark:bg-zinc-900 p-4 " +
        (isDone
          ? draft.review_status === "approved"
            ? "border-emerald-300 dark:border-emerald-800 opacity-70"
            : "border-zinc-300 dark:border-zinc-700 opacity-50"
          : "border-zinc-200 dark:border-zinc-800")
      }
    >
      <div className="flex items-start gap-2 mb-2">
        <DifficultyBadge difficulty={draft.difficulty} />
        <QtypeBadge qtype={draft.qtype} />
        {draft.review_status === "approved" && (
          <span className="text-xs text-emerald-700 dark:text-emerald-300 font-medium">
            ✓ 已通过
          </span>
        )}
        {draft.review_status === "rejected" && (
          <span className="text-xs text-zinc-500 font-medium">× 已驳回</span>
        )}
      </div>

      {rs.mode === "view" && (
        <>
          <p className="text-sm text-zinc-800 dark:text-zinc-200 font-medium">
            {draft.question_text}
          </p>
          {draft.key_points.length > 0 && (
            <ul className="mt-2 text-xs text-zinc-600 dark:text-zinc-400 space-y-0.5 list-disc list-inside">
              {draft.key_points.map((k, i) => (
                <li key={i}>{k}</li>
              ))}
            </ul>
          )}
          {rs.error && (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">{rs.error}</p>
          )}
          {!isDone && (
            <div className="mt-3 flex gap-2">
              <button
                onClick={handleApprove}
                disabled={rs.busy}
                className="rounded bg-emerald-600 text-white px-2.5 py-1 text-xs hover:bg-emerald-700 disabled:opacity-50"
              >
                通过
              </button>
              <button
                onClick={handleReject}
                disabled={rs.busy}
                className="rounded bg-zinc-200 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 px-2.5 py-1 text-xs hover:bg-zinc-300 dark:hover:bg-zinc-700 disabled:opacity-50"
              >
                驳回
              </button>
              <button
                onClick={startEdit}
                disabled={rs.busy}
                className="rounded border border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 px-2.5 py-1 text-xs hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-50"
              >
                编辑
              </button>
            </div>
          )}
        </>
      )}

      {rs.mode === "edit" && (
        <>
          <label className="block text-xs text-zinc-500 mb-1">题文</label>
          <textarea
            value={rs.questionText}
            onChange={(e) => setRs({ ...rs, questionText: e.target.value })}
            rows={2}
            className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-sm px-2 py-1.5"
          />
          <label className="block text-xs text-zinc-500 mt-2 mb-1">
            评分要点 (每行一条)
          </label>
          <textarea
            value={rs.keyPointsText}
            onChange={(e) => setRs({ ...rs, keyPointsText: e.target.value })}
            rows={4}
            className="w-full rounded border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-xs px-2 py-1.5 font-mono"
          />
          {rs.error && (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">{rs.error}</p>
          )}
          <div className="mt-3 flex gap-2">
            <button
              onClick={saveEdit}
              disabled={rs.saving}
              className="rounded bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-2.5 py-1 text-xs disabled:opacity-50"
            >
              保存
            </button>
            <button
              onClick={cancelEdit}
              disabled={rs.saving}
              className="rounded border border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 px-2.5 py-1 text-xs hover:bg-zinc-50 dark:hover:bg-zinc-800 disabled:opacity-50"
            >
              取消
            </button>
          </div>
        </>
      )}
    </div>
  );
}


function DifficultyBadge({ difficulty }: { difficulty: string }) {
  const label = DIFFICULTY_LABEL[difficulty] ?? difficulty;
  const color =
    DIFFICULTY_COLOR[difficulty] ?? "bg-zinc-100 dark:bg-zinc-800 text-zinc-600";
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded ${color}`}>{label}</span>
  );
}


function QtypeBadge({ qtype }: { qtype: string }) {
  const label = QTYPE_LABEL[qtype] ?? qtype;
  return (
    <span className="text-xs px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
      {label}
    </span>
  );
}


function BulkBar({
  pendingCount,
  running,
  error,
  competency,
  onBulkApprove,
}: {
  pendingCount: number;
  running: boolean;
  error: string | null;
  competency: CompetencyId;
  onBulkApprove: () => void;
}) {
  if (pendingCount === 0 && !error) return null;
  return (
    <div className="fixed bottom-0 left-0 right-0 border-t border-zinc-200 dark:border-zinc-800 bg-white/95 dark:bg-zinc-900/95 backdrop-blur">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between gap-4">
        <div className="text-xs text-zinc-600 dark:text-zinc-400">
          {pendingCount > 0
            ? `还有 ${pendingCount} 道待审, 维度 = ${COMP_LABEL[competency]}`
            : "全部已处理"}
          {error && (
            <span className="ml-3 text-red-600 dark:text-red-400">{error}</span>
          )}
        </div>
        {pendingCount > 0 && (
          <button
            onClick={onBulkApprove}
            disabled={running}
            className="rounded-md bg-emerald-600 text-white px-3 py-1.5 text-sm font-medium hover:bg-emerald-700 disabled:opacity-50"
          >
            {running ? "处理中…" : `批量通过 ${pendingCount} 道`}
          </button>
        )}
      </div>
    </div>
  );
}


function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
