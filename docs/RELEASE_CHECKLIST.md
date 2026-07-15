# Release Checklist — FinSight AI v1.0.0

Status of the v1.0.0 portfolio release: what's done, what's deliberately out of
scope, how to verify it, and how to reproduce every claim.

---

## ✅ Completed work

**Product**
- Multi-agent due-diligence pipeline (LangGraph: research → filing → risk →
  synthesis, with a bounded risk-driven loop).
- SEC EDGAR integration: XBRL financials (structured) + 10-K narrative (RAG).
- Schema-validated `DueDiligenceReport` (Pydantic v2) via FastAPI `/analyze`;
  Streamlit UI with PDF export.

**Reliability & quality**
- 42 network-free unit tests (all HTTP/LLM mocked).
- Gates green: **ruff**, **black**, **mypy**, **pytest**.
- CI (GitHub Actions): lint + type-check + tests + Docker build on every push.
- Fail-loud evaluation: RAGAS NaN raises instead of writing a fake result.

**Evaluation & benchmarks**
- RAGAS quality gate (`make eval`) with thresholds → `evaluation/results/latest.json`.
- Comprehensive benchmark (`make benchmark`): deterministic retrieval precision@k,
  recall, latency mean/p95, success rate (all questions) + RAGAS (full set),
  self-documenting Markdown/JSON reports.
- Retrieval tuned by benchmark, not by intuition (similarity k=8 + 150k ingestion).

**Deployment**
- Single Docker image (FastAPI + Streamlit) verified: builds, serves `/health`,
  Docker `HEALTHCHECK` healthy. Live demo on HuggingFace Spaces.

**Documentation**
- `README.md`, `ENGINEERING_GUIDE.md` (agent/engineering guide), `docs/ENGINEERING_DECISIONS.md`
  (interview-grade rationale), `WORKLOG.md` (dated decision trail), `TODO.md`.

## 📊 Benchmark summary (v1.0.0)

Judge `openai/gpt-oss-20b:free` via OpenRouter, 10-question golden set, all 10
RAGAS-scored. Report: `evaluation/results/benchmark_latest.md`.

| Metric | Value | Notes |
|---|---|---|
| Retrieval Precision@8 | **1.00** | ~0.975–1.00 run-to-run (approximate HNSW) |
| Retrieval Recall | **0.79** | keyword-overlap proxy, qualitative questions |
| Success Rate | **10/10** | end-to-end, no exceptions |
| Latency total mean / p95 | **17.4s / 33.4s** | free hosted LLM dominates |
| RAGAS Faithfulness | **0.93** | ≥ 0.70 threshold ✅ |
| RAGAS Answer Relevancy | **0.73** | ≥ 0.65 threshold ✅ |
| RAGAS Context Recall | 0.30 | see limitations |
| RAGAS Context Precision | `null` | parse-fragile on free judge (not zero) |

## ⚠️ Known limitations (honest)

- **Golden set is small** (10 Q across 3 companies) — enough to drive retrieval
  tuning, but RAGAS scores on it are noisy; the deterministic metrics are the
  high-confidence signal.
- **`context_recall` is low (0.30)** and **`context_precision` is `null`**: several
  ground-truth answers are exact financial figures (revenue, margin) that are
  reported via EDGAR **XBRL**, not the 10-K narrative the retriever searches — so
  RAGAS can't attribute them to retrieved text, and its most parse-fragile metric
  fails on free judges. These reflect the data model and judge, not a retrieval
  regression.
- **Retrieval is approximate** (ChromaDB HNSW), so precision@k varies ~±0.02.
- **Evaluation is manual/local**, not in CI (needs a judge/quota); by design.
- **Financial figures depend on EDGAR XBRL availability** per company/period.

## 🔀 Known tradeoffs

- Two data paths (structured XBRL + narrative RAG) instead of one — chosen because
  each is used only where it's reliable.
- Two processes in one container (API + UI) — matches the single-Space deploy model.
- k=8 increases judge/synthesis context cost — accepted for the recall gain.
- Streamlit over a custom SPA — demo speed over frontend flexibility.

## 🗺️ Future roadmap (out of v1.0.0 scope)

- Wire eval into CI as an opt-in job with a seeded fixture corpus (TODO P1-5b).
- Expand and human-review the golden set for tighter RAGAS confidence.
- Consolidate the two ChromaDB access patterns (TODO P2-3).
- Streaming UI updates; multi-company comparison; response caching; retry/backoff.

## 🔁 Reproducibility guide

```bash
git clone https://github.com/ManojMareedu/finsight-ai.git && cd finsight-ai
cp .env.example .env            # add OPENROUTER_API_KEY (free at openrouter.ai/keys)
pip install -r requirements.txt
export PYTHONPATH=$(pwd)

make lint && make test         # gates
make benchmark                 # retrieval + RAGAS report → evaluation/results/
docker compose up --build      # full stack (UI :7860, API :8000/docs)
```

## ✔️ Verification checklist (v1.0.0)

- [x] `ruff check src tests` clean
- [x] `black --check src tests` clean
- [x] `mypy src` clean
- [x] `pytest` — 42 passed
- [x] `docker build` (root `Dockerfile` + `docker/Dockerfile.ui`) succeed
- [x] container serves `/health` → `{"status":"ok"}`, `HEALTHCHECK` healthy
- [x] `make eval` produces a real (non-NaN) result
- [x] `make benchmark` completes and writes JSON + Markdown
- [x] README / ENGINEERING_GUIDE / ENGINEERING_DECISIONS synchronized with code
- [x] every major claim has evidence (tests / benchmark / CI / live demo / schema)

## 🚀 Deployment summary

- **Image:** single Docker container; `start.sh` runs FastAPI (:8000, health-gated)
  then Streamlit (:7860). Embedding model baked at build time for fast cold start.
- **Host:** HuggingFace Spaces (`sdk: docker`, `app_port: 7860`), free/permanent.
- **Health:** `GET /health` → `{"status":"ok"}`; Docker `HEALTHCHECK` on `/health`.
- **Config:** `OPENROUTER_API_KEY` required; Langfuse/Tavily optional.
