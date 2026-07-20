/**
 * API client for the AI Interview Platform backend.
 *
 * 设计:
 * - 统一 base URL 走 NEXT_PUBLIC_API_BASE_URL 环境变量, 缺省 dev 用 localhost:8000
 * - 所有调用走 request<T>(), 返回 typed JSON 或抛 ApiError
 * - ApiError 携带 status + detail, 让 UI 层映射 404/409 等成具体语义
 * - Sprint 5.8: 鉴权从 Bearer 头改成 httpOnly cookie. 所有请求 credentials:
 *   "include" 让浏览器自动带 cookie; { auth: true } 标志保留作向后兼容
 *   (callsite 不动) 但无实际行为; 401 时清 role 缓存让 HrGuard 跳登录。
 */

import { clearRole } from "@/lib/auth";

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
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!res.ok) {
    if (res.status === 401) {
      // cookie 过期 / 错; 清 role 让下一次 HrGuard 跳登录
      clearRole();
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
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (!res.ok) {
    if (res.status === 401) clearRole();
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

// Sprint 5.9: HR 选 role_family 决定 Planner 配比 + aspect 默认模板
export type RoleFamily =
  | "backend"
  | "frontend"
  | "data_science"
  | "product"
  | "hr";

// Sprint 5.9: ProfileAspect = 画像子维度, HR 发布岗位时增删改;
// 候选人答题被 Assessor 标 covered_aspects, 用并集算 richness 决定是否终止面试。
export type ProfileAspect = {
  aspect_id: string;
  competency_id: string;     // "comp:tech" / "comp:comm" (Planner 稳定 ID)
  name: string;
  description: string;
};

export type JobContext = {
  job_id: string;
  title: string;
  jd: string;
  requirements: string[];
  company_materials: string;
  role_family: string;
  track: Track;
  question_source?: "rag" | "llm_direct";  // Sprint H: 出题来源
  aspects: ProfileAspect[];  // Sprint 5.9
};

// Sprint F Phase 2: 简历语义分段 (parse-resume 返回, 候选人确认后随
// createCandidate 提交; source 仅展示用, 提交时 server 强制改 user_confirmed)
export type ResumeSection = {
  type: string; // personal_info/education/project/internship/work/skills/award/other
  title: string;
  text: string;
  source: string; // llm_anchor / heuristic / whole_text / user_confirmed
};

export type CandidateCreate = {
  resume: string;
  projects: string[];
  sections?: { type: string; title: string; text: string }[];
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

// Sprint E: 出题过程 trace (仅 HR 端接口返回; 候选人端 plan 接口剥掉)
export type QuestionTrace = {
  question_id: string;
  stage: InterviewStage;
  category: QuestionCategory;
  path: string;                       // rag_refined / rag_direct / llm_generated / fallback_template / self_intro / lazy_pending / resume_section / resume_rag / resume_llm
  topic: string | null;
  difficulty: string | null;
  source_question_id: string | null;
  source_chunk_ids: string[];
  section_title?: string | null;      // Sprint F: resume_section 路径针对的简历段
};

export type PlanTrace = {
  aspect_queries: string[];
  extracted_skills: string[];
  matches: Record<string, string[]>;  // query → matched topics
  matched_topics: string[];
  unmatched_skills: string[];
  llm_matched_skills: string[];       // embedding 未中、LLM 兜底归类命中的
  questions: QuestionTrace[];
};

export type InterviewPlan = {
  plan_id: string;
  job_id: string;
  rounds: InterviewRound[];
  competencies: Competency[];         // 跨 stage 顶层权威 (Sprint 5.5)
  trace?: PlanTrace | null;           // Sprint E; 老 plan / 候选人端为 null
};

// Sprint E: HR 端 resume 切片 (project 题 source_chunk_ids 的原文)
export type ResumeChunk = {
  document_id: string;
  chunk_index: number;
  text: string;
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

// ---------- Admin: 知识库审核 (Sprint C) ----------

export type DatasetSummary = {
  dataset_id: string;
  n_chunks: number;
  n_pending: number;
  n_approved: number;
  n_rejected: number;
  n_seed: number;
  // Sprint D-lite: 来自 datasets 元数据表; 老 dataset 缺元数据时为空串
  topic: string;
  description: string;
  source_repo: string;
  source_commit: string;
  // Sprint upload: knowledge / scenario
  category: "knowledge" | "scenario";
};

export type ChunkWithDraftStats = {
  chunk_id: string;
  file_path: string;
  heading_path: string[];
  quality_tag: string;
  is_starred: boolean;
  char_count: number;
  n_pending: number;
  n_approved: number;
  n_rejected: number;
};

export type KnowledgeChunk = {
  chunk_id: string;
  source_repo: string;
  source_commit: string;
  dataset_id: string;
  file_path: string;
  doc_title: string;
  doc_tags: string[];
  domain: string;
  topic: string;
  heading_path: string[];
  is_starred: boolean;
  text: string;
  char_count: number;
  content_hash: string;
  quality_tag: string;
};

export type DraftReviewStatus = "pending" | "approved" | "rejected";

export type QuestionDraft = {
  draft_id: string;
  chunk_id: string;
  dataset_id: string;
  question_text: string;
  qtype: string;       // concept / compare / scenario / followup
  difficulty: string;  // easy / medium / hard
  key_points: string[];
  prompt_version: string;
  llm_model: string;
  review_status: DraftReviewStatus;
};

export type ChunkWithDraftsResponse = {
  chunk: KnowledgeChunk;
  drafts: QuestionDraft[];
};

// approve 后端返回的 SeedQuestion (subset, UI 只需 question_id 确认入库)
export type SeedQuestion = {
  question_id: string;
  role_family: string;
  competency: string;
  text: string;
  source: string;
  category: string;
  dataset_id: string;
  source_draft_id: string | null;
  key_points: string[];
  difficulty: string;
  qtype: string;
};

export type CompetencyId = "comp:tech" | "comp:comm";

export type EditDraftBody = {
  question_text?: string;
  key_points?: string[];
};

export type ApproveBody = {
  competency_id: CompetencyId;
  role_family?: string;
};

/** Sprint 6-3: 过渡语音句数, 与后端 orchestrator.FILLER_TEXTS 保持同步。 */
export const FILLER_COUNT = 3;

/**
 * Sprint 6-2/6-3: 拉 TTS 音频 Blob 的共用底座。
 * 非 200 (204 未配置 / 404) 与网络错误一律 null —— 音频是增强不是依赖。
 */
async function fetchAudioBlob(path: string): Promise<Blob | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, { credentials: "include" });
    if (res.status !== 200) return null;
    return await res.blob();
  } catch {
    return null;
  }
}

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
  parseResume: async (
    jobId: string,
    file: File,
  ): Promise<{ parsed_text: string; sections: ResumeSection[] }> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(
      `${API_BASE}/jobs/${jobId}/candidates/parse-resume`,
      { method: "POST", body: fd, credentials: "include" },
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
  /**
   * Sprint 6-2: 拉某个面试官 turn 的 TTS 音频 (mp3 Blob)。
   * 204 (TTS 未配置/合成失败)、404、网络错误一律返回 null ——
   * 音频是增强不是依赖, 调用方拿到 null 就静默退纯文字, 不打断面试。
   */
  fetchTurnAudio: (sessionId: string, refId: string) =>
    fetchAudioBlob(
      `/interviews/${sessionId}/turns/${encodeURIComponent(refId)}/audio`,
    ),
  /** Sprint 6-3: 拉第 idx 句过渡语音 ("嗯, 我了解了" 等), 语义同上。 */
  fetchFillerAudio: (sessionId: string, idx: number) =>
    fetchAudioBlob(`/interviews/${sessionId}/fillers/${idx}/audio`),
  login: (username: string, password: string) =>
    request<LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  // Sprint 5.8: 鉴权工具端点
  getMe: () =>
    request<{ user_id: string; username: string; role: string }>("/auth/me"),
  logout: () => requestVoid("/auth/logout", { method: "POST" }),
  // HR-end endpoints (走 require_hr_user; Sprint 5.8 起 cookie 自动带,
  // auth: true 标志保留作向后兼容无实际行为)
  listJobs: () => request<JobContext[]>("/hr/jobs", { auth: true }),
  createJob: (body: {
    title: string;
    jd: string;
    requirements: string[];
    company_materials: string;
    track: Track;
    role_family: RoleFamily;            // Sprint 5.9
    question_source?: "rag" | "llm_direct";  // Sprint H: 出题来源 (默认 rag)
    aspects: ProfileAspect[];           // Sprint 5.9; HR 在表单上增删改后的最终列表
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
  // Sprint 5.9: HR 切 role_family 时拉默认 aspect 模板; 不要 auth (公开)。
  getAspectsTemplate: (roleFamily: RoleFamily) =>
    request<ProfileAspect[]>(`/jobs/aspects-template/${roleFamily}`),
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
  // 开发者测试: 不答题预览全部题目 (lazy project 题内存 resolve, 不落库)。
  // 后端双门控: HR 登录态 + DEV_PLAN_PREVIEW env; 未开启返 403。
  getPlanPreview: (jobId: string, candidateId: string) =>
    request<InterviewPlan>(
      `/hr/jobs/${jobId}/candidates/${candidateId}/plan-preview`,
      { auth: true },
    ),
  // Sprint E: HR 视角 plan (含 trace 出题过程审计; 候选人端接口无 trace)
  getHrPlan: (jobId: string, candidateId: string) =>
    request<InterviewPlan>(
      `/hr/jobs/${jobId}/candidates/${candidateId}/plan`,
      { auth: true },
    ),
  // Sprint E: 候选人 resume 在 Milvus 里的切片原文 (对照 project 题溯源)
  getResumeChunks: (candidateId: string) =>
    request<ResumeChunk[]>(
      `/hr/candidates/${candidateId}/resume-chunks`,
      { auth: true },
    ),
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
  // Sprint C: 知识库审核 endpoints (require_hr_user 同 /hr/*)
  listDatasets: () =>
    request<DatasetSummary[]>("/admin/datasets", { auth: true }),
  listDatasetChunks: (datasetId: string) =>
    request<ChunkWithDraftStats[]>(
      `/admin/datasets/${encodeURIComponent(datasetId)}/chunks`,
      { auth: true },
    ),
  getChunkDrafts: (chunkId: string) =>
    request<ChunkWithDraftsResponse>(
      `/admin/chunks/${chunkId}/drafts`,
      { auth: true },
    ),
  editDraft: (draftId: string, body: EditDraftBody) =>
    request<QuestionDraft>(`/admin/drafts/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
      auth: true,
    }),
  approveDraft: (draftId: string, body: ApproveBody) =>
    request<SeedQuestion>(`/admin/drafts/${draftId}/approve`, {
      method: "POST",
      body: JSON.stringify(body),
      auth: true,
    }),
  rejectDraft: (draftId: string) =>
    request<QuestionDraft>(`/admin/drafts/${draftId}/reject`, {
      method: "POST",
      auth: true,
    }),
  bulkApproveChunk: (chunkId: string, body: ApproveBody) =>
    request<SeedQuestion[]>(`/admin/chunks/${chunkId}/bulk-approve`, {
      method: "POST",
      body: JSON.stringify(body),
      auth: true,
    }),
  // 一键全审: POST /admin/datasets/{ds}/bulk-approve-all 走后台任务,
  // 立即返 {scheduled, to_approve_estimate}; 前端可轮询 listDatasets 看进度
  bulkApproveDatasetAll: (datasetId: string, body: ApproveBody) =>
    request<{ scheduled: boolean; to_approve_estimate?: number; reason?: string }>(
      `/admin/datasets/${encodeURIComponent(datasetId)}/bulk-approve-all`,
      {
        method: "POST",
        body: JSON.stringify(body),
        auth: true,
      },
    ),
  // Sprint upload: HR multipart 上传 md → 后台 ingest+derive+approve.
  // 立即返 {scheduled, dataset_id, n_files}; 前端轮询 listDatasets 看 chunks/drafts/seed 滚.
  // 不走 request<T>(): multipart 不能加 Content-Type=application/json header.
  uploadKnowledge: async (params: {
    dataset_id: string;
    topic: string;
    description?: string;
    category: "knowledge" | "scenario";
    role_family: string;
    competency_id: CompetencyId;
    auto_approve: boolean;
    files: File[];
  }): Promise<{
    scheduled: boolean;
    dataset_id: string;
    n_files: number;
    auto_approve: boolean;
  }> => {
    const fd = new FormData();
    fd.append("dataset_id", params.dataset_id);
    fd.append("topic", params.topic);
    fd.append("description", params.description || "");
    fd.append("category", params.category);
    fd.append("role_family", params.role_family);
    fd.append("competency_id", params.competency_id);
    fd.append("auto_approve", params.auto_approve ? "true" : "false");
    for (const f of params.files) fd.append("files", f);

    const res = await fetch(`${API_BASE}/admin/upload-knowledge`, {
      method: "POST", body: fd, credentials: "include",
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body?.detail) detail = body.detail;
      } catch { /* 非 JSON body */ }
      throw new ApiError(res.status, detail);
    }
    return res.json();
  },
};

export { API_BASE };
