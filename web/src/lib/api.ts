/**
 * API client for the AI Interview Platform backend.
 *
 * 设计:
 * - 统一 base URL 走 NEXT_PUBLIC_API_BASE_URL 环境变量, 缺省 dev 用 localhost:8000
 * - 所有调用走 request<T>(), 返回 typed JSON 或抛 ApiError
 * - ApiError 携带 status + detail, 让 UI 层映射 404/409 等成具体语义
 * - { auth: true } 让该次调用带上 HR JWT Bearer 头 (从 lib/auth 取 token)
 * - 401 时自动 clearToken, 让 HrGuard 下一次渲染时把用户踢回登录页
 */

import { clearToken, readToken } from "@/lib/auth";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

type RequestOptions = RequestInit & { auth?: boolean };

async function request<T>(
  path: string,
  init?: RequestOptions,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (init?.auth) {
    const token = readToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    if (res.status === 401 && init?.auth) {
      // token 过期 / 错; 清掉让下一次 HrGuard 跳登录
      clearToken();
    }
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

async function requestVoid(
  path: string,
  init?: RequestOptions,
): Promise<void> {
  // 用于 204 No Content 或 fire-and-forget; 不要求响应体
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (init?.auth) {
    const token = readToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    if (res.status === 401 && init?.auth) clearToken();
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* 静默 */
    }
    throw new ApiError(res.status, detail);
  }
}

export type Health = {
  status: string;
  service: string;
  version: string;
};

export type Track = "campus" | "lateral";

export type JobContext = {
  job_id: string;
  title: string;
  jd: string;
  requirements: string[];
  company_materials: string;
  role_family: string;
  track: Track;
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

// Sprint 5.5: category 4 类 + stage 4 类 + lazy 静态信号
export type QuestionCategory =
  | "knowledge"
  | "project_experience"
  | "self_intro"
  | "scenario";

export type InterviewStage = "self_intro" | "knowledge" | "project" | "scenario";

export type Question = {
  question_id: string;
  competency_id: string | null;       // self_intro 题为 null
  text: string;
  type: string;
  category: QuestionCategory;
  lazy: boolean;                      // 静态信号: plan 阶段是否走 lazy 路径
  source_question_id: string | null;
  source_chunk_ids: string[];
};

export type InterviewRound = {
  round_id: string;
  index: number;
  title: string;
  stage: InterviewStage;
  competencies: Competency[];
  questions: Question[];
};

export type InterviewPlan = {
  plan_id: string;
  job_id: string;
  rounds: InterviewRound[];
  competencies: Competency[];         // 跨 stage 顶层权威 (Sprint 5.5)
};

// Sprint 5.7: HR 高级折叠区可配置的 policy
export type FollowUpPolicy = {
  max_followups_per_question: number;
  min_sufficiency_to_stop: number;
  min_confidence_to_stop: number;
};

export type CompletionPolicy = {
  min_competency_coverage: number;
  max_total_questions: number;
  mandatory_competencies: string[];
};

// Sprint 5.6: AnswerAssessment 自然语言字段(不暴露 sufficiency / confidence 数字)
export type AnswerAssessment = {
  question_id: string;
  sufficiency: number;          // 后端返回但前端 UI 不渲染 (合规)
  confidence: number;           // 同上
  missing_signals: string[];
  strengths: string[];
  concerns: string[];
  followup_goal: string;
  stop_reason: string;
};

// Sprint 5.7: HR /hr/sessions/{id} 返回的完整 session
export type TurnRole = "interviewer" | "candidate";
export type SessionTurn = {
  role: TurnRole;
  text: string;
  ref_id: string | null;
  at: string;
};
export type SessionAnswer = {
  answer_id: string;
  question_id: string;
  text: string;
  media_ref: string | null;
  asked_at: string;
};
export type SessionStatus = "created" | "in_progress" | "completed";
export type InterviewSessionDetail = {
  session_id: string;
  plan_id: string;
  job_id: string;
  status: SessionStatus;
  current_round: number;
  history: SessionTurn[];
  answers: SessionAnswer[];
  intro_text: string;
  assessments: AnswerAssessment[];
};

// 面试会话推进结果 (与后端 schemas.TurnResult 对齐)
export type TurnResult = {
  session_id: string;
  done: boolean;
  prompt: string | null;
  ref_id: string | null;
};

// HR 登录响应 (与后端 api.schemas.TokenResponse 对齐)
export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  role: string;
};

// HR Dashboard 列表 (与后端 api.schemas.CandidateWithStatus 对齐)
export type CandidateStatus =
  | "plan_pending"
  | "ready"
  | "completed"
  | "reviewed";

export type CandidateWithStatus = {
  candidate_id: string;
  job_id: string;
  resume_excerpt: string;
  status: CandidateStatus;
  session_id: string | null;
  report_id: string | null;
  review_decision: string | null;
  created_at: string;
};

// EvaluationReport + 子类型 (与后端 src.schemas 对齐)
export type DimensionScore = {
  competency_id: string;
  score: number;
  evidence: string[];
};

export type SignalKind = "language" | "tone" | "gaze";

export type PerformanceObservation = {
  kind: SignalKind;
  observation: string;
  confidence: number;
  note: string;
};

export type EvaluationReport = {
  report_id: string;
  session_id: string;
  content_scores: DimensionScore[];
  performance_observations: PerformanceObservation[];
  overall: number;
  summary: string;
  needs_human_review: boolean;
  rag_context_chunk_ids: string[];
  // Sprint 5.7: 每维度证据充分性 0~1; HR UI 可展示为进度条 / 颜色档
  competency_coverage: Record<string, number>;
};

// 复核相关
export type ReviewDecision = "recommend" | "reject" | "borderline";

export type DimensionOverride = {
  competency_id: string;
  score: number;
  note: string;
};

export type ReviewRecord = {
  record_id: string;
  report_id: string;
  reviewer_id: string;
  comments: string;
  dimension_overrides: DimensionOverride[];
  decision: ReviewDecision;
  reviewed_at: string;
};

export type ReviewSubmit = {
  comments: string;
  dimension_overrides: DimensionOverride[];
  decision: ReviewDecision;
};

export const api = {
  health: () => request<Health>("/health"),
  getJob: (jobId: string) => request<JobContext>(`/jobs/${jobId}`),
  createCandidate: (jobId: string, body: CandidateCreate) =>
    request<CandidateCreated>(`/jobs/${jobId}/candidates`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // Sprint 5.8: multipart 上传, 不能走 request<T>() (它强加 JSON Content-Type),
  // 手写 fetch + 错误映射跟 request<T>() 对齐 (ApiError + 401 处理无需要 —
  // 该端点不要 auth)。
  parseResume: async (jobId: string, file: File): Promise<{ parsed_text: string }> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(
      `${API_BASE}/jobs/${jobId}/candidates/parse-resume`,
      { method: "POST", body: fd },
    );
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body?.detail) detail = body.detail;
      } catch {
        /* 非 JSON body, 用默认 detail */
      }
      throw new ApiError(res.status, detail);
    }
    return res.json();
  },
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
  finalizeInterview: (sessionId: string) =>
    requestVoid(`/interviews/${sessionId}/finalize`, { method: "POST" }),
  login: (username: string, password: string) =>
    request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  // HR-end endpoints (走 require_hr_user, 自动注入 Bearer)
  listJobs: () => request<JobContext[]>("/hr/jobs", { auth: true }),
  createJob: (body: {
    title: string;
    jd: string;
    requirements: string[];
    company_materials: string;
    track: Track;
    followup_policy?: FollowUpPolicy | null;
    completion_policy?: CompletionPolicy | null;
  }) =>
    request<JobContext>("/jobs", {
      method: "POST",
      body: JSON.stringify(body),
      // 注: POST /jobs 当前后端没挂 require_hr_user (Sprint 5 后期可考虑挂上)。
      // 这里加 auth:true 也不会被后端拒绝, 是给未来收紧权限留口子。
      auth: true,
    }),
  listCandidates: (jobId: string) =>
    request<CandidateWithStatus[]>(
      `/hr/jobs/${jobId}/candidates`,
      { auth: true },
    ),
  getHrCandidate: (jobId: string, candidateId: string) =>
    request<CandidateWithStatus>(
      `/hr/jobs/${jobId}/candidates/${candidateId}`,
      { auth: true },
    ),
  getHrSession: (sessionId: string) =>
    request<InterviewSessionDetail>(`/hr/sessions/${sessionId}`, { auth: true }),
  getReport: (reportId: string) =>
    request<EvaluationReport>(`/hr/reports/${reportId}`, { auth: true }),
  getReview: (reportId: string) =>
    // 后端 GET /hr/reports/{id}/review 在没复核时返 null body 而非 404
    request<ReviewRecord | null>(`/hr/reports/${reportId}/review`, {
      auth: true,
    }),
  submitReview: (reportId: string, body: ReviewSubmit) =>
    request<ReviewRecord>(`/hr/reports/${reportId}/review`, {
      method: "PATCH",
      body: JSON.stringify(body),
      auth: true,
    }),
};

export { API_BASE };
