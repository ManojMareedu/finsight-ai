# Performance: measured, not estimated

Numbers below come from `benchmarks/measure.py`, run against the live local
Chroma store. Reproduce with:

```
.venv/bin/python -m benchmarks.measure --company Apple
```

## Hardware and store, as measured

| | |
|---|---|
| Machine | Apple M4, 10 CPU cores (developer laptop, **not** the production host) |
| Python | 3.12.12 |
| Chroma store | `data/chroma`, 8.7 MB, 568 chunks, single company (Apple) |
| Production host | HuggingFace Spaces free Docker tier, 2 vCPU, x86 |

The production Space is a 2 vCPU x86 container. This machine is a 10-core Apple
Silicon laptop. Absolute throughput numbers below do **not** transfer — they are
labeled local and are used only to check the *shape* of the previous estimates
(does latency scale the way claimed, does concurrency hit a wall the way
claimed), not to restate them as production numbers.

## Retrieval latency (`retrieve_context`, company filter applied)

| | cold (first call, includes model load) | warm (n=30) |
|---|---|---|
| mean | 7.384s | 0.034s |
| p50 | - | 0.008s |
| p95 | - | 0.252s |

**Refutes the prior 0.645s mean estimate** — warm retrieval on this machine and
this store is roughly 20x faster than that figure. The gap is explained by
store size (568 chunks / one company here, vs whatever the prior benchmark's
store held) and by hardware (M4 vs 2 vCPU x86). The prior number should not be
trusted as a general figure; recorded here as machine+store-specific evidence
that retrieval itself is not the dominant cost in a request. The p95/mean gap
(0.252s vs 0.034s) is real and worth tracking as the store grows — it's the
lru_cache'd embedding model paying occasional lazy-init cost on first use per
query shape, not the vector search.

## Embedding throughput

`SentenceTransformer.encode`, batch of 180 chunks (1000 chars each, the
production chunk size from `src/rag/ingestion.py`):

- **249.2 chunks/sec**, 180 chunks in 0.722s.
- A cold-company ingestion (~180 chunks from a 150k-char filing) costs well
  under 1 second of CPU-bound embedding time **on this machine**.

## End-to-end latency: what's measured here vs recorded elsewhere

Only retrieval and embedding are measured in this document — both are local,
free, and reproducible. EDGAR fetch, LLM calls (2-4 per analysis, more if the
risk-reassessment loop fires), and final synthesis are **not measured here** to
avoid spending LLM quota that F's eval run needs. Those figures are taken
as-is from the recorded eval data: **17.4s mean / 33.4s p95 total** per
analysis.

Given warm retrieval costs ~0.03s and even a cold embedding pass costs <1s on
this hardware, the 17.4s/33.4s total is dominated by EDGAR fetch and LLM calls,
not by the RAG path. There is no per-stage timing instrumentation in the
pipeline today to split EDGAR from LLM from synthesis precisely — see the Log
entry recommending a timer around each graph node.

## Concurrency: retrieval

`ThreadPoolExecutor`, 3 calls per worker at each level, same store as above:

| concurrency | wall (s) | per-call mean | per-call p95 |
|---|---|---|---|
| 1 | 0.021 | 0.007 | 0.008 |
| 2 | 0.044 | 0.015 | 0.017 |
| 4 | 0.107 | 0.035 | 0.044 |
| 8 | 0.535 | 0.171 | 0.206 |
| 16 | 1.285 | 0.394 | 0.502 |
| 32 | 2.878 | 0.915 | 1.119 |

No cliff on this hardware — per-call latency degrades roughly linearly with
concurrency, still under 1.2s at 32-way concurrency against a 568-chunk store.
Retrieval concurrency is not where this system breaks, on this machine or
(by extension) on the production one — the store is tiny and the query-side
embedding is one short string.

## Concurrency: cold embedding (the "CPU wall" claim)

Simulated by running `embed_documents` on a fresh batch of 180 chunks
concurrently, N threads at once (each thread doing a full cold-ingestion-sized
encode):

| concurrency | wall (s) | per-call mean | aggregate chunks/sec |
|---|---|---|---|
| 1 | 0.475 | 0.475 | 379.2 |
| 2 | 0.932 | 0.929 | 386.1 |
| 3 | 1.348 | 1.343 | 400.5 |
| 4 | 1.870 | 1.866 | 385.0 |

**This confirms the mechanism, not the specific ceiling.** Aggregate throughput
is flat (~380-400 chunks/sec) regardless of concurrency level — a single
`encode()` call already saturates the CPU resources `torch` will use, so
concurrent cold-ingestion requests do not run in parallel, they queue behind
each other at effectively the same total throughput. That is exactly the
"CPU wall, not a threadpool wall" mechanism the prior estimate claimed.

What does **not** transfer is the specific "~2-3 concurrent" number. This
machine has 10 cores and shows the wall at concurrency=1 already (one `encode`
call uses most of the available cores); a 2 vCPU box has an order of magnitude
less headroom per call, so if anything the prior estimate of "~2-3 concurrent
cold" is optimistic, not conservative — a single cold-ingestion request on a
2 vCPU box is closer to the whole CPU budget than one is here. This is
reasoning from the measured mechanism, not a second measurement — it is
explicitly not a production number.

## What was not measured, and why

- **EDGAR fetch, LLM calls, synthesis** — no network/LLM calls were made for
  this benchmark; would burn shared free-tier quota F needs for the real eval
  run. Their contribution to the 17.4s/33.4s total is inferred by subtraction,
  not measured directly.
- **Production (2 vCPU) hardware** — measured on a developer Mac only, per the
  task constraint. Where a production number is claimed elsewhere in this repo
  (docs/scaling.md, README), it is derived by reasoning about the ratio, not by
  running on the Space.
- **Multi-company store at realistic scale** — the local store has 568 chunks
  (one company). Retrieval latency at the scale of dozens of ingested
  companies is not measured; HNSW search cost grows sub-linearly but not
  free, and this is worth re-measuring once the store is bigger.
