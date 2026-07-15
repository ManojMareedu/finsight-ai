# src/evaluation/benchmark.py
"""Comprehensive, reproducible retrieval + RAG benchmark.

Produces objective evidence for FinSight's retrieval and generation quality and
writes a timestamped JSON + Markdown report to ``evaluation/results/``.

Two metric families:

* **Deterministic** (no LLM, unlimited, zero-noise): retrieval precision@k,
  retrieval recall, latency (mean / p95), success rate. These drive
  optimization because they are cheap and reproducible.
* **RAGAS** (LLM judge): faithfulness, answer relevancy, context precision,
  context recall. Reported when a judge is available; individual metrics that the
  judge fails to score are reported as ``null`` rather than crashing the run.

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

from src.evaluation.ragas_eval import _build_judge_llm, _eval_answer
from src.rag.retriever import retrieve_context
from src.utils.config import Settings, get_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

RESULTS_DIR = "evaluation/results"

# Golden ``context_source`` prefix -> the ``company`` value stored on ingested
# chunks. Used to score whether a retrieved chunk belongs to the right filing.
SOURCE_COMPANY = {"apple": "Apple", "msft": "Microsoft", "tesla": "Tesla"}

# Minimal stopword set for the keyword-overlap recall proxy.
_STOPWORDS = set(
    "the a an and or of for to in on at is are as its their with by from that this "
    "key cites approximately including reported showed significant".split()
)

# Documentation for every reported metric: what it measures, why it matters, an
# acceptable range, and its limitations. Rendered into the Markdown report so a
# reviewer can interpret the numbers without reading the code.
METRIC_DOCS: dict[str, dict[str, str]] = {
    "retrieval_precision_at_k": {
        "measures": "Fraction of the top-k retrieved chunks that belong to the "
        "company the question is about (chunk `company` metadata == target).",
        "why": "Cross-company contamination directly degrades answer grounding; "
        "this is the cleanest objective signal of retrieval precision.",
        "acceptable": ">= 0.90 is good for this 3-company corpus.",
        "limits": "Company-level, not passage-level relevance (a right-company but "
        "off-topic chunk still counts). ChromaDB uses approximate NN search (HNSW), "
        "so this varies ~+/-0.02 run-to-run (observed 0.975-1.000).",
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
        "limits": "Judge-dependent; weak judges are noisy.",
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


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z]{4,}", text.lower()) if w not in _STOPWORDS}


def _target_company(context_source: str) -> str:
    return SOURCE_COMPANY.get(context_source.split("_")[0], "")


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
            docs = retrieve_context(item["question"])
            rec["retrieval_latency_s"] = round(time.perf_counter() - t0, 4)

            contexts = [d.page_content for d in docs]
            companies = [d.metadata.get("company", "?") for d in docs]
            target = _target_company(item["context_source"])
            rec["k"] = len(docs)
            rec["retrieval_precision_at_k"] = (
                round(sum(c == target for c in companies) / len(companies), 4) if companies else 0.0
            )
            gt_words = _content_words(item["ground_truth"])
            joined = " ".join(contexts).lower()
            rec["retrieval_recall"] = (
                round(sum(w in joined for w in gt_words) / len(gt_words), 4) if gt_words else 0.0
            )

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


def compute_ragas(ragas_rows: list, settings: Settings) -> dict[str, Optional[float]]:
    """Run RAGAS NaN-tolerant: metrics the judge fails to score become ``None``
    instead of raising (unlike the production ``run_evaluation`` guard)."""
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
    out: dict[str, Optional[float]] = {}
    for key in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
        vals = [
            v for v in df[key] if v is not None and not (isinstance(v, float) and math.isnan(v))
        ]
        out[key] = round(sum(vals) / len(vals), 4) if vals else None
    return out


def run_benchmark(include_ragas: bool = True) -> dict:
    settings = get_settings()
    with open(os.path.join(os.path.dirname(__file__), "golden_dataset.json")) as f:
        golden = json.load(f)

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
        },
        "retrieval": {
            "precision_at_k": _mean(
                [r["retrieval_precision_at_k"] for r in records if "retrieval_precision_at_k" in r]
            ),
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

    _write_reports(report)
    return report


def _fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.4f}"


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
        f"- **Answer model:** `{cfg['answer_model']}`",
        f"- **RAGAS samples:** {report.get('ragas_num_samples', 'n/a')} "
        f"(deterministic metrics cover all {report['num_questions']})",
        "",
        "## Results",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Retrieval Precision@{r['k']} | {_fmt(r['precision_at_k'])} |",
        f"| Retrieval Recall | {_fmt(r['recall'])} |",
        f"| Success Rate | {_fmt(rel['success_rate'])} ({rel['successful']}/{rel['total']}) |",
        f"| Latency total (mean) | {_fmt(lat['total_mean'])}s |",
        f"| Latency total (p95) | {_fmt(lat['total_p95'])}s |",
        f"| Latency retrieval (mean) | {_fmt(lat['retrieval_mean'])}s |",
        f"| RAGAS Faithfulness | {_fmt(ragas.get('faithfulness'))} |",
        f"| RAGAS Answer Relevancy | {_fmt(ragas.get('answer_relevancy'))} |",
        f"| RAGAS Context Precision | {_fmt(ragas.get('context_precision'))} |",
        f"| RAGAS Context Recall | {_fmt(ragas.get('context_recall'))} |",
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
        "# strongest free judge (needs OPENROUTER_API_KEY, ~50 calls/day free):",
        "RAGAS_JUDGE_PROVIDER=openrouter python -m src.evaluation.benchmark",
        "# fully local / unlimited (needs Ollama running):",
        "RAGAS_JUDGE_PROVIDER=ollama RAGAS_OLLAMA_MODEL=gemma4:e4b-mlx \\",
        "  python -m src.evaluation.benchmark",
        "```",
        "",
        "Retrieval, latency, and success-rate metrics are deterministic and need no "
        "LLM. RAGAS metrics depend on the judge; `null` means the judge could not "
        "score that metric (not a zero).",
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
