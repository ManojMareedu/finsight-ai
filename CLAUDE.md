# CLAUDE.md

Operating guide for AI agents (and humans) working in this repository. Read this
first. It defines what the project is, how it is built, the engineering standards
we hold code to, and the Definition of Done every change must meet before it is
considered complete.

> The management system for this repo is three files:
> - **CLAUDE.md** (this file) — standing rules, architecture, commands, DoD.
> - **TODO.md** — the prioritized backlog. Source of truth for "what's next".
> - **WORKLOG.md** — append-only journal of what was done, when, and why.
>
> Keep all three current. When you finish a unit of work: update TODO.md (check
> items off / add follow-ups) and append a WORKLOG.md entry.

---

## 1. What this project is

**FinSight AI** — a multi-agent financial due-diligence platform. A user enters a
US public company name; a LangGraph-orchestrated pipeline of four agents retrieves
SEC EDGAR filings and financials, assesses risk, and synthesizes a structured
`DueDiligenceReport` (executive summary, financial snapshot, risk factors,
investment signal, confidence score) returned as JSON via FastAPI or a PDF via the
Streamlit UI.

- **Language/runtime:** Python 3.11
- **Deploy target:** single Docker container (FastAPI :8000 + Streamlit :7860) on
  HuggingFace Spaces.
- **Cost model:** free-tier everything (EDGAR, OpenRouter free models, local
  embeddings, Langfuse free tier).

## 2. Architecture (ground truth — verified against source)

```
POST /analyze  ──►  LangGraph workflow (src/graph/workflow.py)
                     research ──► filing ──► risk ──┬─(score>0.7 & iters<3)─► research (loop, max 3)
                                                    └─(else)────────────────► synthesis ──► END
```

- **research_agent** (`src/agents/research_agent.py`) — optional Tavily web search
  + financial metrics from **SEC EDGAR** (via `get_stock_info` → EDGAR XBRL, *not*
  yfinance despite the stale docstring). Emits `iterations: 1`.
- **filing_agent** (`src/agents/filing_agent.py`) — on-demand SEC 10-K ingestion
  into ChromaDB (150k chars), then multi-query similarity retrieval (k=8).
  Retrieval params are benchmark-tuned (WORKLOG 2026-07-15).
- **risk_agent** (`src/agents/risk_agent.py`) — LLM extracts 3–6 risks, derives a
  0–1 `risk_score` from a severity-weighted average.
- **synthesis_agent** (`src/agents/synthesis_agent.py`) — `structured_chat`
  produces a schema-validated `DueDiligenceReport`, stamped with the real 10-K
  filing date.

Supporting modules: `src/graph/state.py` (TypedDict + `Annotated[int, add]`
reducer on `iterations`), `src/models/schemas.py` (Pydantic v2 report schema),
`src/utils/` (config, EDGAR data fetchers, OpenRouter LLM client, PDF), `src/rag/`
(ingestion, retriever, local sentence-transformers embeddings),
`src/observability/tracer.py` (Langfuse), `src/api/` (FastAPI app + routes),
`src/ui/app.py` (Streamlit), `src/evaluation/ragas_eval.py` (RAGAS quality gate)
and `src/evaluation/benchmark.py` (comprehensive retrieval + RAG benchmark →
`evaluation/results/`).

## 3. Commands

```bash
# Environment. The committed .venv is Python 3.12 and works for all gates via
# `python -m` (its mypy/black console-script shebangs are stale — invoke through
# python -m, as below). python3.11 is not installed here; recreation is optional.
pip install -r requirements.txt   # into .venv
export PYTHONPATH=$(pwd)

# Run
make run-api        # uvicorn src.api.main:app --reload --port 8000
make run-ui         # streamlit run src/ui/app.py
bash start.sh       # both, as in the container

# Quality gates (must all pass — this is the DoD gate)
make lint           # ruff check src tests && black --check src tests && mypy src
make test           # pytest tests/ -v
make eval           # RAGAS quality gate → evaluation/results/latest.json (pass/fail)
make benchmark      # full retrieval+RAG benchmark → evaluation/results/{json,md}

# Prefer invoking tools via the venv python if console-script shebangs are broken:
.venv/bin/python -m pytest
.venv/bin/ruff check src
.venv/bin/python -m mypy src
```

### Evaluation & benchmark provider (OpenRouter primary)

- **OpenRouter is the primary evaluation provider.** Judge defaults to
  `openai/gpt-oss-20b:free` (`RAGAS_JUDGE_PROVIDER=openrouter`,
  `RAGAS_JUDGE_MODEL=...`). The project's OpenRouter account is **no longer
  free-tier** (~1,000 requests/day), so full-set RAGAS runs are practical — no
  need to cap `RAGAS_MAX_SAMPLES` for a normal run.
- **Ollama is an optional offline fallback only** (`RAGAS_JUDGE_PROVIDER=ollama`,
  via the OpenAI-compatible `/v1` endpoint — RAGAS 0.2.6 does not parse the native
  `ChatOllama` path). Use it when offline/rate-limited; it is slower and noisier.
- Deterministic benchmark metrics (retrieval precision@k, recall, latency,
  success rate) need **no** LLM and always run; RAGAS metrics need a judge.
- `context_precision` is RAGAS's most parse-fragile metric on free judges and may
  report `null` — that means "not scored", not zero.

## 4. Engineering standards

> No explicit standards/DoD were supplied when this file was created, so the
> following industry-standard set was adopted. Adjust to taste and re-baseline.

1. **Style & format:** ruff (`E,W,F,I`) and black, line length 100. Code must be
   ruff-clean and black-formatted before commit.
2. **Types:** mypy on `src/` must pass. New public functions get type hints.
3. **Tests:** every behavioral change ships with a test. Pure logic (parsing,
   scoring, normalization, config) gets unit tests that do not hit the network.
   Network/LLM calls are mocked in tests.
4. **Config over constants:** read settings from `src/utils/config.py`. Do not
   hardcode paths, model names, thresholds, or URLs in modules (see the
   `retriever.py` hardcoded-path bug in TODO).
5. **Graceful degradation:** external calls (EDGAR, Tavily, LLM, Langfuse) must
   fail soft with logging, never crash the pipeline — match the existing pattern.
6. **Secrets:** only via `.env` (gitignored). Never commit keys. `.env.example`
   documents required vars.
7. **Single source of truth for schemas:** request/response and domain models live
   in `src/models/schemas.py`; routes import them (do not redefine).
8. **Docs stay honest:** if you change structure, behavior, or the report shape,
   update the README and this file in the same change.
9. **CI is the contract:** `.github/workflows/ci.yml` (ruff, mypy, pytest, docker
   build) must be green on `main`. A red pipeline is a P0.

## 5. Definition of Done

A change is **Done** only when all of the following hold:

- [ ] Code compiles/imports and the feature works when exercised end-to-end.
- [ ] `ruff check src` is clean.
- [ ] `black --check src` is clean.
- [ ] `mypy src` passes.
- [ ] `pytest` passes, and new/changed behavior has tests.
- [ ] No secrets, keys, or large binaries added to git.
- [ ] Config/paths/models read from `config.py`, not hardcoded.
- [ ] README and CLAUDE.md updated if behavior/structure/contract changed.
- [ ] TODO.md updated (item checked off or follow-ups added).
- [ ] A WORKLOG.md entry appended describing what changed and why.
- [ ] CI is expected to pass (the same gates run there).
- [ ] If retrieval/eval behavior changed: rerun `make benchmark` and confirm the
      objective metrics did not regress (revert changes that don't measurably help).
- [ ] If the Dockerfile/deps/entrypoint changed: `docker build` succeeds and the
      container serves `/health` (verified for v1.0.0 — see WORKLOG 2026-07-15).

## 6. Known landmines (see TODO.md for full backlog)

Resolved (kept here as history — all verified fixed):
- ~~`retriever.py` hardcoded `./data/chroma`~~ → now reads `CHROMA_PERSIST_DIR`.
- ~~`Dockerfile` HEALTHCHECK probed `/api/v1/health`~~ → now `/health`.
- ~~CI red on `ruff`~~ → clean; CI also runs `black --check` + `mypy` + tests.
- ~~Test coverage ≈ zero~~ → 42 network-free tests.
- ~~RAGAS returned all-NaN~~ → root-caused (dependency drift, judge path, silent
  NaN) and fixed; `langchain-core` now pinned `>=0.3.15,<0.4` to prevent drift.
  Local RAGAS judge goes through Ollama `/v1` (ChatOpenAI), not `ChatOllama`.
- ~~Retrieval used MMR (k=6)~~ → benchmark-tuned to similarity k=8 + 150k ingestion.

Still live:
- `.venv` mypy/black console-script shebangs are stale (built at an old
  space-containing path). Harmless: invoke via `.venv/bin/python -m <tool>`.
- The two ChromaDB access patterns are not yet unified (TODO P2-3, deferred).
- RAGAS eval is manual/local, not wired into GH push CI (TODO P1-5b).
- `get_company_cik` is cached per-process via `_get_edgar_tickers` — if a
  long-running server must pick up daily SEC ticker updates, restart it (or add
  TTL). Reference data changes slowly, so this is acceptable today.

## 7. Conventions for agents

- The EDGAR migration is intentional — do not re-add yfinance. Financial figures
  come from EDGAR XBRL (`get_financials_from_edgar`), not the 10-K text; RAG
  retrieval is for qualitative content only.
- When touching the graph state, remember `iterations` uses an `add` reducer:
  return `{"iterations": 1}` to increment, never a running total.
- Keep the four-agent contract intact: each node returns a partial state dict.
