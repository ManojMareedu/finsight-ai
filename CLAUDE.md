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
  into ChromaDB, then multi-query MMR retrieval.
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
`src/ui/app.py` (Streamlit), `src/evaluation/ragas_eval.py` (RAGAS, Ollama-backed).

**Note:** The README's "Repository Structure" section has drifted from reality
(it references `docker/docker-compose.yml` and describes yfinance usage that no
longer exists). Trust the source and this file over the README.

## 3. Commands

```bash
# Environment (recreate if venv is broken — see TODO P1)
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=$(pwd)

# Run
make run-api        # uvicorn src.api.main:app --reload --port 8000
make run-ui         # streamlit run src/ui/app.py
bash start.sh       # both, as in the container

# Quality gates (must all pass — this is the DoD gate)
make lint           # ruff check src && black --check src && mypy src
make test           # pytest tests/ -v
make eval           # RAGAS (requires local Ollama running llama3.2 + ingested ChromaDB)

# Prefer invoking tools via the venv python if console-script shebangs are broken:
.venv/bin/python -m pytest
.venv/bin/ruff check src
.venv/bin/python -m mypy src
```

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

## 6. Known landmines (see TODO.md for full backlog)

- `src/rag/retriever.py` hardcodes `./data/chroma`, ignoring `CHROMA_PERSIST_DIR`
  → retrieval silently returns nothing in Docker/HF where the dir is `/data/chroma`.
- `Dockerfile` HEALTHCHECK probes `/api/v1/health`; the real route is `/health`.
- CI currently fails on `ruff check src` (6 errors in `ragas_eval.py`).
- Local `.venv` console scripts (mypy/black) are broken — the venv was built at a
  path containing a space and the shebangs point to a non-existent interpreter.
- Test coverage is effectively zero (`tests/test_smoke.py` asserts `True`).

## 7. Conventions for agents

- Do not "fix" the yfinance references by re-adding yfinance — the EDGAR migration
  is intentional; fix the stale docstring instead.
- When touching the graph state, remember `iterations` uses an `add` reducer:
  return `{"iterations": 1}` to increment, never a running total.
- Keep the four-agent contract intact: each node returns a partial state dict.
