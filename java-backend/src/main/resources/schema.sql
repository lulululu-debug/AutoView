-- S1 建表脚本（幂等）。生产化应改用 Flyway/Liquibase 做版本化迁移。
-- 沿用 Python 侧取舍：嵌套结构走 jsonb，可查询字段提列。

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL,                 -- hr / admin
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,                 -- 所有权锚点
    title         TEXT,
    jd            TEXT,
    role_family   TEXT,
    track         TEXT,                          -- campus / lateral
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs (owner_user_id);

CREATE TABLE IF NOT EXISTS candidates (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL,
    name         TEXT,
    resume_text  TEXT,
    content_hash TEXT,                           -- 幂等键（候选人+简历内容）
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_candidates_job ON candidates (job_id);

CREATE TABLE IF NOT EXISTS interview_plans (
    id            TEXT PRIMARY KEY,
    candidate_id  TEXT NOT NULL,
    job_id        TEXT,
    content_hash  TEXT,                          -- 候选人+简历内容 hash：消费幂等键
    rounds        JSONB,
    competencies  JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 兼容旧库:CREATE TABLE IF NOT EXISTS 不会给已存在的表补列，显式补
ALTER TABLE interview_plans ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_plans_candidate ON interview_plans (candidate_id);
CREATE INDEX IF NOT EXISTS idx_plans_cand_hash ON interview_plans (candidate_id, content_hash);

CREATE TABLE IF NOT EXISTS interview_sessions (
    session_id      TEXT PRIMARY KEY,
    plan_id         TEXT,
    job_id          TEXT,
    candidate_id    TEXT,
    status          TEXT NOT NULL,               -- IN_PROGRESS / COMPLETED
    history         JSONB,
    answers         JSONB,
    assessments     JSONB,
    beliefs         JSONB,
    candidate_model JSONB,
    intro_text      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_plan   ON interview_sessions (plan_id);
CREATE INDEX IF NOT EXISTS idx_sessions_job    ON interview_sessions (job_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON interview_sessions (status);

CREATE TABLE IF NOT EXISTS evaluation_reports (
    session_id               TEXT PRIMARY KEY,   -- 唯一：一场面试一份报告
    overall                  DOUBLE PRECISION,
    needs_human_review       BOOLEAN NOT NULL DEFAULT TRUE,
    content_scores           JSONB,
    performance_observations JSONB,
    competency_coverage      JSONB,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS seed_questions (
    id          TEXT PRIMARY KEY,
    role_family TEXT,
    competency  TEXT,
    category    TEXT,                            -- KNOWLEDGE / PROJECT_EXPERIENCE / SELF_INTRO / SCENARIO
    dataset_id  TEXT,
    text        TEXT,
    rubric      JSONB,
    difficulty  TEXT
);
CREATE INDEX IF NOT EXISTS idx_seed_role  ON seed_questions (role_family);
CREATE INDEX IF NOT EXISTS idx_seed_comp  ON seed_questions (competency);
CREATE INDEX IF NOT EXISTS idx_seed_cat   ON seed_questions (category);
CREATE INDEX IF NOT EXISTS idx_seed_ds    ON seed_questions (dataset_id);
