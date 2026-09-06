# src/evaluation/benchmark.py
"""Comprehensive, reproducible retrieval + RAG benchmark.

Produces objective evidence for FinSight's retrieval and generation quality and
writes a timestamped JSON + Markdown report to ``evaluation/results/``.

Two metric families:

* **Deterministic** (no LLM, unlimited, zero-noise): company-filter integrity,
  retrieval hit rate, keyword recall, latency (mean / p95), success rate. These
  drive optimization because they are cheap and reproducible.
* **RAGAS** (LLM judge): faithfulness, answer relevancy, context precision,
  context recall. Every RAGAS metric carries the number of rows the judge
  actually scored, and a metric scored on fewer than ``MIN_COVERAGE`` of the
  rows is reported as ``null`` with its coverage instead of being averaged over
  the survivors.

Retrieval runs with the production company filter and the corpus is pinned by
accession number (``snapshot.json``), so the numbers describe the shipped
configuration and two runs are comparable.

Run: ``python -m src.evaluation.benchmark`` (honors the same RAGAS_* settings as
the eval pipeline: judge provider/model, sample cap, timeout).
"""
import datetime
import json
import logging
import math
import os
import re
import time
from typing import Any, Optional

from src.evaluation.dataset import EMBEDDING_MODEL, load_golden, load_snapshot
from src.evaluation.ragas_eval import (
    MIN_COVERAGE,
    _build_judge_llm,
    _eval_answer,
    score_with_coverage,
)
from src.rag.retriever import retrieve_context
from src.utils.config import Settings, get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RESULTS_DIR = "evaluation/results"

# Every run appends one line here and nothing ever rewrites it, so a regression
# is visible as a drop against the previous line rather than only as a failed
# absolute threshold. The schema is flat and versioned so two runs months apart
# stay comparable.
HISTORY_PATH = os.path.join(RESULTS_DIR, "history.jsonl")
HISTORY_SCHEMA = 1

# How far a tracked metric may fall below the previous run before it counts as
# a regression. Wide enough to absorb the approximate-HNSW jitter documented in
# the README (~0.02) and free-judge noise, narrow enough to catch a real drop.
REGRESSION_TOLERANCE = 0.05

# A question counts as "hit" when one retrieved chunk carries at least this
# share of the ground truth's content words.
HIT_THRESHOLD = 0.5

# Minimal stopword set for the keyword-overlap recall proxy.
_STOPWORDS = set(
    "the a an and or of for to in on at is are as its their with by from that this "
    "key cites approximately including reported showed significant".split()
)

# Documentation for every reported metric: what it measures, why it matters, an
# acceptable range, and its limitations. Rendered into the Markdown report so a
# reviewer can interpret the numbers without reading the code.
METRIC_DOCS: dict[str, dict[str, str]] = {
    "company_filter_integrity": {
        "measures": "Fraction of retrieved chunks belonging to the company the "
        "question is about, under the production `company=` metadata filter.",
        "why": "This is a correctness check on the filter, not a quality score: "
        "production always filters by company, so anything below 1.0 means the "
        "filter or the chunk metadata is broken.",
        "acceptable": "Exactly 1.0. Treat any other value as a bug, not a trend.",
        "limits": "By construction it cannot fail for a working filter, which is "
        "why retrieval quality is measured by hit rate and recall instead.",
    },
    "retrieval_hit_rate": {
        "measures": "Fraction of questions where at least one retrieved chunk "
        f"contains >= {HIT_THRESHOLD:.0%} of the ground truth's content words.",
        "why": "A per-question hit/miss the company filter cannot satisfy for "
        "free — the right company's chunks still have to contain the answer.",
        "acceptable": ">= 0.60 on this mixed numeric/qualitative set.",
        "limits": "Lexical. Exact figures live in XBRL rather than the 10-K text "
        "the corpus holds, so numeric questions miss more often than they should.",
    },
    "retrieval_recall": {
        "measures": "Mean fraction of ground-truth content words present in the "
        "concatenated retrieved chunks (keyword-overlap proxy).",
        "why": "Approximates whether the answer-supporting text was retrieved, "
        "with no LLM cost.",
        "acceptable": ">= 0.70 for the qualitative questions in this set.",
        "limits": "Lexical, not semantic; and exact financial figures live in "
        "XBRL (fetched separately), not the 10-K text, so numeric questions "
        "cannot reach 1.0 here.",
    },
    "success_rate": {
        "measures": "Fraction of questions that produced a non-empty answer with "
        "no exception in retrieval or generation.",
        "why": "Reliability of the end-to-end path under the current judge/config.",
        "acceptable": "1.0 expected; anything lower is a reliability regression.",
        "limits": "Does not judge answer quality — only that the pipeline ran.",
    },
    "latency_total_p95_s": {
        "measures": "95th-percentile end-to-end latency (retrieval + generation) "
        "per question, in seconds.",
        "why": "Tail latency is what users feel; p95 is more honest than the mean.",
        "acceptable": "Dominated by the LLM; local Ollama is slower than hosted.",
        "limits": "Small N makes p95 coarse; embedding model load excluded (warm).",
    },
    "faithfulness": {
        "measures": "RAGAS: are the answer's claims supported by the retrieved "
        "context (no hallucination)?",
        "why": "The core trust metric for a RAG system.",
        "acceptable": ">= 0.70.",
        "limits": "Judge-dependent; weak judges are noisy. Read the scored-N "
        "column before reading the value.",
    },
    "answer_relevancy": {
        "measures": "RAGAS: how well the answer addresses the question.",
        "why": "Catches evasive or off-topic answers.",
        "acceptable": ">= 0.65.",
        "limits": "Depends on the generated answer, not retrieval; noisiest metric "
        "on weak local judges.",
    },
    "context_precision": {
        "measures": "RAGAS: are the retrieved contexts relevant to the ground "
        "truth, and ranked well?",
        "why": "LLM-judged retrieval precision.",
        "acceptable": ">= 0.60.",
        "limits": "Most call-heavy RAGAS metric and the most parse-fragile; treat "
        "with caution on free judges.",
    },
    "context_recall": {
        "measures": "RAGAS: is the ground truth attributable to the retrieved " "contexts?",
        "why": "LLM-judged retrieval recall.",
        "acceptable": ">= 0.60.",
        "limits": "Judge-dependent; small N is noisy.",
    },
}


def append_history(kind: str, timestamp: str, metrics: dict, context: dict) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    row = {
        "schema": HISTORY_SCHEMA,
        "kind": kind,
        "timestamp": timestamp,
        "metrics": metrics,
        "context": context,
    }
    # A run killed mid-write leaves a partial line; start a new one rather than
    # gluing this row onto it and losing both.
    partial = os.path.exists(HISTORY_PATH) and os.path.getsize(HISTORY_PATH) > 0
    if partial:
        with open(HISTORY_PATH, "rb") as f:
            f.seek(-1, os.SEEK_END)
            partial = f.read(1) != b"\n"
    with open(HISTORY_PATH, "a") as f:
        f.write(("\n" if partial else "") + json.dumps(row, sort_keys=True) + "\n")


def load_history(kind: str) -> list[dict]:
    if not os.path.exists(HISTORY_PATH):
        return []
    rows = []
    with open(HISTORY_PATH) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                # A half-written line from a killed run must not cost us the
                # rest of the history.
                logger.warning("Skipping unparseable history line")
                continue
            if row.get("kind") == kind and row.get("schema") == HISTORY_SCHEMA:
                rows.append(row)
    return rows


def regressions(current: dict, previous: Optional[dict]) -> list[str]:
    """Metrics that dropped against the previous run of the same kind."""
    if not previous:
        return []
    found = []
    for name, value in sorted(current.items()):
        before = previous.get("metrics", {}).get(name)
        if before is None or value is None:
            continue
        if value < before - REGRESSION_TOLERANCE:
            found.append(
                f"{name}: {value:.4f}, down from {before:.4f} on "
                f"{previous.get('timestamp', 'the previous run')}"
            )
    return found


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z]{4,}", text.lower()) if w not in _STOPWORDS}


def _percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (p in 0..100). Robust for small samples."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, math.ceil(p / 100.0 * len(s)) - 1)
    return round(s[min(idx, len(s) - 1)], 4)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def collect_with_metrics(golden: list, settings: Settings) -> tuple[list, list]:
    """Retrieve + generate per item, timing each stage and computing the
    deterministic retrieval metrics. Returns ``(ragas_rows, per_item_records)``."""
    ragas_rows: list[dict] = []
    records: list[dict] = []
    for i, item in enumerate(golden):
        logger.info(f"[{i+1}/{len(golden)}] {item['question'][:60]}...")
        rec: dict[str, Any] = {"question": item["question"], "success": False}
        try:
            t0 = time.perf_counter()
            # Same call production makes (filing_agent.py) — an unfiltered
            # retrieval measures a configuration that never ships.
            docs = retrieve_context(item["question"], company=item["company"])
            rec["retrieval_latency_s"] = round(time.perf_counter() - t0, 4)

            contexts = [d.page_content for d in docs]
            companies = [d.metadata.get("company", "?") for d in docs]
            target = item["company"]
            rec["k"] = len(docs)
            rec["company_filter_integrity"] = (
                round(sum(c == target for c in companies) / len(companies), 4) if companies else 0.0
            )
            gt_words = _content_words(item["ground_truth"])
            joined = " ".join(contexts).lower()
            rec["retrieval_recall"] = (
                round(sum(w in joined for w in gt_words) / len(gt_words), 4) if gt_words else 0.0
            )
            best_chunk = max(
                (len(gt_words & _content_words(c)) / len(gt_words) for c in contexts),
                default=0.0,
            )
            rec["retrieval_hit"] = 1.0 if gt_words and best_chunk >= HIT_THRESHOLD else 0.0

            t1 = time.perf_counter()
            answer = _eval_answer(item["question"], contexts, settings) if contexts else ""
            rec["generation_latency_s"] = round(time.perf_counter() - t1, 4)
            rec["total_latency_s"] = round(
                rec["retrieval_latency_s"] + rec["generation_latency_s"], 4
            )
            rec["success"] = bool(answer and answer.strip())

            if contexts and rec["success"]:
                ragas_rows.append(
                    {
                        "question": item["question"],
                        "answer": answer,
                        "contexts": contexts,
                        "ground_truth": item["ground_truth"],
                    }
                )
        except Exception as e:  # noqa: BLE001 - benchmark must not crash on one item
            logger.warning(f"Item failed: {e}")
            rec["error"] = str(e)
        records.append(rec)
    return ragas_rows, records


def compute_ragas(ragas_rows: list, settings: Settings) -> dict[str, dict]:
    """Run RAGAS NaN-tolerant and report each metric with its denominator.

    A metric the judge failed to score on most rows is suppressed rather than
    averaged over the rows that happened to parse.
    """
    from datasets import Dataset
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from ragas.run_config import RunConfig

    from src.rag.embeddings import get_embeddings

    scores = evaluate(
        Dataset.from_list(ragas_rows),
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=_build_judge_llm(settings),
        embeddings=LangchainEmbeddingsWrapper(get_embeddings()),
        run_config=RunConfig(
            timeout=settings.ragas_timeout, max_workers=settings.ragas_max_workers
        ),
        raise_exceptions=False,
    )
    df = scores.to_pandas()
    return {
        key: score_with_coverage(list(df[key]))
        for key in ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
    }


def run_benchmark(include_ragas: bool = True) -> dict:
    settings = get_settings()
    golden = load_golden()
    snapshot = load_snapshot()

    # Deterministic metrics (retrieval, latency, success) cover ALL questions —
    # they are free. RAGAS is capped separately (ragas_max_samples) because it is
    # the expensive, rate-limited part.
    logger.info(f"Benchmarking {len(golden)} questions...")
    ragas_rows, records = collect_with_metrics(golden, settings)

    ok = [r for r in records if r.get("success")]
    retr_lat = [r["retrieval_latency_s"] for r in records if "retrieval_latency_s" in r]
    total_lat = [r["total_latency_s"] for r in records if "total_latency_s" in r]

    report: dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "num_questions": len(golden),
        "config": {
            "judge_provider": settings.ragas_judge_provider,
            "judge_model": (
                settings.ragas_judge_model
                if settings.ragas_judge_provider == "openrouter"
                else settings.ragas_ollama_model
            ),
            "answer_model": (
                settings.primary_model
                if settings.ragas_judge_provider == "openrouter"
                else settings.ragas_ollama_model
            ),
            "temperature": 0,
            "embedding_model": EMBEDDING_MODEL,
            "snapshot_pinned_at": snapshot["pinned_at"],
            "min_coverage": MIN_COVERAGE,
        },
        "retrieval": {
            "company_filter_integrity": _mean(
                [r["company_filter_integrity"] for r in records if "company_filter_integrity" in r]
            ),
            "hit_rate": _mean([r["retrieval_hit"] for r in records if "retrieval_hit" in r]),
            "recall": _mean([r["retrieval_recall"] for r in records if "retrieval_recall" in r]),
            "k": records[0].get("k") if records else None,
        },
        "reliability": {
            "success_rate": round(len(ok) / len(records), 4) if records else 0.0,
            "successful": len(ok),
            "total": len(records),
        },
        "latency_s": {
            "retrieval_mean": _mean(retr_lat),
            "retrieval_p95": _percentile(retr_lat, 95),
            "total_mean": _mean(total_lat),
            "total_p95": _percentile(total_lat, 95),
        },
        "ragas": None,
        "per_item": records,
    }

    tracked = {
        "company_filter_integrity": report["retrieval"]["company_filter_integrity"],
        "retrieval_hit_rate": report["retrieval"]["hit_rate"],
        "retrieval_recall": report["retrieval"]["recall"],
        "success_rate": report["reliability"]["success_rate"],
    }

    if include_ragas and ragas_rows:
        cap = settings.ragas_max_samples
        rows = ragas_rows[:cap] if cap and cap > 0 else ragas_rows
        report["ragas_num_samples"] = len(rows)
        logger.info("Running RAGAS on %d rows...", len(rows))
        try:
            report["ragas"] = compute_ragas(rows, settings)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"RAGAS stage failed entirely: {e}")
            report["ragas"] = {"error": str(e)}

    for name, metric in (report.get("ragas") or {}).items():
        if isinstance(metric, dict) and metric.get("value") is not None:
            tracked[name] = metric["value"]

    previous = load_history("benchmark")
    report["regressions"] = regressions(tracked, previous[-1] if previous else None)
    report["compared_against"] = previous[-1]["timestamp"] if previous else None
    append_history("benchmark", report["timestamp"], tracked, report["config"])

    _write_reports(report)
    return report


def _fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def _ragas_row(name: str, metric: Optional[dict]) -> str:
    """One RAGAS table row: value, the N it was scored on, and its coverage.

    A suppressed metric shows why it is missing instead of showing a number
    computed from whichever rows survived the judge.
    """
    if not metric:
        return f"| RAGAS {name} | n/a | 0/0 | n/a |"
    scored, total = metric["scored"], metric["total"]
    value = (
        _fmt(metric["value"])
        if metric["value"] is not None
        else f"suppressed (< {MIN_COVERAGE:.0%} coverage)"
    )
    return f"| RAGAS {name} | {value} | {scored}/{total} | {metric['coverage']:.2f} |"


def render_markdown(report: dict) -> str:
    r, lat, rel = report["retrieval"], report["latency_s"], report["reliability"]
    ragas = report.get("ragas") or {}
    cfg = report["config"]
    lines = [
        "# FinSight AI — Benchmark Report",
        "",
        f"- **Generated:** {report['timestamp']}",
        f"- **Questions:** {report['num_questions']}",
        f"- **Judge:** `{cfg['judge_provider']}` / `{cfg['judge_model']}`",
        f"- **Answer model:** `{cfg['answer_model']}` at temperature {cfg['temperature']}",
        f"- **Embeddings:** `{cfg['embedding_model']}`",
        f"- **Corpus pinned at:** {cfg['snapshot_pinned_at']}",
        f"- **RAGAS samples:** {report.get('ragas_num_samples', 'n/a')} "
        f"(deterministic metrics cover all {report['num_questions']})",
        "",
        "## Deterministic results (no LLM)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Company filter integrity (k={r['k']}) | {_fmt(r['company_filter_integrity'])} |",
        f"| Retrieval Hit Rate | {_fmt(r['hit_rate'])} |",
        f"| Retrieval Recall | {_fmt(r['recall'])} |",
        f"| Success Rate | {_fmt(rel['success_rate'])} ({rel['successful']}/{rel['total']}) |",
        f"| Latency total (mean) | {_fmt(lat['total_mean'])}s |",
        f"| Latency total (p95) | {_fmt(lat['total_p95'])}s |",
        f"| Latency retrieval (mean) | {_fmt(lat['retrieval_mean'])}s |",
        "",
        "## Regression against the previous run",
        "",
        (
            f"Compared against {report['compared_against']}."
            if report.get("compared_against")
            else "No previous run in `evaluation/results/history.jsonl` to compare against."
        ),
        "",
    ]
    lines += [f"- REGRESSION {r}" for r in report.get("regressions", [])] or [
        "No metric fell more than " f"{REGRESSION_TOLERANCE:.2f} below the previous run."
    ]
    lines += [
        "",
        "## LLM-judged results (RAGAS)",
        "",
        "| Metric | Value | Scored N | Coverage |",
        "|---|---|---|---|",
        _ragas_row("Faithfulness", ragas.get("faithfulness")),
        _ragas_row("Answer Relevancy", ragas.get("answer_relevancy")),
        _ragas_row("Context Precision", ragas.get("context_precision")),
        _ragas_row("Context Recall", ragas.get("context_recall")),
        "",
        "## Metric definitions",
        "",
    ]
    for name, doc in METRIC_DOCS.items():
        lines += [
            f"### {name}",
            f"- **Measures:** {doc['measures']}",
            f"- **Why it matters:** {doc['why']}",
            f"- **Acceptable:** {doc['acceptable']}",
            f"- **Limitations:** {doc['limits']}",
            "",
        ]
    lines += [
        "## Reproduce",
        "",
        "```bash",
        "# one-off: pin the corpus and ingest the pinned filings",
        "python -m src.evaluation.dataset ingest",
        "# strongest free judge (needs OPENROUTER_API_KEY, ~50 calls/day free):",
        "RAGAS_JUDGE_PROVIDER=openrouter python -m src.evaluation.benchmark",
        "# fully local / unlimited (needs Ollama running):",
        "RAGAS_JUDGE_PROVIDER=ollama RAGAS_OLLAMA_MODEL=gemma4:e4b-mlx \\",
        "  python -m src.evaluation.benchmark",
        "```",
        "",
        "Retrieval, latency, and success-rate metrics are deterministic and need no "
        "LLM. RAGAS metrics depend on the judge; read the scored-N column first — "
        "a metric scored on under "
        f"{MIN_COVERAGE:.0%} of rows is suppressed rather than averaged over the "
        "rows that happened to parse.",
        "",
    ]
    return "\n".join(lines)


def _write_reports(report: dict) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    stamp = report["timestamp"].replace(":", "").replace("-", "").split(".")[0]
    json_path = os.path.join(RESULTS_DIR, f"benchmark_{stamp}.json")
    md_path = os.path.join(RESULTS_DIR, f"benchmark_{stamp}.md")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    md = render_markdown(report)
    with open(md_path, "w") as f:
        f.write(md)
    # Stable "latest" pointers for docs/README links.
    with open(os.path.join(RESULTS_DIR, "benchmark_latest.json"), "w") as f:
        json.dump(report, f, indent=2)
    with open(os.path.join(RESULTS_DIR, "benchmark_latest.md"), "w") as f:
        f.write(md)
    logger.info(f"Wrote {json_path} and {md_path}")


if __name__ == "__main__":
    rep = run_benchmark()
    print("\n" + render_markdown(rep))
    # Non-zero on a drop against the previous run, so CI gates on the trend and
    # not only on an absolute threshold.
    raise SystemExit(1 if rep["regressions"] else 0)
