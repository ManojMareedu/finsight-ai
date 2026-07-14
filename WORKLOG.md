# WORKLOG

Append-only journal of work on FinSight AI. Newest entries on top. Each entry:
what changed, why, and how it was verified. Companion to TODO.md (the backlog) and
CLAUDE.md (the standing rules).

---

## 2026-07-14 — P1-3/4/6/7/8: schema de-dup, config/doc accuracy, LICENSE, compose

**Author:** Claude (Opus 4.8), autonomous execution loop.

- **P1-3:** removed the duplicate `AnalyzeRequest`/`AnalyzeResponse` from
  `api/routes/analyze.py`; it now imports the canonical models from
  `models/schemas.py`. This also turned on request validation (`min_length=2` →
  short names 422, verified) and made the response `report` strongly typed
  (`DueDiligenceReport`; the workflow's dict output coerces cleanly, verified).
- **P1-4:** unified the model default on `meta-llama/llama-3.3-70b-instruct:free`
  across `config.py`, `.env.example`, and the README env table. Chose a concrete
  free slug over `openrouter/auto` (can bill) and the invalid `openrouter/free`.
  Confirmed the code default with `Settings(_env_file=None)`. **Flagged:** the
  user's gitignored `.env` still says `openrouter/free` — left untouched (their
  secret/runtime file) and noted in TODO P1-4 as an owner action.
- **P1-6:** corrected the `research_agent.py` docstring/comment from yfinance to
  SEC EDGAR (matching the actual `get_stock_info` implementation).
- **P1-7:** added MIT `LICENSE` referenced by the README.
- **P1-8:** README promised `docker/docker-compose.yml` + a `docker-compose up`
  Quick Start with no compose file present. Added a real root `docker-compose.yml`
  (builds the single-container root Dockerfile, ports 7860/8000, `.env`, persistent
  `chroma_data` volume) so the documented command works, and fixed the structure
  block. Validated via `docker compose config`.
- Gates after this batch: `ruff` clean, `mypy` clean (31 files), `pytest` 31 passed.

---

## 2026-07-14 — P1-2: establish real test coverage

**Author:** Claude (Opus 4.8), autonomous execution loop.

- Added 30 network-free unit tests across four files; suite went **1 → 31 passing**.
  Everything external (HTTP to EDGAR, the LLM `chat` call) is mocked, so tests are
  deterministic and CI-safe.
- Coverage targets the pure logic most likely to regress, including the tricky
  EDGAR sanity checks and the `_latest_annual` GAAP-concept-switch handling — and
  it regression-locks the P0-2 retriever behavior indirectly by pinning the data
  layer it feeds.
- Fixed lint in the new tests (import order + wrapped long lines) so
  `ruff check src tests` is clean. `mypy src` still clean.
- Files: `tests/test_data_fetchers.py`, `tests/test_llm_client.py`,
  `tests/test_risk_agent.py`, `tests/test_schemas.py`.

### Note for CI
CI currently runs `pytest` with no env. A module only needs `OPENROUTER_API_KEY`
when it constructs `Settings`; the new tests avoid instantiating `Settings`, so
they pass without secrets. (The pre-existing smoke test also needs
none.) If future tests import the API app, they'll need a dummy key — track under
P1-5/CI hardening.

---

## 2026-07-14 — Fix P0 batch (CI red, RAG path bug, Docker healthcheck) + resolve P1-1

**Author:** Claude (Opus 4.8), autonomous execution loop.

### Baseline (verified before changing anything, via `.venv/bin/python -m <tool>`)
- **Correction to the prior audit:** the venv is **not** broken for running gates.
  `mypy`, `black`, `ruff`, `pytest` all run via `python -m`. Only console-script
  shebangs are stale (old space-containing path). The venv is **Python 3.12**;
  `python3.11` is not installed, so recreating as 3.11 is impossible. Decision: do
  **not** recreate — it works and destroying it would be needless. → **P1-1 closed
  by verification**, not by rebuild.
- Gate baseline: `ruff` 6 errors (all `ragas_eval.py`); **`mypy` clean** (31 files);
  `black` would reformat 17 files (CI does not run black — not CI-breaking);
  `pytest` 1 passed.

### P0-1 — CI red (ruff) → FIXED
- Root cause: 6 lint errors isolated to `src/evaluation/ragas_eval.py`.
- Fix: `ruff --fix` cleared 4 (import sort, f-string-without-placeholder, EOF
  newline); manually wrapped the 2 E501 lines (system + user message strings) using
  parenthesized implicit string concatenation. No behavior change.
- Evidence: `ruff check src` → "All checks passed!"; `mypy` clean; `pytest` passes.

### P0-2 — RAG retrieval path bug → FIXED (highest real-world impact)
- Root cause: `retriever.py` hardcoded `persist_directory="./data/chroma"` while
  ingestion writes to `settings.chroma_persist_dir` (=`/data/chroma` in Docker/HF).
  In the container the two paths differ → retrieval reads an empty store → reports
  synthesized on zero filing context.
- Fix: `get_retriever` now reads `get_settings().chroma_persist_dir` (one line +
  docstring). Ingestion already uses the setting, so both sides now agree.
- Evidence (runtime, no network): set `CHROMA_PERSIST_DIR` to a temp dir, wrote 3
  docs via the same `chromadb.PersistentClient`/`financial_filings` collection
  ingestion uses, then `retrieve_context("...", company="Apple")` returned **3/3**
  docs. Pre-fix this path returned 0.

### P0-3 — Dockerfile healthcheck path → FIXED
- Root cause: `HEALTHCHECK` probed `/api/v1/health`; the only health route is
  `/health` (`src/api/routes/health.py`), which `start.sh` also polls.
- Fix: changed the probe URL to `/health`.
- Evidence (in-process TestClient): `/health` → 200 `{"status":"ok"}`;
  `/api/v1/health` → 404. Full `docker build` deferred (multi-minute; the change is
  a URL correction against a route proven to respond).

### Files changed
- `src/evaluation/ragas_eval.py`, `src/rag/retriever.py`, `Dockerfile`.

### Next up
P1-2 (real test coverage) — also lets the pure-logic fixes above be regression-locked.

---

## 2026-07-14 — Repository management system initialized + full audit

**Author:** Claude (Opus 4.8), at Manoj's request.

### What was done
- Created the repository management system: **CLAUDE.md**, **TODO.md**,
  **WORKLOG.md** (this file). No prior versions existed.
- Adopted an industry-standard engineering-standards + Definition of Done set in
  CLAUDE.md, because no explicit standards/DoD were supplied with the request.
  **This is flagged for the owner to confirm or replace.**
- Performed a full read-through of the codebase (all `src/` modules, tests, Docker,
  CI, config, README) and ran the quality gates that could run.

### Repository state observed
- **Architecture is sound and matches the README's high-level design:** a LangGraph
  4-agent pipeline (research → filing → risk → synthesis) with a conditional loop
  back to research when `risk_score > 0.7` and `iterations < 3`. Pydantic-enforced
  structured output, EDGAR as the data source, ChromaDB + local MiniLM embeddings,
  FastAPI + Streamlit, Langfuse tracing, Dockerized for HuggingFace Spaces.
- **Gate results:**
  - `pytest` → 1 passed (only `test_smoke.py`, `assert True` — no real coverage).
  - `ruff check src` → **6 errors, all in `src/evaluation/ragas_eval.py`** →
    **CI's lint step is currently failing on `main`.**
  - `mypy` / `black` → **could not run locally**: the `.venv` was created under a
    path containing a space (`.../Finsight AI/...` vs the real `Finsight_AI`), so
    the console-script shebangs point to a missing interpreter.
  - `fastapi`, `ruff`, `pytest` import/run fine via `.venv/bin/python`.
- **`.env` is correctly gitignored and untracked** — no secret leak in history.

### Key findings (triaged into TODO.md)
- **P0-2 (highest real-world impact):** `retriever.py` hardcodes `./data/chroma`,
  ignoring `CHROMA_PERSIST_DIR`. In Docker/HF the persist dir is `/data/chroma`, so
  ingestion and retrieval point at different stores — retrieval returns nothing and
  reports are synthesized on empty filing context.
- **P0-1:** CI red due to 6 ruff errors in `ragas_eval.py`.
- **P0-3:** Dockerfile HEALTHCHECK probes `/api/v1/health`; real route is `/health`.
- **P1:** broken local venv; ~zero test coverage; duplicated
  `AnalyzeRequest`/`AnalyzeResponse` (schemas.py vs routes/analyze.py, and they
  diverge); model-name config drift across config.py/.env.example/README; RAGAS eval
  depends on local Ollama and can't be CI-gated; stale yfinance docstring in
  research_agent; missing `LICENSE`; README structure drift.
- **P2:** stray tracked artifacts (`apple_report.pdf`, `.hf_rebuild`); CI missing
  `black --check`; two divergent ChromaDB access patterns; no retry/caching;
  roadmap features unstarted.

### Verification
- Findings confirmed by reading source directly and by running `pytest` and
  `ruff check src`. The retriever/Docker path and healthcheck-path mismatches were
  confirmed against `Dockerfile`, `start.sh`, `config.py`, and the route source.

### Decisions
- No code changed this session (analysis-only, per request).
- yfinance references are treated as stale docs to fix, **not** a regression to
  revert — the EDGAR migration is intentional.

### Next up (see TODO.md)
1. P1-1 — recreate the venv so `mypy`/`black` (and the full DoD gate) can run.
2. P0-1 — clear the 6 ruff errors to turn CI green.
3. P0-2 — fix the ChromaDB persist-path bug.
4. P0-3 — fix the Dockerfile healthcheck path.

### Open question for the owner
- Confirm or replace the engineering standards / Definition of Done drafted in
  CLAUDE.md §4–5 (none were provided with the initial request).
