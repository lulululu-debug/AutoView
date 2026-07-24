"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  api,
  type FeishuImportProgress,
  type FeishuPreview,
} from "@/lib/api";

/**
 * Sprint 6.7 task 4: 从飞书导入知识文档。
 *
 * 流程: 状态探测 -> (未授权则连接飞书 OAuth) -> 贴 wiki/docx 链接预览
 * (标题 + 子文档列表) -> 配 dataset 元数据 -> 发起导入 -> 轮询进度 ->
 * 完成跳审核队列。未配置飞书时本页只显示配置指引 (入口在 /hr/admin
 * 也会隐藏)。
 */

type Status =
  | { kind: "loading" }
  | { kind: "unconfigured" }
  | {
      kind: "ready";
      authorized: boolean;
      source: "env" | "db" | null;
      appIdMasked: string | null;
    }
  | { kind: "error"; message: string };

const POLL_MS = 2000;

export default function FeishuImportPage() {
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  // Sprint 6.7 task 5: 前端凭证配置表单
  const [cfgAppId, setCfgAppId] = useState("");
  const [cfgSecret, setCfgSecret] = useState("");
  const [cfgSaving, setCfgSaving] = useState(false);
  const [url, setUrl] = useState("");
  const [preview, setPreview] = useState<FeishuPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // 导入表单
  const [datasetId, setDatasetId] = useState("");
  const [topic, setTopic] = useState("");
  const [category, setCategory] = useState("knowledge");
  const [competencyId, setCompetencyId] = useState("comp:tech");
  const [autoApprove, setAutoApprove] = useState(true);
  const [includeChildren, setIncludeChildren] = useState(true);

  // 进度
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState<FeishuImportProgress | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function refreshStatus() {
    try {
      const s = await api.feishuStatus();
      setStatus(
        s.configured
          ? {
              kind: "ready",
              authorized: s.authorized,
              source: s.source,
              appIdMasked: s.app_id_masked,
            }
          : { kind: "unconfigured" },
      );
    } catch (e) {
      setStatus({ kind: "error", message: errMessage(e) });
    }
  }

  useEffect(() => {
    void Promise.resolve().then(refreshStatus);
  }, []);

  async function saveConfig() {
    if (!cfgAppId.trim() || !cfgSecret.trim()) return;
    setCfgSaving(true);
    setNotice(null);
    try {
      await api.feishuPutConfig(cfgAppId.trim(), cfgSecret.trim());
      setCfgSecret("");
      await refreshStatus();
      setNotice("✅ 凭证有效, 已保存 (secret 加密存储, 不会回显)");
    } catch (e) {
      setNotice(errMessage(e));
    } finally {
      setCfgSaving(false);
    }
  }

  // 进度轮询; done/failed 停
  useEffect(() => {
    if (!jobId) return;
    pollRef.current = setInterval(async () => {
      try {
        const p = await api.feishuImportProgress(jobId);
        setProgress(p);
        if (p.status === "done" || p.status === "failed") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        /* 进度过期等: 停轮询, 保留最后状态 */
        if (pollRef.current) clearInterval(pollRef.current);
      }
    }, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobId]);

  async function connectFeishu() {
    try {
      const { url: authUrl } = await api.feishuAuthorizeUrl();
      window.location.href = authUrl; // 授权完成后回调 302 回 /hr/admin
    } catch (e) {
      setNotice(errMessage(e));
    }
  }

  async function doPreview() {
    if (!url.trim()) return;
    setPreviewing(true);
    setPreview(null);
    setNotice(null);
    try {
      const p = await api.feishuPreview(url.trim());
      setPreview(p);
      if (p.title && !topic) setTopic(p.title);
      if (p.needs_authorization) {
        setNotice("该节点有子文档, 列目录需要飞书授权 —— 请先「连接飞书」");
      }
    } catch (e) {
      setNotice(errMessage(e));
    } finally {
      setPreviewing(false);
    }
  }

  async function doImport() {
    if (!preview || !datasetId.trim() || !topic.trim()) return;
    setNotice(null);
    try {
      const r = await api.feishuImport({
        url: url.trim(),
        dataset_id: datasetId.trim(),
        topic: topic.trim(),
        category,
        competency_id: competencyId,
        auto_approve: autoApprove,
        include_children: includeChildren,
      });
      setJobId(r.job_id);
      setProgress({ status: "queued" });
    } catch (e) {
      setNotice(errMessage(e));
    }
  }

  return (
    <main className="min-h-screen bg-zinc-50 dark:bg-black p-6">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-semibold">从飞书导入</h1>
          <Link href="/hr/admin" className="text-sm text-zinc-500 hover:underline">
            ← 返回知识库
          </Link>
        </div>

        {status.kind === "loading" && (
          <p className="text-zinc-500">检查飞书连接状态...</p>
        )}

        {status.kind === "unconfigured" && (
          <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 text-sm space-y-3">
            <p className="font-medium">配置飞书应用凭证</p>
            <p className="text-zinc-500 text-xs">
              开放平台自建应用的 App ID / App Secret (需开通 wiki 与 docx
              只读权限, 并把回调地址加入白名单)。保存前会自动验证凭证有效性;
              Secret 加密存储, 保存后不再回显。
            </p>
            <div className="grid grid-cols-2 gap-3">
              <input
                value={cfgAppId}
                onChange={(e) => setCfgAppId(e.target.value)}
                placeholder="App ID (cli_...)"
                className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2"
              />
              <input
                value={cfgSecret}
                onChange={(e) => setCfgSecret(e.target.value)}
                type="password"
                placeholder="App Secret"
                className="rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2"
              />
            </div>
            <button
              onClick={saveConfig}
              disabled={cfgSaving || !cfgAppId.trim() || !cfgSecret.trim()}
              className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-4 py-2 font-medium hover:opacity-90 disabled:opacity-50"
            >
              {cfgSaving ? "验证并保存中..." : "验证并保存"}
            </button>
          </div>
        )}

        {status.kind === "error" && (
          <p className="text-red-600 text-sm">{status.message}</p>
        )}

        {status.kind === "ready" && (
          <>
            {/* 凭证信息条 */}
            <div className="flex items-center gap-2 mb-2 text-xs text-zinc-500">
              <span>
                凭证 {status.appIdMasked}
                {status.source === "env" ? " (部署环境固定)" : " (界面配置)"}
              </span>
              {status.source === "db" && (
                <button
                  onClick={async () => {
                    await api.feishuDeleteConfig().catch(() => {});
                    await refreshStatus();
                  }}
                  className="underline hover:text-zinc-700"
                >
                  清除重配
                </button>
              )}
            </div>

            {/* 连接状态条 */}
            <div className="flex items-center gap-3 mb-5 text-sm">
              <span
                className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 ${
                  status.authorized
                    ? "bg-emerald-50 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-300"
                    : "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400"
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    status.authorized ? "bg-emerald-500" : "bg-zinc-400"
                  }`}
                />
                {status.authorized ? "飞书已授权" : "未授权 (单篇文档仍可导入)"}
              </span>
              {!status.authorized && (
                <button
                  onClick={connectFeishu}
                  className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-3 py-1.5 text-xs font-medium hover:opacity-90"
                >
                  连接飞书 (导入 wiki 目录需要)
                </button>
              )}
            </div>

            {/* 链接 + 预览 */}
            <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 mb-4">
              <label className="block text-sm font-medium mb-2">
                飞书 wiki / docx 链接
              </label>
              <div className="flex gap-2">
                <input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://xxx.feishu.cn/wiki/..."
                  className="flex-1 rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2 text-sm"
                />
                <button
                  onClick={doPreview}
                  disabled={previewing || !url.trim()}
                  className="rounded-md border border-zinc-300 dark:border-zinc-700 px-4 py-2 text-sm font-medium hover:bg-zinc-100 dark:hover:bg-zinc-800 disabled:opacity-50"
                >
                  {previewing ? "预览中..." : "预览"}
                </button>
              </div>

              {preview && (
                <div className="mt-4 text-sm">
                  <p className="font-medium">
                    {preview.title ?? "(docx 文档, 标题导入时解析)"}
                  </p>
                  {preview.children && (
                    <ul className="mt-2 max-h-48 overflow-y-auto space-y-1 text-zinc-600 dark:text-zinc-400">
                      {preview.children.map((c) => (
                        <li key={c.node_token}>
                          · {c.title}
                          {c.obj_type !== "docx" && (
                            <span className="text-xs text-zinc-400">
                              {" "}({c.obj_type}, 跳过)
                            </span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                  {preview.has_child && !preview.children && (
                    <p className="mt-2 text-xs text-amber-600">
                      含子文档 (列表需授权)
                    </p>
                  )}
                </div>
              )}
            </div>

            {/* 导入表单 */}
            {preview && !jobId && (
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 mb-4 space-y-3 text-sm">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block font-medium mb-1">dataset_id</label>
                    <input
                      value={datasetId}
                      onChange={(e) => setDatasetId(e.target.value)}
                      placeholder="cs-db-redis"
                      className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2"
                    />
                  </div>
                  <div>
                    <label className="block font-medium mb-1">topic</label>
                    <input
                      value={topic}
                      onChange={(e) => setTopic(e.target.value)}
                      className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2"
                    />
                  </div>
                  <div>
                    <label className="block font-medium mb-1">category</label>
                    <select
                      value={category}
                      onChange={(e) => setCategory(e.target.value)}
                      className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2"
                    >
                      <option value="knowledge">knowledge</option>
                      <option value="scenario">scenario</option>
                    </select>
                  </div>
                  <div>
                    <label className="block font-medium mb-1">默认维度</label>
                    <select
                      value={competencyId}
                      onChange={(e) => setCompetencyId(e.target.value)}
                      className="w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 px-3 py-2"
                    >
                      <option value="comp:tech">技术深度</option>
                      <option value="comp:comm">沟通协作</option>
                    </select>
                  </div>
                </div>
                <div className="flex items-center gap-5">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={includeChildren}
                      onChange={(e) => setIncludeChildren(e.target.checked)}
                    />
                    导入全部子文档
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={autoApprove}
                      onChange={(e) => setAutoApprove(e.target.checked)}
                    />
                    派生后自动批准入题库
                  </label>
                </div>
                <button
                  onClick={doImport}
                  disabled={!datasetId.trim() || !topic.trim()}
                  className="rounded-md bg-zinc-900 dark:bg-zinc-100 text-white dark:text-black px-5 py-2 font-medium hover:opacity-90 disabled:opacity-50"
                >
                  开始导入
                </button>
              </div>
            )}

            {/* 进度 */}
            {progress && (
              <div className="rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 p-5 text-sm space-y-2">
                <p className="font-medium">
                  {progress.status === "queued" && "排队中..."}
                  {progress.status === "fetching" &&
                    `拉取文档 ${progress.done ?? 0}/${progress.total ?? "?"}: ${progress.current ?? ""}`}
                  {progress.status === "pipeline_running" &&
                    `入库与出题中 (${progress.n_files} 个文档)...`}
                  {progress.status === "done" && "✅ 导入完成"}
                  {progress.status === "failed" && `❌ 失败: ${progress.error}`}
                </p>
                {progress.status === "done" && (
                  <p className="text-zinc-600 dark:text-zinc-400">
                    chunks {progress.chunks} · drafts {progress.drafts}
                    {progress.skipped_dup
                      ? ` · 去重跳过 ${progress.skipped_dup} 篇`
                      : ""}
                    {" · "}
                    <Link
                      href={`/hr/admin/${progress.dataset_id ?? datasetId}`}
                      className="underline"
                    >
                      进入审核 →
                    </Link>
                  </p>
                )}
              </div>
            )}
          </>
        )}

        {notice && (
          <p className="mt-4 rounded-md bg-amber-50 dark:bg-amber-950 border border-amber-200 dark:border-amber-900 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
            {notice}
          </p>
        )}
      </div>
    </main>
  );
}

function errMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.status}: ${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
}
