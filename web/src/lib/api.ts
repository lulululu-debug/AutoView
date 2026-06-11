/**
 * API client for the AI Interview Platform backend.
 *
 * 设计:
 * - 统一 base URL 走 NEXT_PUBLIC_API_BASE_URL 环境变量, 缺省 dev 用 localhost:8000
 * - 所有调用走 request<T>(), 返回 typed JSON 或抛 ApiError
 * - ApiError 携带 status + detail, 让 UI 层映射 404/409 等成具体语义
 *
 * 后续 sprint 在 api.* 上加: jobs / candidates / interviews 等命名空间。
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* body 不是 JSON 时用默认 detail */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export type Health = {
  status: string;
  service: string;
  version: string;
};

export const api = {
  health: () => request<Health>("/health"),
};

export { API_BASE };
