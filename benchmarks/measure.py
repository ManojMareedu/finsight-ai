"""Local performance measurements for retrieval, embedding, and concurrency.

Stdlib only (time, statistics, concurrent.futures, argparse, platform). No
network calls, no LLM calls — this only exercises the local Chroma store and
the local SentenceTransformer model already loaded by the app.

Run from the repo root with the project venv:

    .venv/bin/python -m benchmarks.measure --company Apple

This is a script, not a pytest suite: it takes tens of seconds to run and its
numbers depend on the machine and the current contents of data/chroma. See
docs/performance.md for the last recorded run and how to read it.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def percentile(data: list, p: float) -> float:
    """Linear-interpolated percentile, p in [0, 100]. No numpy dependency."""
    if not data:
        raise ValueError("percentile of empty data")
    s = sorted(data)
    if len(s) == 1:
        return s[0]
    rank = (p / 100) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def summarize(label: str, latencies: list) -> dict:
    result = {
        "label": label,
        "n": len(latencies),
        "mean": statistics.mean(latencies),
        "p50": percentile(latencies, 50),
        "p95": percentile(latencies, 95),
        "min": min(latencies),
        "max": max(latencies),
    }
    print(
        f"{label}: n={result['n']} mean={result['mean']:.3f}s "
        f"p50={result['p50']:.3f}s p95={result['p95']:.3f}s "
        f"min={result['min']:.3f}s max={result['max']:.3f}s"
    )
    return result


def chroma_store_size(persist_dir: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(persist_dir):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    return total


def hardware_info(persist_dir: str) -> dict:
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
        "chroma_store_bytes": chroma_store_size(persist_dir),
    }
    print(f"hardware: {json.dumps(info)}")
    return info


QUERIES = [
    "What are the main risk factors?",
    "Summarize revenue growth for the most recent fiscal year",
    "What does the company say about supply chain risk?",
    "Describe competitive pressures mentioned in the filing",
    "What litigation or legal proceedings are disclosed?",
    "How does the company describe its liquidity position?",
]


def measure_retrieval(company: str, warm_n: int) -> dict:
    from src.rag.retriever import retrieve_context

    t0 = time.perf_counter()
    retrieve_context(QUERIES[0], company=company)
    cold = time.perf_counter() - t0
    print(f"retrieval cold (first call, includes model load): {cold:.3f}s")

    warm = []
    for i in range(warm_n):
        q = QUERIES[i % len(QUERIES)]
        t0 = time.perf_counter()
        retrieve_context(q, company=company)
        warm.append(time.perf_counter() - t0)

    return {"cold_seconds": cold, "warm": summarize("retrieval warm", warm)}


def measure_embedding_throughput(n_chunks: int, chunk_chars: int) -> dict:
    from src.rag.embeddings import get_embeddings

    text = ("The company reported quarterly results reflecting steady demand. " * 20)[:chunk_chars]
    chunks = [text] * n_chunks

    embeddings = get_embeddings()
    embeddings.embed_documents(chunks[:4])  # warm the model once, not part of the number

    t0 = time.perf_counter()
    embeddings.embed_documents(chunks)
    elapsed = time.perf_counter() - t0
    rate = n_chunks / elapsed
    print(f"embedding throughput: {n_chunks} chunks in {elapsed:.3f}s " f"= {rate:.1f} chunks/sec")
    return {"n_chunks": n_chunks, "elapsed_seconds": elapsed, "chunks_per_sec": rate}


def measure_concurrency_retrieval(company: str, levels: list) -> dict:
    from src.rag.retriever import retrieve_context

    def run_one(i: int) -> float:
        t0 = time.perf_counter()
        retrieve_context(QUERIES[i % len(QUERIES)], company=company)
        return time.perf_counter() - t0

    results = {}
    for level in levels:
        with ThreadPoolExecutor(max_workers=level) as pool:
            t0 = time.perf_counter()
            latencies = list(pool.map(run_one, range(level * 3)))
            wall = time.perf_counter() - t0
        results[level] = {
            "wall_seconds": wall,
            "per_call_mean": statistics.mean(latencies),
            "per_call_p95": percentile(latencies, 95),
        }
        print(
            f"retrieval concurrency={level}: wall={wall:.3f}s "
            f"per-call mean={results[level]['per_call_mean']:.3f}s "
            f"p95={results[level]['per_call_p95']:.3f}s"
        )
    return results


def measure_concurrency_embedding(n_chunks: int, levels: list) -> dict:
    from src.rag.embeddings import get_embeddings

    embeddings = get_embeddings()
    text = ("The company reported quarterly results reflecting steady demand. " * 20)[:1000]
    chunks = [text] * n_chunks
    embeddings.embed_documents(chunks[:4])  # warm once

    def run_one(_i: int) -> float:
        t0 = time.perf_counter()
        embeddings.embed_documents(chunks)
        return time.perf_counter() - t0

    results = {}
    for level in levels:
        with ThreadPoolExecutor(max_workers=level) as pool:
            t0 = time.perf_counter()
            latencies = list(pool.map(run_one, range(level)))
            wall = time.perf_counter() - t0
        results[level] = {
            "wall_seconds": wall,
            "per_call_mean": statistics.mean(latencies),
            "chunks_per_sec_aggregate": (n_chunks * level) / wall,
        }
        print(
            f"cold-ingest concurrency={level}: wall={wall:.3f}s "
            f"per-call mean={results[level]['per_call_mean']:.3f}s "
            f"aggregate={results[level]['chunks_per_sec_aggregate']:.1f} chunks/sec"
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--company", default="Apple", help="metadata filter value present in the store"
    )
    parser.add_argument("--warm-n", type=int, default=30)
    parser.add_argument(
        "--cold-chunks", type=int, default=180, help="simulated cold-ingestion chunk count"
    )
    parser.add_argument("--retrieval-levels", type=int, nargs="+", default=[1, 2, 4, 8, 16, 32])
    parser.add_argument("--embedding-levels", type=int, nargs="+", default=[1, 2, 3, 4])
    parser.add_argument("--skip-concurrency", action="store_true")
    parser.add_argument("--out", help="write full results as JSON to this path")
    args = parser.parse_args()

    persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "./data/chroma")
    results: dict = {"hardware": hardware_info(persist_dir)}

    results["retrieval"] = measure_retrieval(args.company, args.warm_n)
    results["embedding_throughput"] = measure_embedding_throughput(args.cold_chunks, 1000)

    if not args.skip_concurrency:
        results["retrieval_concurrency"] = measure_concurrency_retrieval(
            args.company, args.retrieval_levels
        )
        results["embedding_concurrency"] = measure_concurrency_embedding(
            args.cold_chunks, args.embedding_levels
        )

    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
