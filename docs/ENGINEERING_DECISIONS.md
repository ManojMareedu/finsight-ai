# Engineering Decisions

Why FinSight AI is built the way it is. Each decision records the **problem**, the
**alternatives** considered, the **choice**, its **tradeoffs**, the **evidence**
that justified it, and **why it was selected**. Where a decision was validated by
measurement, the numbers come from the benchmark harness
(`src/evaluation/benchmark.py`) and are reproducible with `make benchmark`.

> TL;DR of the philosophy: *free-tier everything, structured data over narrative
> where possible, and every quality claim backed by an objective, reproducible
> metric.*

---

## 1. Orchestration: LangGraph (stateful graph) over a linear chain

- **Problem:** Due diligence is not a single prompt. It needs research → filing
  retrieval → risk assessment → synthesis, with the ability to *loop back* for a
  deeper pass when risk looks high.
- **Alternatives:** (a) a plain function pipeline; (b) LangChain `SequentialChain`;
  (c) a hand-rolled state machine; (d) LangGraph.
- **Chosen:** LangGraph `StateGraph` with a conditional edge after risk assessment
  (`src/graph/workflow.py`): `risk_score > 0.7 and iterations < 3` routes back to
  research, otherwise proceeds to synthesis.
- **Tradeoffs:** LangGraph adds a dependency and a small learning curve versus a
  bare function pipeline. In return we get explicit nodes/edges, typed state, and
  first-class conditional cycles.
- **Evidence / why:** A linear chain cannot express "re-research if risk is high
  and we haven't looped too many times" without ad-hoc control flow. The bounded
  loop (`iterations < 3`) is the one piece of genuine agentic control in the
  system, and LangGraph models it declaratively and testably.

## 2. Multi-agent decomposition (four single-responsibility agents)

- **Problem:** One mega-prompt that "does due diligence" is undebuggable and
  un-evaluable.
- **Alternatives:** a single prompt; a ReAct tool-calling agent; four specialized
  nodes.
- **Chosen:** four nodes — `research` (EDGAR financials + optional web),
  `filing` (RAG over the 10-K), `risk` (extract + score risks), `synthesis`
  (schema-validated report).
- **Tradeoffs:** more prompts and more LLM calls than a single shot; but each node
  is independently testable and swappable, and failures are localizable.
- **Evidence / why:** Separation of concerns makes the pipeline observable (each
  node returns a partial state dict) and lets us evaluate retrieval independently
  of generation. It also enables graceful degradation per stage (see §5).

## 3. Typed shared state (`TypedDict` + reducer)

- **Problem:** Agents must pass data without clobbering each other, and the loop
  must count iterations correctly.
- **Alternatives:** an untyped `dict`; a Pydantic model; a `TypedDict` with
  reducers.
- **Chosen:** `DueDiligenceState(TypedDict)` with `iterations:
  Annotated[int, operator.add]` so returning `{"iterations": 1}` *increments*
  rather than overwrites (`src/graph/state.py`).
- **Tradeoffs:** `TypedDict` gives static-check ergonomics but not runtime
  validation (that lives at the boundary — see §8). The `add` reducer is subtle;
  it is documented in CLAUDE.md so future edits don't return a running total.
- **Evidence / why:** The reducer is the correct LangGraph pattern for loop depth;
  without it, concurrent/looped updates to a counter are wrong. Typing catches
  state-key mistakes at mypy time.

## 4. Structured EDGAR data vs narrative RAG (the most important data decision)

- **Problem:** Answer both *quantitative* questions (revenue, margin, EPS) and
  *qualitative* ones (risks, strategy, competition) from SEC filings.
- **Alternatives:** (a) RAG over the 10-K text for everything; (b) scrape a
  finance API (Yahoo/Alpha Vantage); (c) split the two data types by source.
- **Chosen:** **structured financials from EDGAR XBRL** (`get_financials_from_edgar`
  → `companyfacts` API) for numbers, and **RAG over the 10-K narrative** only for
  qualitative content.
- **Tradeoffs:** two data paths instead of one; but each is used where it is
  actually reliable.
- **Evidence:** Direct inspection proved the rounded figures the questions ask for
  (e.g. "$383.3B", "44.1%") **do not appear in the 10-K text at all** — not even in
  the full 393k-char document — because 10-Ks report them via XBRL tables. Asking
  RAG to retrieve them is asking for something that isn't there. This is why the
  benchmark's keyword-recall proxy tops out on qualitative questions, and why
  numeric accuracy is a property of the XBRL path, not retrieval.
- **Why EDGAR over Yahoo/Alpha Vantage:** EDGAR is the official US-gov source — no
  API key, no rate limits, and every figure is traceable to a filed document.
  Yahoo/Alpha Vantage rate-limit aggressively on free tiers and are scraped
  estimates. Fits the zero-cost principle (§16).

## 5. Graceful degradation everywhere

- **Problem:** Free external services (Tavily, LLM router, Langfuse, even EDGAR)
  fail intermittently.
- **Chosen:** every external call is wrapped: Tavily is skipped if unkeyed, LLM
  failures are retried then surfaced, Langfuse tracing is best-effort, EDGAR
  fetch errors downgrade to an empty result. The pipeline logs and continues.
- **Tradeoffs:** more defensive code; partial results are possible.
- **Why:** for a 24/7 free demo, *degrade, don't crash* is the right reliability
  posture. Tests assert the fallbacks (e.g. EDGAR fetch failure → `{}`).

## 6. Vector store: ChromaDB

- **Problem:** Store and semantically retrieve 10-K chunks with zero setup and
  persistence.
- **Alternatives:** FAISS (in-memory, no persistence/metadata filtering out of the
  box), pgvector (needs Postgres), Pinecone/Weaviate (hosted, paid/quota), Chroma.
- **Chosen:** ChromaDB (persistent local client + a `langchain_chroma` retriever).
- **Tradeoffs:** Chroma's HNSW index is **approximate**, so retrieval is not
  bit-for-bit deterministic (observed precision@8 variance ~±0.02); good enough for
  this corpus and far simpler to operate than a DB server.
- **Why:** zero-setup persistence, metadata filtering (by `company`), and it runs
  in the same container. The approximate-NN variance is documented in the benchmark
  metric limitations rather than hidden.

## 7. Retrieval: pure-relevance similarity (k=8) over MMR — *benchmark-driven*

- **Problem:** MMR (Maximal Marginal Relevance) trades relevance for diversity.
  Does diversity help financial-QA retrieval?
- **Alternatives:** MMR at various `lambda_mult`/`k`; plain similarity at various
  `k`.
- **Chosen:** `search_type="similarity"`, `k=8`.
- **Evidence (measured, then kept only what improved):**

  | config | company precision@k | keyword recall |
  |---|---|---|
  | MMR k=6, λ=0.7 (original) | 0.8167 | 0.6081 |
  | similarity k=8 | 0.9125 | 0.7061 |
  | **similarity k=8 + 150k ingest (shipped)** | **~0.96–1.00** | **~0.79** |
  | reverted: MMR λ<1, chunk 500, 300k ingest | worse / no gain | — |

- **Tradeoffs:** k=8 sends more context to the judge (RAGAS `context_precision`
  cost scales with k) and to the synthesis prompt. Worth it for the recall gain.
- **Why:** a full parameter sweep showed diversity *lowered* both precision and
  recall for this task — financial QA rewards the most on-topic chunks, not diverse
  weaker ones. This is the headline example of "measure, then decide."

## 8. Ingestion / chunk sizing (1000/200) and 150k window — *benchmark-driven*

- **Problem:** What to chunk and how much of the filing to ingest.
- **Alternatives:** chunk sizes 500/700/1000; ingest 50k/150k/300k chars.
- **Chosen:** `RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)`,
  ingest **150k** chars.
- **Evidence:** smaller chunks (500) *reduced* both precision and recall (0.875 /
  0.593); the original 50k window stopped inside the business/risk sections so MD&A
  was never retrievable; 300k gave only +0.03 recall over 150k with lower precision
  and 2× the store cost. 150k was the knee of the curve.
- **Why:** kept the two changes that moved the metric (window 50k→150k, similarity
  k=8) and reverted the ones that didn't (chunk size, 300k).

## 9. Structured LLM output: Pydantic v2 schema enforcement

- **Problem:** The report must be machine-consumable JSON with a stable shape and
  valid enums/bounds — an LLM emits free text by default.
- **Alternatives:** parse free text; JSON mode with no schema; Pydantic-validated
  structured output with retry.
- **Chosen:** `structured_chat()` appends the JSON schema to the prompt, extracts
  the JSON, and `model_validate_json()`s it against `DueDiligenceReport`, retrying
  up to 3× on failure (`src/utils/llm_client.py`, `src/models/schemas.py`).
- **Tradeoffs:** occasional retries; a strict schema can reject a borderline-valid
  answer. Mitigated by a safety net in `synthesis_agent` (e.g. overriding a
  hallucinated `report_date` with the real EDGAR filing date).
- **Evidence / why:** `confidence_score` bounds, the `InvestmentSignal`/`RiskLevel`
  enums, and request `min_length` are all covered by unit tests. Schema validation
  is the "structured outputs → schema validation" evidence line.

## 10. API: FastAPI; UI: Streamlit

- **FastAPI** — async, auto-generated OpenAPI docs at `/docs`, Pydantic-native.
  Chosen over Flask (sync, no schema/docs) because the response model *is* the
  contract and it is validated for free. Routes import the canonical schemas from
  `src/models/schemas.py` (no duplication).
- **Streamlit** — fastest path to a clean, deployable demo UI with a progress
  display and PDF download; chosen over a React SPA (build tooling, hosting) and
  Gradio (less layout control) because the goal is a zero-friction portfolio demo,
  not a product frontend.
- **Tradeoff:** Streamlit re-runs top-to-bottom and isn't a general web framework;
  fine for a single-page analyst tool.

## 11. Single-container Docker + Compose; HuggingFace Spaces deploy

- **Problem:** Run FastAPI + Streamlit reproducibly and host them free, 24/7.
- **Chosen:** one image (root `Dockerfile`) running both via `start.sh` (API in the
  background, health-gated, then Streamlit in the foreground); a root
  `docker-compose.yml` for `docker-compose up`; deployed to **HuggingFace Spaces**
  (`sdk: docker`, `app_port: 7860`).
- **Tradeoffs:** two processes in one container (against the "one process per
  container" ideal) — but it matches the single-Space deployment model and keeps
  the demo one click. The embedding model is baked into the image at build time so
  cold starts don't pay a 2–3 minute model download.
- **Evidence:** both `Dockerfile` and `docker/Dockerfile.ui` build clean; the
  running container serves `/health` and Docker `HEALTHCHECK` reports `healthy`
  (WORKLOG 2026-07-15). HF Spaces gives free, permanent, HTTPS hosting with no
  spin-down.

## 12. LLM access: OpenRouter (model-agnostic router)

- **Problem:** Access capable LLMs at zero/low cost without locking to one vendor.
- **Alternatives:** direct OpenAI/Anthropic (paid), a single hosted OSS model,
  OpenRouter.
- **Chosen:** OpenRouter via the OpenAI SDK (`src/utils/llm_client.py`), one key
  for many models. System messages are normalized into the first user turn because
  some free models reject the `system` role (HTTP 400).
- **Tradeoffs:** router latency/availability varies; free models are weaker.
- **Why:** one API key, swappable models, and a free tier. It is also the primary
  **evaluation** provider (§14). The account is now beyond free-tier (~1,000
  req/day), so full-set evaluation is practical.

## 13. Evaluation: RAGAS as the LLM-judged quality gate

- **Problem:** Quantify faithfulness/relevancy of a RAG system, not just "looks
  fine".
- **Alternatives:** hand-graded spot checks; BLEU/ROUGE (wrong tool for RAG);
  RAGAS.
- **Chosen:** RAGAS (faithfulness, answer relevancy, context precision/recall) with
  a fail-loud gate (`run_evaluation` raises on NaN instead of writing NaN as a
  "result"), configurable judge, and a thresholded pass/fail.
- **Tradeoffs:** RAGAS is judge-dependent and noisy on weak judges;
  `context_precision` is parse-fragile and can come back `null`. These are reported
  honestly, not zeroed.
- **Evidence / why:** the all-NaN failure that once made this pipeline useless was
  root-caused (dependency drift → `langchain-core` pinned; native `ChatOllama`
  path unparseable → local judge uses Ollama's `/v1` via `ChatOpenAI`; silent NaN →
  fail-loud). RAGAS now produces real numbers on the strongest free judge.

## 14. Deterministic retrieval benchmarks (the trust anchor)

- **Problem:** LLM-judged metrics are noisy and rate-limited — a poor basis for
  iterating on retrieval.
- **Chosen:** a **deterministic, LLM-free** benchmark (`benchmark.py`): retrieval
  precision@k (right-company chunks), keyword-recall, latency mean/p95, success
  rate — computed over *all* questions, unlimited, reproducible. RAGAS runs on top
  (capped only when needed).
- **Tradeoffs:** these are proxies (company-level precision, lexical recall), not
  human relevance judgments. But they are objective and repeatable, and every
  metric ships with a documented meaning/limitation in the generated report.
- **Why:** every retrieval decision in §7–§8 was made on these metrics precisely
  because they are cheap and noise-free. This is the "retrieval → benchmark"
  evidence line, and it is what lets the repo claim improvements instead of
  asserting them.

## 15. CI/CD: GitHub Actions

- **Chosen:** on every push/PR, run ruff + black + mypy + pytest + a Docker build
  (`.github/workflows/ci.yml`). Tests are network-free (all HTTP/LLM mocked) so CI
  needs no secrets.
- **Tradeoffs:** CI does **not** run the RAGAS eval/benchmark — those need a judge
  (quota/Ollama) and are flaky/slow, so they are manual/local by design.
- **Why:** CI enforces the mechanical DoD gates (the "quality → CI" evidence) while
  keeping runs fast, deterministic, and secret-free.

## 16. Zero-cost engineering as a design constraint

Every component was chosen to keep total infra cost at **$0** while staying
production-shaped:

| Need | Free choice | Why it's enough |
|---|---|---|
| Financial data | SEC EDGAR (XBRL + filings) | official, no key, no limits |
| LLM inference | OpenRouter free/low models | one key, model-agnostic |
| Embeddings | `all-MiniLM-L6-v2` (local, 80MB) | CPU, no API cost, baked into image |
| Vector store | ChromaDB (local persistent) | zero-setup, in-container |
| Observability | Langfuse free tier | 50k traces/mo, best-effort |
| Hosting | HuggingFace Spaces (Docker) | free, permanent, HTTPS, no spin-down |
| CI | GitHub Actions | free for public repos |

The constraint is a feature: it forces defensible, well-understood building blocks
and makes the whole system reproducible by anyone with free accounts.

---

### How to reproduce the evidence behind these decisions

```bash
make lint && make test        # correctness/quality gates
make benchmark                # retrieval + RAGAS metrics → evaluation/results/
docker build -t finsight . && \
  docker run -p 8000:8000 -e OPENROUTER_API_KEY=... finsight   # deployment
```

See `evaluation/results/benchmark_latest.md` for the current numbers and
`WORKLOG.md` for the dated decision trail.
