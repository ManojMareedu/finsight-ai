# Scaling path, costed

Numbers referenced here are from `docs/performance.md`. This is the order
things break as load rises on the current design, and what fixing each one
costs. See `audit/JUDGE_REVIEW.md` "Scalability verdict" for the original
estimate this replaces.

## What today's design actually supports

Single container, single `uvicorn` worker (`start.sh --workers 1`), one
`PersistentClient` per process against a local SQLite-backed Chroma directory,
no queue, no cache, synchronous request handling end to end.

- **Warm requests** (company already ingested): retrieval and embedding are
  not the bottleneck — warm retrieval measured at 0.034s mean, 0.252s p95
  locally. The 17.4s mean / 33.4s p95 recorded end-to-end figure is EDGAR +
  LLM + synthesis, not RAG.
- **Cold requests** (new company, ~180 chunks to embed): embedding itself is
  cheap even on modest hardware — well under a second measured locally — but
  it is CPU-bound and a single cold-ingestion call already uses most of what a
  2 vCPU box has to offer (see the concurrency-embedding table in
  `docs/performance.md`). Concurrent cold requests do not parallelize; they
  queue at roughly constant aggregate throughput.
- **Free-tier LLM**: 2-4 calls per analysis, tripled if the risk-reassessment
  loop fires, against a low-hundreds-of-requests/day OpenRouter free-tier cap
  with no backoff (F7 in the audit). This is a hard ceiling regardless of
  anything below.

## What breaks first, second, third

1. **CPU on cold ingestion, first.** A cold company's embedding pass already
   uses most of a 2 vCPU box's capacity per request (reasoned from the flat
   aggregate-throughput measurement in `docs/performance.md` — not measured on
   that hardware directly). A second concurrent cold request does not run in
   parallel with the first; it queues behind it. This is a hardware wall, not
   a threadpool setting — raising `--workers` does not fix it, it just means
   more processes competing for the same two cores.
2. **Single SQLite-backed Chroma writer, second.** Two concurrent cold
   requests are two concurrent writers to one file-backed store. One client
   per process (`src/rag/chroma_client.py`) removes the *within-process*
   version of this problem; it does nothing for a second `uvicorn` worker
   process, which would open a second `PersistentClient` against the same
   path. Multiple workers is exactly the setup G is documenting as unsafe for
   in-process job/cache/limiter state (item 32) — the same rewrite-not-tuning
   problem applies to Chroma.
3. **Free-tier LLM throughput, third but binding.** Even with unlimited CPU
   and a networked store, the OpenRouter free tier caps total daily request
   volume. This is the ceiling on how many analyses/day the system can serve
   no matter what else is fixed — it does not move without spending money.
4. **Threadpool exhaustion, last and rarely reached.** The `~40`-worker
   default threadpool the prior audit named as the limit is only reachable on
   the warm path, where ingestion is skipped and the per-request cost is
   dominated by EDGAR/LLM wait time, not CPU. It is a real ceiling, but three
   other constraints bind first.
5. **Horizontal scaling: 0 replicas today.** A second replica gets its own
   local Chroma directory and its own in-process job/cache/limiter state
   (item 32). Adding a second container does not add capacity today; it adds
   a second, inconsistent copy of the system.

## Remediation costs

| Fix | Cost | What it buys | What it doesn't |
|---|---|---|---|
| Multiple `uvicorn` workers | $0 | More warm-path concurrency (helps constraint 4) | Nothing for cold-path CPU (constraint 1); breaks in-process job/cache/rate-limiter state across workers (G, item 32) unless that state moves out of process first |
| Networked vector store (Qdrant/pgvector, managed or self-hosted) | Money (hosting) or ops time (self-hosted) | Removes the one-client-per-path constraint (2), enables real multi-replica retrieval | Does not touch CPU-bound embedding (1) or the LLM ceiling (3) |
| Job queue (Celery/RQ + broker, or a hosted queue) | Money or ops time | Turns synchronous 45-135s holds into background jobs; lets the UI stop blocking on the 180s client timeout | Does not raise the LLM throughput ceiling (3); still queues, just more gracefully |
| Paid LLM capacity | Money, and it is the current hard ceiling | Directly raises constraint 3, the binding one regardless of everything else | Does nothing for CPU-bound cold ingestion (1) or storage (2) |

## The honest bottom line

The free-tier LLM cap is the binding constraint on throughput today, and stays
binding after every $0 fix above is applied. Multiple workers, a networked
store, and a job queue all improve *how gracefully* the system behaves as
concurrent users show up — none of them raise the ceiling on analyses/day,
because that ceiling is the LLM provider's, not this codebase's. The one line
item that actually moves the daily-volume ceiling is paid LLM capacity. Every
other fix is worth doing for latency and correctness under load, not for
raw throughput.

At $0, today's design is a well-built single-user-at-a-time demo: fast warm
path, cheap local embedding, and a hard, provider-imposed cap on how many
analyses it can run in a day. The next dollar spent should go to LLM capacity
before infrastructure, because infrastructure changes without it just let more
requests queue faster in front of the same ceiling.
