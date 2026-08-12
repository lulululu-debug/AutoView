# AutoView — AI Interview Platform

> 中文版本：[`README.zh-CN.md`](./README.zh-CN.md)

Multi-agent infrastructure for AI-driven video interviews. HR registers an account and
uploads a JD / role requirements / company materials (Feishu docs supported); candidates
upload a resume. The system automatically generates an interview plan, runs multi-round
interviews with intelligent follow-ups, produces a structured evaluation report, and
supports human review by HR. Data is isolated per HR tenant — every account manages
only its own.

The end state is a two-sided ("recruiter + candidate") AI interview platform. **Video
interviewing (Tier B) is already end-to-end**: the AI interviewer has a face and asks
questions aloud in Chinese; candidates can answer by voice (transcript is editable before
submission) and their camera feed is recorded and archived. A true lip-sync avatar (Tier A)
and multimodal evaluation are planned for later sprints. **Text Q&A is the permanent
fallback** — if any media stage fails, the interview degrades gracefully and never breaks.

> Architecture and compliance constraints: [`ARCHITECTURE.md`](./ARCHITECTURE.md).
> Roadmap and milestones: [`sprint.md`](./sprint.md).
> Contribution rules for this repo: [`CLAUDE.md`](./CLAUDE.md).
> Evaluation system (structural guardrails + real-LLM effectiveness eval) and results:
> [`EVALUATION.md`](./EVALUATION.md).
> Agent capability upgrades (the 8.x series), proposals and reviews:
> [`AGENT_UPGRADES.md`](./AGENT_UPGRADES.md).

---

## Highlights

### Interview core
- **Multi-agent orchestration**: Planner / Interviewer / Assessor / Evaluator / Analyzer
  each own one job; agents never call each other — the Orchestrator is the sole router.
- **Two interview tracks**: `campus` (knowledge-heavy) and `lateral` (project- and
  scenario-heavy). The track drives stage quotas without leaking into agent internals.
- **Staged progression**: `self_intro → knowledge → project → scenario`. Project questions
  use **lazy generation** — after the self-intro is captured, they are generated live from
  resume RAG, prioritizing the open doubts accumulated in the CandidateModel.
- **Online Assessor**: every answer yields a structured `AnswerAssessment`; on LLM failure
  it always degrades to a heuristic, so both paths coexist permanently. The real-LLM path
  carries a **Platt-calibrated score** (P(signal sufficient), fit on 50 hand-labeled golds).
- **Calibrated, belief-driven follow-ups** (Sprint 8.3): follow-up decisions are made in
  calibrated-probability space, layered with a global follow-up budget and a competency
  belief-variance gate (dimensions with enough evidence and a well-established mean stop
  consuming budget). The heuristic path and the config-driven FollowUpPolicy /
  CompletionPolicy stay unchanged.
- **Cross-stage candidate memory** (Sprint 8.4): strengths/concerns settle into
  `SkillClaim`s backed by verbatim evidence (verified / doubted / contradicted);
  contradictions are flagged by deterministic rules, clarified via follow-ups, and honestly
  surfaced in the summary — never adjudicated in HR's place.
- **Rubric-based scoring** (Sprint 8.5): each question gets a 3–6 item binary checklist at
  plan time; the Assessor scores each item, and per-question quality =
  0.7 × sufficiency + 0.3 × hit-rate. A multi-judge panel is code-complete but **off by
  default** (not enabled until the MAE calibration gate passes).
- **No dynamic question injection** — the question set is fixed once by plan + lazy project
  generation (reproducibility + fairness).

### Security & compliance
- **Registration and multi-tenant isolation** (Sprint 6.8): HR self-service registration
  (optional invite code); jobs / candidates / reports are isolated per owner, cross-tenant
  access always returns 404; admin sees everything.
- **Input injection defense** (Sprint 8.1): candidate text is composed into prompts via
  `wrap_untrusted` (hash-derived nonce boundaries) plus an "instructions are void"
  declaration; resumes/answers are stripped of invisible characters; anomalies only set
  `integrity_flags` for human review — they **do not block** (avoiding over-defense).
- **Decision trace + deterministic replay** (Sprint 8.2): every follow-up / stop / evaluation
  decision — with the actual threshold values used — is fully persisted to `decision_traces`
  (EU AI Act Art. 12 "decisions reconstructible"); `REPLAY_MODE` replays by `request_hash`,
  and golden-trace diffs give zero-token regression coverage.
- **Compliance partitioning baked into the schema**: `overall` is derived only from
  `content_scores`; soft signals, assessment numbers, beliefs, memory, and traces are all
  internal data and never appear in HR-facing report details.

### Media & data
- **Video media layer (pure adapters)**: consent gate → TTS narration (Volcengine / Azure,
  routed by region) → three-state video avatar → streaming STT voice answers (WS proxy;
  vendor keys never leave the backend) → camera recording + archival (**record only, never
  judge**).
- **RAG question bank + document retrieval**: two collections (questions + documents);
  Milvus Lite (single file) for dev/eval, **Milvus standalone** (docker, Strong-consistency
  reads) for multi-process/production. Postgres is the source of truth; Milvus is rebuildable
  at any time.
- **Knowledge pipeline**: markdown corpus upload / **Feishu wiki·docx import** (Sprint 6.7,
  credentials configurable from the frontend, stored Fernet-encrypted) → chunk & ingest →
  LLM reverse question generation → HR review into the bank.
- **Deep resume parsing**: semantic segmentation + image OCR + PDF/DOCX upload; the Planner
  generates questions via topic matching + skill extraction.

### Concurrency & async (Sprint 9)
- **RQ task queue**: post-upload chunking + question generation run on a separate worker
  process with backoff retries; if the queue is unavailable it falls back to BackgroundTasks.
- **Concurrency handling**: per-session Redis locks (concurrent same-session → 409), env-tunable
  PG connection pool / threadpool; measured 30 concurrent interviews at 30/30 with zero
  failures and zero cross-talk, app-layer turn latency p50 18ms, throughput ~367 turns/s
  (stub load-test basis, excludes LLM latency; see the 2026-07-30 erratum in EVALUATION.md).

### Quality system
- **Two physically separated evaluation suites**: `evals/` — 590 structural guardrails
  (forced stub, zero token) + `sim/` — real-LLM effectiveness eval (persona simulation /
  adversarial / fairness perturbation / LLM-as-judge gold calibration / RAG metrics / frozen
  regression baselines / LLM-layer concurrency load tests), 19 reproducible methods. See
  EVALUATION.md.

---

## Tech stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.11+ |
| Data contracts | pydantic v2 |
| LLM / Embedding | OpenAI (`gpt-4o-mini` + `text-embedding-3-small`, single provider) |
| Relational storage | PostgreSQL + SQLAlchemy 2.0 + psycopg3 (env-tunable pool) |
| Hot store / cache / lock | Redis (session state + LLM/embedding/TTS cache + session locks + queue broker) |
| Task queue | RQ (separate worker, off by default, enable via `JOBS_QUEUE_ENABLED`) |
| Vector search | Milvus Lite (dev/eval, single process) / Milvus standalone (docker, multi-process) |
| HTTP API | FastAPI + uvicorn |
| Auth | JWT (HS256) + httpOnly cookie + bcrypt; optional registration invite code |
| External doc source | Feishu OpenAPI (tenant/user token dual mode, Fernet-encrypted credentials) |
| Realtime media | TTS / streaming STT routed by region (Volcengine / Azure) · WS transcription proxy · MediaRecorder capture |
| Avatar | Tier B three-state video loop (`web/public/avatar/`) → Tier A true lip-sync (planned) |
| Candidate / HR frontend | Next.js 16 + React 19 + Tailwind 4 (`web/`) |
| Test / eval | stdlib `unittest` (`evals/`, forced stub) + `sim/` (real-LLM effectiveness eval) |

---

## Quick start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Start local external services

```bash
brew services start postgresql
brew services start redis
createdb interview          # application DB
createdb interview_test     # eval-only, protects dev data from accidental wipes

# Optional (needed for multi-process / high concurrency): standalone Milvus
docker compose up -d milvus
```

### 3. Configure `.env`

Copy `.env.example` to `.env` and fill in as needed:

- `OPENAI_API_KEY` — leave empty to run on stubs; the skeleton still runs (no real LLM calls)
- `POSTGRES_URL` / `TEST_POSTGRES_URL` / `REDIS_URL` / `MILVUS_LITE_URI`
- `JWT_SECRET` — for prod use `openssl rand -hex 32`
- `MILVUS_SERVER_URI` — takes priority over lite when set (requires docker Milvus; the two
  data stores are independent, so re-seed the question bank after switching)
- `JOBS_QUEUE_ENABLED` — enabling the queue requires a separate worker: `python -m src.jobs.worker`
- `REGISTER_INVITE_CODE` — if set, an invite code is required to register; empty = open registration
- **Media (optional)**: `TTS_PROVIDER` / `STT_PROVIDER` + Volcengine/Azure keys,
  `MEDIA_STORAGE_DIR` — none configured = text-only interview; **Feishu (optional)**:
  `FEISHU_APP_ID/SECRET` or configure directly in the HR frontend

Each variable's semantics and failure-fallback behavior are documented in the `.env.example`
comments. Restart uvicorn/worker after editing `.env`.

### 4. Start the services

```bash
uvicorn api.main:app --reload          # API; see OpenAPI at /docs
python -m src.jobs.worker              # optional: RQ worker (when the queue is on)
cd web && npm install && npm run dev   # frontend at http://localhost:3000
```

`python -m src.main` still runs the hard-coded-input skeleton demo (requires PG + Redis).

### 5. Seed data & ops scripts

```bash
python -m scripts.seed_users           # default HR account (or register at /hr/register)
python -m scripts.seed_questions       # seed the question bank into Milvus
python -m scripts.ingest_md_corpus     # chunk & ingest corpus/ markdown (knowledge pipeline)
python -m scripts.derive_questions     # reverse question generation → HR review queue
python -m scripts.backfill_job_owner   # migrate ownership of existing jobs (6.8 isolation)
python -m scripts.cleanup_recordings   # recording retention cleanup (cron recommended, 90 days default)
python -m scripts.load_test            # concurrency load test (stub, zero token)
python -m scripts.record_golden_traces # record golden traces (after prompt/policy changes)
python -m scripts.freeze_persona_answers  # re-record sim frozen baseline (after question changes)
python -m scripts.review_stats         # HR review throughput stats
```

### 6. Avatar assets (optional)

`web/public/avatar/{idle,talking,thinking}.mp4` — three clips of the same person switched by
state; generation and ffmpeg processing conventions are in
[`web/public/avatar/README.md`](./web/public/avatar/README.md). Missing assets fall back to a
placeholder panel without affecting the interview.

---

## Project layout

```
src/
  schemas/        all pydantic data contracts (agent I/O types)
  llm/            sole OpenAI Chat call site; stub fallback; sanitize (injection defense, 8.1)
  embeddings/     sole OpenAI Embeddings call site; zero-vector stub
  agents/
    planner/      JD + Resume + RAG → InterviewPlan (track/lazy/rubric/topic matching)
    interviewer/  next question / follow-up / stop; calibrated + belief-gated decisions (8.3)
    assessor/     per-question online eval + Platt calibration (calibration.py); degrades to heuristic
    evaluator/    EvaluationReport (rubric hit-rate scores; panel off by default, 8.5)
    analyzer/     multimodal analysis placeholder (Sprint 7)
  orchestrator/   agent routing + Redis state machine + session locks + PG archival + trace orchestration
  trace/          decision-trace collection (contextvars) + deterministic replay + diff (8.2)
  beliefs.py      Gaussian-conjugate updates of competency beliefs (8.3)
  candidate_model.py  cross-stage candidate memory: SkillClaim settle/contradict/reflect (8.4)
  coverage.py     competency_coverage / richness computation (consumed by CompletionPolicy)
  jobs/           RQ task queue: enqueue / tasks / worker entry (Sprint 9)
  db/             Postgres lazy connect + ORM + repository
  cache/          Redis: session hot store + LLM/embedding/TTS cache + trace hot store + session locks
  vector_store/   Milvus: questions + documents; lite/server dual mode (Sprint 9)
  connectors/     Feishu OpenAPI (wiki/docx fetch, encrypted credentials, 6.7)
  auth/           JWT + bcrypt + httpOnly cookie auth (cookie preferred, Bearer fallback)
  tts/ stt/       sole voice-synthesis / streaming-transcription call sites (auto text fallback)
  media_store/    interview recording archival (record only, never judge)
  knowledge_pipeline/ + derivation/   markdown corpus parse + reverse question gen (offline pipeline)
  ingestion/      document chunking + vectorization + resume semantic segmentation
  resume_parser.py  PDF / DOCX / image (OCR) resume parsing + sanitization
  main.py         hard-coded-input skeleton demo

api/
  main.py         FastAPI factory: CORS / exception mapping / threadpool capacity
  routes/         auth · jobs · candidates · interviews · media · hr
                  · admin_upload · admin_drafts · admin_feishu
  schemas.py      API request/response DTOs

web/              Next.js 16 candidate portal + HR Dashboard (register/login/jobs/review/admin)
sim/              real-LLM effectiveness eval: persona sim / adversarial / fairness / judge /
                  calibration (assessor·platt·judges·evaluator) / RAG metrics / frozen baselines
evals/            stdlib unittest structural guardrails (590 cases, forced stub, zero external deps in CI)
scripts/          ops scripts (see list above)
docker-compose.yml + deploy/   Milvus standalone (Sprint 9)
corpus/           markdown corpus for the knowledge pipeline
```

---

## API at a glance

The authoritative request/response shapes for every route are at `/docs`.

| Module | Routes | Notes |
| --- | --- | --- |
| Auth | `POST /auth/register` · `POST /auth/login` · `GET /auth/me` · `POST /auth/logout` | Register (optional invite code) + httpOnly cookie auth |
| Jobs | `GET/POST /jobs` · `GET /jobs/{id}` | Creating a job requires login; owned by its creator (6.8) |
| Candidates | `POST /jobs/{id}/candidates` · `.../parse-resume` · `GET .../plan` | Resume parse + sanitize + trigger Planner (queue or BG) |
| Interviews | `POST /interviews` · `POST /interviews/{id}/answers` · `GET /interviews/{id}` · `POST .../finalize` · `GET .../report` | Three-phase session + session lock (concurrent 409) + resume-on-interrupt |
| Media | `GET /media/config` · turn/filler audio · `WS .../transcribe` · `POST .../recordings` | TTS / transcription proxy / recording archival |
| HR | `GET /hr/jobs` · `GET /hr/sessions/{id}` · `GET /hr/sessions/{id}/trace` · `GET /hr/reports/{id}` · `PATCH .../review` | Isolated per owner; trace is an audit export (not in the UI) |
| Admin | `POST /admin/upload-knowledge` · drafts review · `/admin/feishu/*` | Corpus upload / reverse-gen review / Feishu import & credential config |

---

## Testing & evaluation

```bash
python -m unittest discover -s evals               # all structural guardrails (zero token)
python -m unittest evals.test_trace_replay         # golden-trace decision regression
python -m sim.run_interviews --personas all --frozen --run-dir sim/runs/xxx
                                                   # frozen baseline batch (real LLM)
```

Conventions:

- `evals/` forces the LLM stub (clears `OPENAI_API_KEY`); cases needing PG+Redis auto-skip
  when the env is missing; they always use `TEST_POSTGRES_URL` and **never** touch the dev DB.
- **Changing any prompt / policy threshold = a two-gate check**: golden-trace diff (turning
  red is the expected signal — re-record after confirming) + the corresponding calibration.
  Changing the Assessor mandates re-running `sim/calibrate_assessor`.
- Effectiveness eval (real LLM, burns tokens) lives in `sim/`, physically separated from the
  structural guardrails; methods, metrics, and historical results are in
  [`EVALUATION.md`](./EVALUATION.md).

---

## Key design constraints (read before writing code)

- Agents communicate only through the Orchestrator; LLM/embedding calls always go through
  `src/llm` / `src/embeddings`.
- Every new LLM call carries a timeout + deterministic fallback; the heuristic safety net is
  never removed.
- **Internal data stays internal**: `AnswerAssessment` numbers / beliefs / CandidateModel /
  traces / `integrity_flags` never appear in HR-facing report details, and candidates never
  see their own report.
- **No dynamic question injection**; multimodal soft signals are never the sole basis for
  automatic rejection (see ARCHITECTURE §7).
- **The media layer is pure adapters**: any stage failing degrades to text Q&A; recordings
  are recorded, never judged; candidates can proofread the transcript — the textarea is the
  single source of truth.
- Cross-tenant access always returns 404 (anti-enumeration); data is isolated per owner.
- **The multi-judge panel must not be enabled until the MAE calibration gate passes**
  (`EVAL_PANEL_ENABLED` off by default).

---

## Progress

- ✅ Sprint 0–5.9 — skeleton → persistence → API → RAG → candidate portal → HR Dashboard →
  dual track/stage → Assessor → CompletionPolicy → calibration
- ✅ Lettered sprint series — knowledge pipeline / resume segmentation + image OCR / Planner topic matching
- 🔨 Sprint 6 — video interviewing (5/6; Tier A true lip-sync TBD)
- ✅ Sprint 6.5 — effectiveness eval system (sim/ + 8 categories of quality-defect fixes)
- ✅ Sprint 6.7 — Feishu doc import (credentials configurable from the frontend)
- ✅ Sprint 6.8 — HR registration + per-owner data isolation
- ✅ Sprint 8.1–8.5 — agent capability upgrades: injection defense / decision trace+replay /
  calibrated + belief-driven follow-ups / CandidateModel memory / rubric scoring + judge panel (off by default)
- ✅ Sprint 8.3.1 — sim frozen regression baseline ("real-LLM golden")
- ✅ Sprint 9 — RQ async queue + Milvus standalone + session locks + concurrency load test
- ⏳ Sprint 7 — multimodal evaluation (with compliance guardrails) · deployment

Full task list in [`sprint.md`](./sprint.md).
</content>
</invoke>
