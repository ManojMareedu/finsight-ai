# src/evaluation/ragas_eval.py
import datetime
import json
import logging
import math
import os

from datasets import Dataset
from ragas import evaluate
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

from src.evaluation.dataset import EMBEDDING_MODEL, load_golden, load_snapshot
from src.rag.retriever import retrieve_context
from src.utils.config import Settings, get_settings
from src.utils.llm_client import chat

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# A metric averaged over the handful of rows the judge managed to parse is a
# selection-biased number, and the bias runs in the flattering direction. Below
# this coverage the metric is reported as null with its coverage, not averaged.
MIN_COVERAGE = 0.8

# Thresholds for the two context metrics. Kept here rather than in Settings
# because config.py is outside this change; move them there when convenient.
CONTEXT_PRECISION_MIN = get_settings().ragas_context_precision_min
CONTEXT_RECALL_MIN = get_settings().ragas_context_recall_min


def score_with_coverage(values: list) -> dict:
    """Mean of the rows the judge actually scored, plus the denominator.

    Returns ``value=None`` when fewer than MIN_COVERAGE of the rows scored, so
    a metric the judge mostly failed on cannot be printed as if it were a
    result. ``scored``/``total`` are always reported.
    """
    total = len(values)
    scored = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    coverage = round(len(scored) / total, 4) if total else 0.0
    value = round(sum(scored) / len(scored), 4) if scored and coverage >= MIN_COVERAGE else None
    return {"value": value, "scored": len(scored), "total": total, "coverage": coverage}


def _build_judge_llm(settings: Settings):
    """
    Build the RAGAS judge LLM from settings.

    Both backends are free and both go through ChatOpenAI: RAGAS 0.2.6's output
    parser works reliably with the ChatOpenAI code path, whereas the native
    ChatOllama path returns output RAGAS cannot parse (every metric NaN). For the
    local backend we therefore talk to Ollama's OpenAI-compatible /v1 endpoint.
    """
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    provider = settings.ragas_judge_provider.lower()

    if provider == "ollama":
        # Local, unlimited, offline. api_key is required by the client but unused.
        return ChatOpenAI(
            model=settings.ragas_ollama_model,
            base_url=settings.ollama_base_url,
            api_key=SecretStr("ollama"),
            temperature=0,
            timeout=180,
        )

    # OpenRouter. Uses ragas_judge_model (NOT primary_model): the judge must
    # reliably emit RAGAS's structured JSON, which weak/router models do not.
    return ChatOpenAI(
        model=settings.ragas_judge_model,
        base_url=settings.openrouter_base_url,
        api_key=SecretStr(settings.openrouter_api_key),
        temperature=0,
        timeout=120,
        max_retries=2,
    )


def _eval_answer(question: str, contexts: list, settings: Settings) -> str:
    """
    Generate the RAG answer for one eval item using the configured backend.

    Honors ragas_judge_provider so the whole eval can run on one backend: with
    "ollama" it stays fully local/offline (no OpenRouter quota), otherwise it uses
    the normal OpenRouter chat() path.
    """
    # All contexts, not a prefix: RAGAS judges every retrieved context, so
    # generating from a subset scores the answer against text it never saw.
    system = "Answer the question based only on the provided context. Be concise and factual."
    user = f"Context:\n{chr(10).join(contexts)}\n\nQuestion: {question}"

    if settings.ragas_judge_provider == "ollama":
        # Reuse the judge LLM (local Ollama via /v1) so the whole eval stays offline.
        llm = _build_judge_llm(settings)
        return str(llm.invoke([("system", system), ("user", user)]).content)

    return chat([{"role": "system", "content": system}, {"role": "user", "content": user}])


def collect_eval_data(golden: list) -> list:
    settings = get_settings()
    eval_data = []
    for i, item in enumerate(golden):
        logger.info(f"Processing item {i+1}/{len(golden)}: {item['question'][:60]}...")
        try:
            docs = retrieve_context(item["question"], company=item["company"])
            contexts = [d.page_content for d in docs]

            if not contexts:
                logger.warning(f"No context retrieved for: {item['question']}")
                continue

            answer = _eval_answer(item["question"], contexts, settings)

            eval_data.append(
                {
                    "question": item["question"],
                    "answer": answer,
                    "contexts": contexts,
                    "ground_truth": item["ground_truth"],
                }
            )

        except Exception as e:
            logger.warning(f"Eval item failed: {e}")

    logger.info(f"Collected {len(eval_data)}/{len(golden)} eval samples")
    return eval_data


def run_evaluation() -> dict:
    settings = get_settings()

    golden = load_golden()

    # Optional cap. RAGAS makes many judge calls per sample (context_precision
    # alone is one per retrieved context), so on rate-limited free tiers a full
    # run can exhaust the daily quota. ragas_max_samples=0 means "all".
    if settings.ragas_max_samples and settings.ragas_max_samples > 0:
        golden = golden[: settings.ragas_max_samples]

    logger.info(f"Loaded {len(golden)} golden items")

    eval_data = collect_eval_data(golden)

    if not eval_data:
        raise RuntimeError(
            "No evaluation data collected. " "Make sure ChromaDB has filings ingested."
        )

    logger.info(f"Running RAGAS evaluation on {len(eval_data)} samples...")

    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.run_config import RunConfig

    from src.rag.embeddings import get_embeddings

    ragas_llm = _build_judge_llm(settings)

    ragas_embeddings = LangchainEmbeddingsWrapper(get_embeddings())

    dataset = Dataset.from_list(eval_data)

    # RunConfig: a generous per-call timeout so slower local judges don't get
    # cut off (RAGAS defaults to 180s -> TimeoutError -> NaN), and modest
    # concurrency so a single local model isn't thrashed by 16 parallel calls.
    run_config = RunConfig(timeout=settings.ragas_timeout, max_workers=settings.ragas_max_workers)

    scores = evaluate(
        dataset,
        metrics=[
            faithfulness,
            answer_relevancy,
            context_precision,
            context_recall,
        ],
        llm=ragas_llm,
        embeddings=ragas_embeddings,
        run_config=run_config,
    )

    metrics = {
        k: score_with_coverage(list(scores[k]) if isinstance(scores[k], list) else [scores[k]])
        for k in ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
    }

    # A metric nothing scored is a pipeline failure (judge unreachable, output
    # not parseable, langchain-core mismatch), not a model-quality result.
    unscored = [k for k, v in metrics.items() if v["scored"] == 0]
    if unscored:
        judge_name = (
            settings.ragas_judge_model
            if settings.ragas_judge_provider == "openrouter"
            else settings.ragas_ollama_model
        )
        raise RuntimeError(
            f"RAGAS scored 0 rows for: {', '.join(unscored)}. This is a pipeline "
            f"failure (judge={settings.ragas_judge_provider}/{judge_name}), not a "
            "model-quality result. No results were written."
        )

    result = {
        **metrics,
        "num_samples": len(eval_data),
        "min_coverage": MIN_COVERAGE,
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
            "snapshot_pinned_at": load_snapshot()["pinned_at"],
        },
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # Quality gate. All four metrics count — gating on two of them let
    # context_recall sit at 0.30 forever without ever failing a run. A metric
    # suppressed for low coverage fails the gate rather than being ignored.
    mins = {
        "faithfulness": settings.ragas_faithfulness_min,
        "answer_relevancy": settings.ragas_answer_relevancy_min,
        "context_precision": CONTEXT_PRECISION_MIN,
        "context_recall": CONTEXT_RECALL_MIN,
    }
    result["passed"] = all(
        metrics[k]["value"] is not None and metrics[k]["value"] >= floor
        for k, floor in mins.items()
    )

    os.makedirs("evaluation/results", exist_ok=True)
    with open("evaluation/results/latest.json", "w") as f:
        json.dump(result, f, indent=2)

    return result


if __name__ == "__main__":
    import sys

    settings = get_settings()
    results = run_evaluation()

    mins = {
        "faithfulness": settings.ragas_faithfulness_min,
        "answer_relevancy": settings.ragas_answer_relevancy_min,
        "context_precision": CONTEXT_PRECISION_MIN,
        "context_recall": CONTEXT_RECALL_MIN,
    }

    print("\n" + "=" * 62)
    print("  RAGAS Evaluation Results")
    print("=" * 62)
    for k, floor in mins.items():
        m = results[k]
        if m["value"] is None:
            print(
                f"  {k:<22} suppressed   scored {m['scored']}/{m['total']} "
                f"(coverage {m['coverage']:.2f} < {MIN_COVERAGE})"
            )
        else:
            status = "PASS" if m["value"] >= floor else "FAIL"
            print(f"  {k:<22} {m['value']:.4f}   scored {m['scored']}/{m['total']}   [{status}]")
    print("=" * 62)
    print("\nResults saved to evaluation/results/latest.json")

    # Non-zero exit turns this into a real quality gate for `make eval`.
    sys.exit(0 if results["passed"] else 1)
