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

export type JobContext = {
  job_id: string;
  title: string;
  jd: string;
  requirements: string[];
  company_materials: string;
  role_family: string;
};

export type CandidateCreate = {
  resume: string;
  projects: string[];
};

export type CandidateCreated = {
  candidate_id: string;
  job_id: string;
  plan_pending: boolean;
};

// Plan 类型 (与后端 schemas.InterviewPlan 对应; 当前只用必要字段)
export type Competency = {
  competency_id: string;
  name: string;
  description: string;
  weight: number;
};

export type Question = {
  question_id: string;
  competency_id: string;
  text: string;
  type: string;
  category: string;
  source_question_id: string | null;
  source_chunk_ids: string[];
};

export type InterviewRound = {
  round_id: string;
  index: number;
  title: string;
  competencies: Competency[];
  questions: Question[];
};

export type InterviewPlan = {
  plan_id: string;
  job_id: string;
  rounds: InterviewRound[];
};

// 面试会话推进结果 (与后端 schemas.TurnResult 对齐)
export type TurnResult = {
  session_id: string;
  done: boolean;
  prompt: string | null;
  ref_id: string | null;
};

export const api = {
  health: () => request<Health>("/health"),
  getJob: (jobId: string) => request<JobContext>(`/jobs/${jobId}`),
  createCandidate: (jobId: string, body: CandidateCreate) =>
    request<CandidateCreated>(`/jobs/${jobId}/candidates`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getCandidatePlan: (jobId: string, candidateId: string) =>
    request<InterviewPlan>(
      `/jobs/${jobId}/candidates/${candidateId}/plan`,
    ),
  startInterview: (candidateId: string) =>
    request<TurnResult>("/interviews", {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId }),
    }),
  resumeInterview: (sessionId: string) =>
    request<TurnResult>(`/interviews/${sessionId}`),
  submitAnswer: (sessionId: string, text: string) =>
    request<TurnResult>(`/interviews/${sessionId}/answers`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
};

export { API_BASE };
