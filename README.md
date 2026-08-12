# AutoView — AI Interview Platform

> 中文版本：[`README.zh-CN.md`](./README.zh-CN.md)

Multi-agent infrastructure for AI-driven video interviews. HR uploads a JD and company
materials; a candidate uploads a resume. AutoView then plans the interview, runs it over
multiple rounds with adaptive follow-ups, and returns a structured, reviewable evaluation
report — end to end.

Video interviewing works today: the AI interviewer has a face and asks questions aloud,
candidates answer by voice (transcript editable before submit), and the camera feed is
recorded. **Text Q&A is the permanent fallback** — if any media stage fails, the interview
degrades gracefully instead of breaking.

<p align="center">
  <img src="./docs/screenshots/interview.png" alt="Live video interview — AI avatar interviewer asking a question, candidate camera feed, and live voice transcription" width="720">
  <br>
  <em>Live interview: AI avatar interviewer asks aloud while the candidate answers by voice with live transcription.</em>
</p>

**Deep dives:** [Architecture](./ARCHITECTURE.md) · [Evaluation](./EVALUATION.md) ·
[Roadmap](./sprint.md) · [Agent upgrades](./AGENT_UPGRADES.md)

---

## How it works

```
JD + Resume ──▶ Planner ──▶ Interviewer ⇄ Assessor ──▶ Evaluator ──▶ Report ──▶ HR review
                              (per answer)                (content vs. soft signals)
```

Five agents, each with one job, wired together by a single **Orchestrator** — agents never
call each other. The interview advances through stages (`self_intro → knowledge → project →
scenario`), and project questions are generated live from resume RAG once the candidate has
introduced themselves, rather than guessed up front.

## Features

- **Adaptive follow-ups.** Each answer gets a structured assessment with a Platt-calibrated
  sufficiency score. Follow-up decisions run in calibrated-probability space against a global
  budget and per-competency belief gates, so the interview probes where evidence is thin and
  moves on where it isn't. Every LLM call degrades to a heuristic on failure.
- **Reproducible & fair.** The question set is fixed once at plan time — no dynamic question
  injection mid-interview. `overall` is derived only from content scores; soft signals,
  beliefs, and candidate memory are internal and never leak into the report or the score.
- **Auditable decisions.** Every follow-up / stop / evaluation decision is persisted with the
  actual thresholds used and can be replayed deterministically — golden-trace diffs give
  zero-token regression coverage (EU AI Act Art. 12).
- **Video media as pure adapters.** Consent gate → TTS narration → three-state avatar →
  streaming STT → recording. Vendor keys stay server-side; recordings are stored, never
  scored.
- **Retrieval-backed content.** A Milvus-backed question bank and document store, fed by a
  knowledge pipeline (markdown corpus + Feishu doc import → chunk → reverse question
  generation → HR review). Postgres is the source of truth; the vector store is rebuildable.
- **Multi-tenant & hardened.** HR self-registration, per-owner isolation (cross-tenant access
  returns 404), and prompt-injection defenses on all candidate-supplied text.
- **Async & concurrent.** Post-upload work runs on an RQ worker; per-session Redis locks
  prevent cross-talk. Measured 30 concurrent interviews at 30/30, p50 turn latency 18ms.

## Quality system

Two physically separated suites: `evals/` — 590 structural guardrails running on stubs (zero
token, CI-safe) — and `sim/` — real-LLM effectiveness eval (persona simulation, adversarial &
fairness probes, LLM-as-judge gold calibration, RAG metrics, frozen regression baselines).
Changing any prompt or policy threshold requires a golden-trace diff plus the matching
calibration run. Details in [`EVALUATION.md`](./EVALUATION.md).

---

## Tech stack

| Layer | Choice |
| --- | --- |
| Backend | Python 3.11+ · FastAPI · pydantic v2 |
| Frontend | Next.js 16 · React 19 · Tailwind 4 |
| LLM / Embedding | OpenAI (`gpt-4o-mini` + `text-embedding-3-small`) |
| Storage | PostgreSQL (source of truth) · Redis (state, cache, locks) · Milvus (vectors) |
| Async | RQ worker (optional) |
| Auth | JWT + httpOnly cookie + bcrypt |
| Media | TTS / streaming STT routed by region (Volcengine / Azure) · MediaRecorder |

---

## Quick start

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# 2. Local services
brew services start postgresql redis
createdb interview && createdb interview_test
docker compose up -d milvus          # optional: standalone Milvus for concurrency

# 3. Configure — copy .env.example to .env and fill in as needed.
#    OPENAI_API_KEY can be left empty; the skeleton runs on stubs with no real LLM calls.

# 4. Run
uvicorn api.main:app --reload        # API + OpenAPI docs at /docs
cd web && npm install && npm run dev # frontend at http://localhost:3000
```

**Optional:** `python -m src.jobs.worker` (RQ worker when `JOBS_QUEUE_ENABLED=1`) · media
env vars (`TTS_PROVIDER` / `STT_PROVIDER` + provider keys) enable voice; none configured =
text-only. Every variable's semantics and fallback behavior are documented in `.env.example`.

Seed and ops scripts (`seed_questions`, `ingest_md_corpus`, `derive_questions`,
`record_golden_traces`, …) live in `scripts/` — each has usage in its header comment.

---

## Project layout

```
src/
  schemas/        pydantic data contracts (all agent I/O types)
  llm/ embeddings/ sole OpenAI call sites; stub fallback; injection sanitize
  agents/         planner · interviewer · assessor · evaluator · analyzer
  orchestrator/   agent routing + Redis state machine + session locks + PG archival
  trace/          decision-trace collection + deterministic replay
  vector_store/   Milvus: questions + documents (lite / standalone dual mode)
  knowledge_pipeline/ + derivation/   corpus parse + reverse question generation
  db/ cache/ auth/ tts/ stt/ media_store/ connectors/   infra & adapters
api/              FastAPI: HTTP entry, validation, exception mapping
web/              Next.js candidate portal + HR dashboard
evals/            structural guardrails (stub, zero token)
sim/              real-LLM effectiveness eval
scripts/          seed & ops scripts
```

## Design principles

- Agents talk only through the Orchestrator; all LLM/embedding calls go through `src/llm` /
  `src/embeddings`, and every LLM call carries a timeout + deterministic fallback.
- Internal signals (assessment numbers, beliefs, candidate memory, traces) never reach HR
  detail views or candidates; candidates never see their own report.
- The media layer is pure adapters — any stage failing degrades to text; the editable
  transcript is the single source of truth.

## Roadmap

- ✅ Core loop → persistence → API → RAG → candidate portal → HR dashboard
- ✅ Dual tracks & stages · online Assessor · completion policy · calibration
- ✅ Agent upgrades: injection defense · decision trace + replay · calibrated belief-driven
  follow-ups · cross-stage candidate memory · rubric scoring
- ✅ Async queue + standalone Milvus + session locks + concurrency load test
- 🔨 Video interviewing (true lip-sync avatar TBD)
- ⏳ Multimodal evaluation (with compliance guardrails) · deployment

Full task list in [`sprint.md`](./sprint.md).
</content>
