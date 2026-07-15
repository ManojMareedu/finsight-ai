# TODO — FinSight AI Backlog

Prioritized backlog. Source of truth for "what's next". Ordered **P0 → P2**.
When you complete an item, check it off and append a WORKLOG.md entry. Add
follow-ups you discover as new items.

Legend: **P0** = broken / blocking · **P1** = correctness, debt, must-do-soon ·
**P2** = hygiene, enhancements, roadmap.

Last reviewed: **2026-07-14** (see WORKLOG entries 2026-07-14). **Status:** all P0
+ P1 resolved and verified; P2-1/2/4(partial) done. Remaining: P2-3 (deferred —
architecture decision), P2-4 remainder, P2-5 (roadmap features), P1-5b.

---

## P0 — Broken / blocking (do these first)

- [x] **P0-1 · CI is red on `main`.** ~~`ruff check src` fails with 6 errors, all in
  `src/evaluation/ragas_eval.py`.~~ **DONE 2026-07-14:** 4 auto-fixed, 2 E501 long
  lines wrapped via implicit string concatenation. `ruff check src` now exits 0;
  `mypy`/`pytest` still green.

- [x] **P0-2 · Retrieval path bug breaks RAG in Docker/HF.**
  ~~`src/rag/retriever.py` hardcoded `persist_directory="./data/chroma"`, ignoring
  `settings.chroma_persist_dir`.~~ **DONE 2026-07-14:** `get_retriever` now reads
  `get_settings().chroma_persist_dir`. Verified at runtime: wrote docs to a
  non-default temp dir via ingestion's low-level path, retrieved 3/3 through
  `retrieve_context` (pre-fix would return 0).

- [x] **P0-3 · Dockerfile HEALTHCHECK hits the wrong path.**
  ~~`Dockerfile` probed `/api/v1/health`; real route is `/health`.~~ **DONE
  2026-07-14:** corrected to `/health`. Verified in-process with TestClient:
  `/health` → 200, `/api/v1/health` → 404.

## P1 — Correctness, tech debt, must-do-soon

- [x] **P1-1 · Local dev environment.** **DONE 2026-07-14 (reclassified after
  verification):** the venv is **not** broken for running gates — `mypy`, `black`,
  `ruff`, and `pytest` all run via `.venv/bin/python -m <tool>`. Only the
  console-script shebangs are stale (they point at an old space-containing path).
  Recreating as 3.11 is impossible here (`python3.11` is not installed; the venv is
  3.12) and would destroy working infra, so it was **not** recreated. CLAUDE.md §3
  already prescribes the `python -m` invocation. mypy targets 3.11 semantics via
  config regardless of the 3.12 interpreter.

- [x] **P1-2 · Establish real test coverage.** **DONE 2026-07-14:** added 30
  network-free unit tests (suite now 31, was 1). `tests/test_data_fetchers.py`
  (ticker resolution, `_fmt_large`/`_parse_fmt_large` round-trips, `_latest_annual`
  incl. GAAP concept-switch, `_revenue_growth`, and the net-income>revenue and
  >100% gross-margin sanity checks via mocked HTTP), `tests/test_llm_client.py`
  (`_normalize_messages` folding + non-mutation), `tests/test_risk_agent.py`
  (score derivation, severity normalization, fenced-JSON, fallback; LLM mocked),
  `tests/test_schemas.py` (confidence bounds, enum + min-length validation). All
  HTTP/LLM mocked. `ruff`/`mypy`/`pytest` green.

- [x] **P1-3 · De-duplicate request/response schemas.** **DONE 2026-07-14:**
  deleted the route-local `AnalyzeRequest`/`AnalyzeResponse` in `analyze.py`; it now
  imports both from `src.models.schemas`. Side benefit: request validation
  (`min_length=2`) is now enforced (verified: short name → 422) and the response
  `report` is the typed `DueDiligenceReport` (dict output coerces cleanly).

- [x] **P1-4 · Config drift on the model name.** **DONE 2026-07-14:** standardized
  on `meta-llama/llama-3.3-70b-instruct:free` (a concrete free slug — keeps the
  default $0, unlike `openrouter/auto` which can route to paid, and unlike the
  invalid `openrouter/free`). `config.py` default + `.env.example` + README env
  table now agree. **⚠️ Action for owner:** the local/deploy `.env` (gitignored)
  still has `PRIMARY_MODEL=openrouter/free`; update it to the free slug above if
  `openrouter/free` isn't valid on your OpenRouter account.

- [x] **P1-5 · RAGAS eval judge de-hardwired + gated.** **DONE 2026-07-14
  (partial; CI wiring split to P1-5b).** The judge LLM is no longer hardwired to
  `ChatOllama("llama3.2")`: `_build_judge_llm(settings)` selects
  `RAGAS_JUDGE_PROVIDER` = `ollama` (default, local) or `openrouter` (reuses the
  free model) — both free. Thresholds moved to settings
  (`ragas_faithfulness_min`/`ragas_answer_relevancy_min`); `run_evaluation` returns
  a `passed` bool and the CLI exits non-zero on failure, so `make eval` is a real
  gate. Documented as manual/local (README roadmap + config comments). mypy/ruff
  clean; both backends verified to construct offline.

- [x] **P1-5c · RAGAS returned NaN for all metrics — FIXED 2026-07-14.** Full RCA
  in WORKLOG (dep drift `langchain-core` 1.x; judge unreachable/weak-JSON; RAGAS
  parses only the `ChatOpenAI` path not `ChatOllama`; free-tier 50 req/day cap;
  NaN written silently). Fixes: pin `langchain-core`, dedicated `ragas_judge_model`,
  local judge via Ollama `/v1`, fail-loud NaN guard, `RunConfig` timeout,
  `ragas_max_samples`. Rerun computes all 4 metrics (no NaN); results in
  `evaluation/results/latest.json` (N=3 local run). Commit 307de80.

- [x] **R1 · Retrieval-quality optimization — DONE 2026-07-15.** Benchmark-validated
  via a deterministic golden-set diagnostic + RAGAS. Kept: retriever
  MMR(k6,fetch_k20,λ0.7) → similarity(k8); ingestion max_chars 50k → 150k. Reverted
  (measured worse/no gain): chunk sizes 500/700, max_chars 300k. Before→after:
  company_precision@k 0.8167→0.9625, gt_keyword_recall 0.6081→0.7942, RAGAS
  faithfulness 0.8333→1.0000. Commit 3e334d8. See WORKLOG 2026-07-15.

- [ ] **P1-5d · Full-set benchmark with the capable judge.** The N=3 run used a
  weak local judge (gemma4) because the OpenRouter free daily cap (50/day) was
  exhausted; scores are conservative/noisy. Rerun the full 10-item set with
  `RAGAS_JUDGE_PROVIDER=openrouter` / `openai/gpt-oss-20b:free` after the quota
  resets (validation on 1 sample gave 1.0/0.924/1.0/1.0), or with a stronger local
  judge that fits RAGAS's timeout. Also revisit the P1-4 answer-gen default —
  `openrouter/free` is in fact a valid, working slug.

- [ ] **P1-5b · Wire eval into an automated gate + self-contained fixture.**
  Deferred from P1-5. `collect_eval_data` still requires a pre-populated ChromaDB
  (calls `retrieve_context`). To make eval runnable from a clean checkout, seed a
  tiny fixture corpus into a temp Chroma so it doesn't depend on prior ingestion,
  then decide whether/how to gate it (a scheduled job or an opt-in CI job with an
  `OPENROUTER_API_KEY` secret — not the default push CI, to avoid flakiness).

- [x] **P1-6 · Stale/incorrect docstring in `research_agent.py`.** **DONE
  2026-07-14:** docstring + inline comment now say "financial metrics from SEC
  EDGAR (XBRL company facts)" instead of yfinance. yfinance was **not**
  re-introduced.

- [x] **P1-7 · Missing LICENSE file.** **DONE 2026-07-14:** added MIT `LICENSE`
  (© 2026 Manoj Mareedu), matching the README badge/text.

- [x] **P1-8 · README structure section has drifted.** **DONE 2026-07-14:** the
  README referenced a nonexistent `docker/docker-compose.yml` and a `docker-compose
  up` Quick Start that couldn't work. Added a real root `docker-compose.yml` that
  builds the single-container root Dockerfile (both services via `start.sh`, ports
  7860/8000, `.env` env_file, persistent `chroma_data` volume for `/data/chroma`)
  so the documented command works; updated the structure block to match reality
  (`docker/Dockerfile.ui` + root `docker-compose.yml`). Validated with
  `docker compose config`.

## P2 — Hygiene, enhancements, roadmap

- [ ] **P2-1 · Remove stray tracked artifacts.** `apple_report.pdf` and
  `.hf_rebuild` are committed at the repo root. Remove from git (keep `.hf_rebuild`
  out or move under a docs/ sample if intentional).

- [ ] **P2-2 · Align CI with the Makefile lint gate.** CI runs `ruff` + `mypy` but
  not `black --check`, while `make lint` does. Add `black --check src` to CI so the
  DoD format gate is enforced on every push.

- [ ] **P2-3 · Consolidate the two ChromaDB access patterns.** `ingestion.py` uses
  the raw `chromadb.PersistentClient` with manually computed embeddings, while
  `retriever.py` uses the `langchain_chroma.Chroma` wrapper. **DEFERRED (needs
  owner decision) 2026-07-14:** both paths currently work; unifying them rewrites
  the core RAG **write** path (collection creation with `hnsw:space=cosine`, id +
  metadata handling) and can only be safely validated with a live EDGAR ingestion,
  which isn't possible offline in this loop. Per "don't rewrite working code
  unnecessarily," left for a deliberate, integration-tested change. Recommended
  target: standardize on `langchain_chroma.Chroma` for both, then ingest a real
  company end-to-end and diff retrieval output before/after.

- [~] **P2-4 · Resilience for external calls.** **PARTIAL 2026-07-14:** cached the
  SEC `company_tickers.json` fetch via `_get_edgar_tickers` (`lru_cache`) — it was
  re-downloaded (~1MB+) on every `get_company_cik`, which runs 3+ times per
  analysis; now one download per process (verified by test: 3 lookups → 1 fetch;
  fetch errors are not cached, so it retries). **Remaining:** retry/backoff on
  transient EDGAR/LLM failures, and a per-company report/response cache (the
  roadmap caching item).

- [ ] **P2-5 · Roadmap features** (from README, unstarted): streaming responses to
  the UI, multi-company comparison mode, international filings, risk-change email
  alerts, caching layer.

---

### Notes
- The Definition of Done and quality-gate commands live in **CLAUDE.md** §4–5.
- P1-1 (fix venv) is a practical prerequisite for confidently closing anything
  that must pass `mypy`/`black` locally.
