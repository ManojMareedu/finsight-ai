# WORKLOG

Append-only journal of work on FinSight AI. Newest entries on top. Each entry:
what changed, why, and how it was verified. Companion to TODO.md (the backlog) and
ENGINEERING_GUIDE.md (the standing rules).

---

## 2026-07-15 — Fix CI test collection (pythonpath)

**Author:** Manoj Mareedu.

GitHub Actions ran `pytest` from the repo root with no `PYTHONPATH`, so every test
module importing `src.*` failed to collect (`ModuleNotFoundError: No module named
'src'` × 5 modules) and the CI test step was red — masked locally because local
runs export `PYTHONPATH=$(pwd)`. Fix: add `pythonpath = ["."]` to
`[tool.pytest.ini_options]` so collection works everywhere without env setup.
Verified by running `pytest` with `PYTHONPATH` unset (42 passed); CI now green.

---

## 2026-07-15 — v1.0.0 release preparation

**Author:** Manoj Mareedu. Final release pass — no new features; docs,
evidence, and release readiness only.

### Verified (fresh, no assumptions)
ruff / black / mypy clean; 42 tests pass. OpenRouter account is now **beyond
free-tier** (`is_free_tier: false`, ~1,000 req/day) — so the final benchmark ran
RAGAS on **all 10** questions, not a cap. `git ls-remote origin` works (osxkeychain
credential); `gh` not installed (release handled via git tag + provided command).

### Final benchmark (OpenRouter `gpt-oss-20b:free`, N=10, all RAGAS-scored)
`evaluation/results/benchmark_latest.md`:
Precision@8 **1.00**, Recall **0.79**, Success **10/10**, latency mean/p95
**17.4s / 33.4s**, Faithfulness **0.93**, Answer Relevancy **0.73**,
Context Recall 0.30, Context Precision `null`. Quality **improved** vs the earlier
N=3 run (faithfulness 0.83→0.93, answer-relevancy 0.54→0.73) — no regression.
Context recall/precision are depressed by the structured-vs-narrative data model
(numeric answers live in XBRL, not the 10-K text) + RAGAS parse fragility, both
documented, not a retrieval fault.

### Created / updated
- `docs/ENGINEERING_DECISIONS.md` — interview-grade rationale for every major
  decision (problem / alternatives / choice / tradeoffs / evidence / why).
- `docs/RELEASE_CHECKLIST.md` — status, limitations, tradeoffs, roadmap,
  reproducibility, verification checklist, benchmark + deployment summary.
- ENGINEERING_GUIDE.md — OpenRouter primary provider + quota note, Ollama optional fallback,
  DoD extended with benchmark/docker gates.
- README — Documentation section, `src/evaluation/` + `docs/` in the structure
  tree, refreshed benchmark headline.

### Audit (clean)
No broken internal links; all `make` targets exist; README structure paths exist;
no dead code / TODO-FIXME in `src`; deps match pins (`pip check` OK).

---

## 2026-07-15 — Final engineering loop: comprehensive benchmark + evidence

**Author:** Manoj Mareedu. Goal: production-quality repo with objective,
reproducible evidence. No new product features.

### Verified current state (all green)
ruff / black / mypy clean; 42 tests pass; `pip check` OK; deps match pins
(`langchain-core 0.3.63`); no dead code (`ruff --select F`), no TODO/FIXME in
`src`, no duplicated schemas, `.env` untracked, no leaked secrets, LICENSE present.
FastAPI `/health` → 200, `/docs` → 200, OpenAPI paths `[/health, /analyze]`.
HF frontmatter correct (`sdk: docker`, `app_port: 7860`).

**Docker verified (started the daemon):** both `docker/Dockerfile.ui` (CI) and the
root `Dockerfile` (HF deploy — bakes the embedding model) build clean; the app
imports inside the image; running the root image serves `/health` →
`{"status":"ok"}` and Docker `HEALTHCHECK` reports `healthy` (the earlier
`/api/v1/health`→`/health` fix confirmed in a real container). Test images removed
afterward.

### Added — comprehensive benchmark (`src/evaluation/benchmark.py`, `make benchmark`)
Deterministic metrics (no LLM, cover **all** questions): retrieval precision@k,
retrieval recall, latency mean/p95, success rate. RAGAS metrics (capped, judge):
faithfulness, answer relevancy, context precision/recall — NaN-tolerant (a metric
the judge can't score is `null`, not a crash or a zero). Writes timestamped JSON +
Markdown to `evaluation/results/` with per-metric documentation (what / why /
acceptable / limitations). 9 new network-free tests.

### Benchmark result — strongest free judge (`openai/gpt-oss-20b:free`, N=10)
`evaluation/results/benchmark_latest.md`:

| Metric | Value |
|---|---|
| Retrieval Precision@8 | **1.0000** |
| Retrieval Recall | 0.7942 |
| Success Rate | 1.0000 (10/10) |
| Latency total mean / p95 | 17.57s / 50.57s |
| Latency retrieval mean | 0.72s |
| RAGAS Faithfulness | 0.8333 |
| RAGAS Answer Relevancy | 0.5372 |
| RAGAS Context Recall | 0.3333 |
| RAGAS Context Precision | null (parse-fragile on free judge) |

Note: a local-gemma benchmark run was abandoned — RAGAS at k=8 on gemma is
~130s/iteration (impractical); gpt-oss (hosted) completes in ~10s/iteration and is
the stronger judge. Deterministic retrieval metrics are judge-independent.

### Docs synced to implementation
ENGINEERING_GUIDE.md (§2 similarity k=8 + benchmark module; §3 make targets; §6 RAGAS/pinning
resolution; removed stale README-drift note). README: new "Evaluation & Benchmarks"
section (before/after table, reproduce steps, latest-run headline, honest caveats);
corrected stale "tests are minimal" constraint. `make benchmark` target added.

---

## 2026-07-15 — Retrieval-quality optimization (benchmark-validated)

**Author:** Manoj Mareedu. Scope: retrieval quality only (chunking, metadata,
retrieval params, prompt grounding) — no new features. Every change validated by
objective benchmark metrics; non-improving changes reverted.

### Measurement approach
The free-tier judge (`gpt-oss-20b`) is capped at 50 req/day — one RAGAS run
exhausts it — and RAGAS `context_precision` is unreliable (parse fails even on
gpt-oss; gemma floors it to 0.0). So iteration used a **deterministic, LLM-free
retrieval diagnostic** over all 10 golden items (clean, unlimited, repeatable):
- `company_precision@k`: fraction of retrieved chunks from the correct company
  (measures cross-company contamination).
- `gt_keyword_recall`: fraction of ground-truth content words present in retrieved
  chunks (qualitative-recall proxy).
The winning config was then confirmed with the RAGAS pipeline (gemma, same judge,
N=3) on the metrics gemma computes reliably (faithfulness, context_recall).
(`answer_hit_rate` on exact figures was dropped: the numbers — "$383.3B", "44.1%"
— are **not in the 10-K text at all**, even the full 393k-char doc; 10-Ks report
them via XBRL, which the system already fetches through `get_financials_from_edgar`.
RAG retrieval is inherently for qualitative content.)

### Changes kept (each improves the objective metrics)
1. **Retrieval params** (`retriever.py`): MMR `k=6, fetch_k=20, lambda_mult=0.7`
   → **similarity `k=8`**. A full param sweep showed MMR diversity (λ<1) *lowered*
   both precision and recall; pure relevance at k=8 was best.
2. **Ingestion size** (`ingestion.py`): `max_chars` 50k → **150k**. The first ~50k
   chars stop inside the business/risk sections, so MD&A and later content were
   never retrievable. (chunk_size/overlap kept at 1000/200.)

### Reverted (measured, did NOT improve → not applied)
- Smaller chunks (500/100, 700/150): both metrics dropped (e.g. 500 → comp_prec
  0.875, kw_recall 0.59). Kept 1000/200.
- `max_chars` 300k: only +0.03 recall over 150k, with lower precision (0.9875 vs
  1.0) and 2× the store/ingest cost. Kept 150k.

### Before / after
Deterministic retrieval metrics (10 items, the reliable signal):

| metric | before | after |
|---|---|---|
| company_precision@k | 0.8167 | **0.9625** |
| gt_keyword_recall | 0.6081 | **0.7942** |

RAGAS pipeline (gemma judge, N=3, same judge before/after):

| metric | before | after | note |
|---|---|---|---|
| faithfulness | 0.8333 | **1.0000** | ↑ better-grounded answers |
| context_recall | 0.3333 | 0.3333 | flat (small-N, noisy) |
| context_precision | 0.0000 | **0.1111** | ↑ off the floor — gemma is unreliable here but the direction matches the deterministic gain |
| answer_relevancy | 0.4618 | 0.1969 | gemma noise (0.20–0.46 across same-config runs); not a retrieval metric |

(`latest.json` holds the full optimized-config run: faithfulness 1.0,
context_precision 0.1111, context_recall 0.3333, answer_relevancy 0.1969.)

### Notes / follow-ups
- Re-ingested the main store at 150k (Apple 190 / Microsoft 189 / Tesla 189 chunks,
  was ~64 each).
- Authoritative RAGAS before/after with the reliable `gpt-oss-20b` judge is
  deferred until the free daily quota resets (TODO P1-5d) — today's quota was spent
  confirming the baseline. Gates: ruff/black/mypy clean, 33 tests pass.

---

## 2026-07-14 — RAGAS all-NaN: root-cause analysis + fix + successful rerun

**Author:** Manoj Mareedu. Task: investigate why RAGAS returned NaN for every
metric, as a pipeline failure. Verified each stage with runtime evidence.

### Evidence gathered (per stage)
- **Dataset construction:** legacy columns (`question/answer/contexts/ground_truth`)
  — verified RAGAS 0.2.6 *does* accept them (metrics reached computation), so not
  the cause.
- **Retrieval:** ChromaDB populated (Apple 64 / Microsoft 64 / Tesla 63 chunks);
  `collect_eval_data` retrieved contexts fine (Collected 10/10).
- **Answer generation:** worked (via OpenRouter, then local).
- **Judge model:** the original run used local Ollama `llama3.2` — **not running**
  (`:11434` refused) → all NaN.
- **Env:** `langchain-core` had drifted to **1.2.23** and `langchain-ollama` to
  **1.0.1** (unpinned; `langchain-ollama 1.x` pulled core 1.x), which is
  incompatible with `langchain-openai 0.2.9`/`ragas 0.2.6`. Proven: `ChatOpenAI`
  raised `OpenAIError: api_key must be set` at construction under core 1.x; fine
  after repair.
- **Judge output/API errors:** with `raise_exceptions=True`, RAGAS threw
  `ValidationError: Verification missing reason/verdict`, input
  `{'text': '{"reason":...,"verdict":1}'}` — the judge's JSON wasn't parsed.
  Weak/free models (`openrouter/free`) don't emit RAGAS's structured JSON.
- **Rate limits:** raw capture showed **HTTP 429 "free-models-per-day … 
  X-RateLimit-Remaining: 0"** — OpenRouter free tier caps at **50 req/day**. RAGAS
  makes hundreds of calls; `context_precision` (one call per retrieved context) is
  the most call-heavy and the first to hit the wall → it went NaN while the other
  three computed.
- **Exception handling:** the pipeline wrote `NaN` to `latest.json` as if it were a
  valid result — a silent failure.
- **RAGAS + local Ollama:** even reachable, the native `ChatOllama` path yields
  all-NaN; the **`ChatOpenAI` path parses correctly**. Verified by driving the
  local model through Ollama's OpenAI-compatible `/v1` endpoint — all 4 metrics
  computed.

### Root cause (chain, all pipeline/environment — not model quality)
1. Dependency drift (`langchain-core` 1.x) broke `ChatOpenAI` + RAGAS internals.
2. Default judge was local Ollama, typically not running.
3. Weak judge models emit JSON RAGAS can't parse.
4. RAGAS 0.2.6 only parses the `ChatOpenAI` code path, not `ChatOllama`.
5. Free-tier 50 req/day cap starves the call-heavy metrics.
6. NaN was written as a valid result (no guard).

### Fix (commit 307de80)
- Pin `langchain-core>=0.3.15,<0.4` (+ keep `langchain-ollama==0.2.3`) in
  requirements; repaired the local venv to match.
- Dedicated `ragas_judge_model` (`openai/gpt-oss-20b:free`), provider default →
  `openrouter`.
- Local judge now uses `ChatOpenAI` → Ollama `/v1` (`ollama_base_url`), the path
  RAGAS can parse. Offline answer-gen reuses the same LLM.
- **Fail-loud:** NaN now raises `RuntimeError` (naming the failing metrics +
  likely causes) and writes nothing; `safe_score` drops NaN rows.
- `RunConfig(timeout, max_workers)` so slow local judges don't time-out to NaN;
  `ragas_max_samples` to fit free-tier daily caps.

### Successful rerun — all metrics computed (no NaN)
- **Validation (capable judge, `openai/gpt-oss-20b:free`, 1 real Apple sample):**
  faithfulness **1.0**, answer_relevancy **0.924**, context_precision **1.0**,
  context_recall **1.0** — proves the pipeline computes all four.
- **Benchmark run (local `gemma4:e4b-mlx` via /v1, N=3, `evaluation/results/latest.json`):**
  faithfulness **0.8333**, answer_relevancy **0.2085**, context_precision **0.0**,
  context_recall **0.2222**, passed=false. Every metric is a real number.
  Caveat (model/eval quality, not pipeline): `gemma4:e4b` is a weak free judge, so
  absolute values are conservative/noisy (esp. context_precision 0.0); the run was
  local because the OpenRouter free daily quota was exhausted, and capped at N=3
  because the local judge is slow (~60–90s per context_precision job).

### Follow-ups (see TODO)
- Full 10-sample benchmark with the capable judge needs the OpenRouter free daily
  quota to reset (UTC midnight) or credits — `ragas_max_samples` keeps a run under
  the 50/day cap. Also: `openrouter/free` turns out to be a *valid* slug (works),
  contra the P1-4 assumption — worth revisiting the answer-gen default.

---

## 2026-07-14 — P2-1/2 + P2-4 (partial); P2-3 deferred

**Author:** Manoj Mareedu.

- **P2-1:** removed the stray committed `apple_report.pdf` (generated output,
  referenced nowhere); hardened `.gitignore` (`*.pdf`, tool caches). **Kept
  `.hf_rebuild`** — verification showed it's an intentional HF Space rebuild
  trigger (`# rebuild trigger`, committed as "ci: trigger HF Space rebuild"), not a
  stray artifact. Corrected the audit's mislabel.
- **P2-2:** ran `black` across `src`+`tests` (18 files) and added
  `black --check src tests` to CI and `make lint`, so formatting is enforced going
  forward. `ruff`/`black` scope extended to `tests/`. Verified tests pass in a
  CI-like env (no `.env`, no secret).
- **P2-4 (partial):** `get_company_cik` re-downloaded the full SEC
  `company_tickers.json` on every call and runs 3+ times per analysis. Extracted
  `_get_edgar_tickers()` behind `lru_cache(maxsize=1)` (immutable tuple, SEC order
  preserved so the 3-pass search is identical). Objective result: **3 lookups → 1
  network fetch** (unit test), and fetch errors aren't cached (retry preserved).
  Remaining retry/backoff + report cache tracked in P2-4.
- **P2-3 deferred:** consolidating the two ChromaDB access patterns rewrites the
  core RAG write path and needs live-EDGAR integration testing to validate safely
  — an architecture decision left for the owner rather than a blind refactor of
  working code. Rationale + recommended approach recorded in TODO P2-3.
- Suite now **33 passing**; `ruff`/`black`/`mypy` all clean.

---

## 2026-07-14 — P1-5: de-hardwire RAGAS judge + make eval a real gate

**Author:** Manoj Mareedu.

- Root cause: `ragas_eval.py` hardwired `ChatOllama("llama3.2")` and printed
  PASS/FAIL but always exited 0 — it could neither run without a specific local
  daemon nor actually gate anything.
- Change: added `_build_judge_llm(settings)` selecting the judge via
  `RAGAS_JUDGE_PROVIDER` (`ollama` default / `openrouter`; both free — no paid
  dep). Thresholds now live in settings; `run_evaluation()` returns a `passed`
  bool and the CLI `sys.exit(0/1)` on it, so `make eval` is a genuine quality gate.
- Config: new `ragas_*` settings; `.env.example` documents the provider toggle;
  README roadmap updated to reflect the gated-but-manual reality.
- mypy flagged `api_key` needing `SecretStr` on the OpenRouter path — fixed.
  Verified both judge backends construct offline; `ruff`/`mypy`/`pytest` green.
- **Deliberately not wired into GH push CI** (no Ollama there; live-LLM scoring is
  flaky). Remaining CI-wiring + a self-contained fixture corpus split into new
  **P1-5b**.

---

## 2026-07-14 — P1-3/4/6/7/8: schema de-dup, config/doc accuracy, LICENSE, compose

**Author:** Manoj Mareedu.

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

**Author:** Manoj Mareedu.

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

**Author:** Manoj Mareedu.

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

**Author:** Manoj Mareedu.

### What was done
- Created the repository management system: **ENGINEERING_GUIDE.md**, **TODO.md**,
  **WORKLOG.md** (this file). No prior versions existed.
- Adopted an industry-standard engineering-standards + Definition of Done set in
  ENGINEERING_GUIDE.md, because no explicit standards/DoD were supplied with the request.
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
  ENGINEERING_GUIDE.md §4–5 (none were provided with the initial request).
