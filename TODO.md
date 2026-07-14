# TODO — FinSight AI Backlog

Prioritized backlog. Source of truth for "what's next". Ordered **P0 → P2**.
When you complete an item, check it off and append a WORKLOG.md entry. Add
follow-ups you discover as new items.

Legend: **P0** = broken / blocking · **P1** = correctness, debt, must-do-soon ·
**P2** = hygiene, enhancements, roadmap.

Last reviewed: **2026-07-14** (see WORKLOG entries 2026-07-14).

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

- [ ] **P1-2 · Establish real test coverage.** Only `tests/test_smoke.py`
  (`assert True`) exists. Add network-free unit tests for the highest-value pure
  logic: `data_fetchers` (`resolve_ticker`, `_fmt_large`/`_parse_fmt_large`,
  `_latest_annual`, revenue-growth, the gross-margin/net-income sanity checks),
  `llm_client._normalize_messages`, `risk_agent` score derivation + severity
  normalization, and `schemas` validation (bounds on `confidence_score`, enums).
  Mock all HTTP/LLM.

- [ ] **P1-3 · De-duplicate request/response schemas.**
  `AnalyzeRequest`/`AnalyzeResponse` are defined in **both**
  `src/models/schemas.py` and `src/api/routes/analyze.py` (and diverge — the route
  copy types `report` as `dict`, the schema copy as `DueDiligenceReport`). Delete
  the route copies and import from `schemas.py`. (Per DoD standard #7.)

- [ ] **P1-4 · Config drift on the model name.** Default `primary_model` is
  `"openrouter/free"` in `config.py`, `meta-llama/llama-3.3-70b-instruct:free` in
  `.env.example`, and the README says `openrouter/auto`. Pick one, make it the
  config default, and align `.env.example` + README.

- [ ] **P1-5 · RAGAS eval is not CI-gatable.** `ragas_eval.py` hardwires
  `ChatOllama(model="llama3.2")`, requiring a local Ollama daemon + a pre-populated
  ChromaDB — it cannot run in CI, yet the README roadmap promises "automated CI
  quality gates". Decide the judge backend (OpenRouter LLM vs Ollama), make it
  configurable, seed a tiny fixture corpus, and wire a threshold check. Until then,
  document it as manual-only.

- [ ] **P1-6 · Stale/incorrect docstring in `research_agent.py`.** The docstring
  says it "Fetches live stock metrics via yfinance" and does "Tavily web search";
  the code uses SEC EDGAR (`get_stock_info` → EDGAR XBRL). Correct the docstring —
  do **not** re-introduce yfinance.

- [ ] **P1-7 · Missing LICENSE file.** README badges + text claim MIT and link to
  `LICENSE`, which does not exist. Add the MIT license file.

- [ ] **P1-8 · README structure section has drifted.** It documents
  `docker/docker-compose.yml` (absent — only `docker/Dockerfile.ui` exists) and a
  yfinance-based research flow. Reconcile the README with the actual tree and data
  flow (or add the missing compose file if it's intended).

## P2 — Hygiene, enhancements, roadmap

- [ ] **P2-1 · Remove stray tracked artifacts.** `apple_report.pdf` and
  `.hf_rebuild` are committed at the repo root. Remove from git (keep `.hf_rebuild`
  out or move under a docs/ sample if intentional).

- [ ] **P2-2 · Align CI with the Makefile lint gate.** CI runs `ruff` + `mypy` but
  not `black --check`, while `make lint` does. Add `black --check src` to CI so the
  DoD format gate is enforced on every push.

- [ ] **P2-3 · Consolidate the two ChromaDB access patterns.** `ingestion.py` uses
  the raw `chromadb.PersistentClient` with manually computed embeddings, while
  `retriever.py` uses the `langchain_chroma.Chroma` wrapper. Two code paths against
  one collection is a compatibility risk; standardize on one.

- [ ] **P2-4 · Resilience for external calls.** Add timeouts already exist, but no
  retry/backoff on EDGAR/LLM transient failures and no response caching. Consider a
  simple retry + a per-company cache (also unblocks the roadmap caching item).

- [ ] **P2-5 · Roadmap features** (from README, unstarted): streaming responses to
  the UI, multi-company comparison mode, international filings, risk-change email
  alerts, caching layer.

---

### Notes
- The Definition of Done and quality-gate commands live in **CLAUDE.md** §4–5.
- P1-1 (fix venv) is a practical prerequisite for confidently closing anything
  that must pass `mypy`/`black` locally.
