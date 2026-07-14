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

from src.rag.retriever import retrieve_context
from src.utils.config import Settings, get_settings
from src.utils.llm_client import chat

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    system = "Answer the question based only on the provided context. Be concise and factual."
    user = f"Context:\n{chr(10).join(contexts[:3])}\n\nQuestion: {question}"

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
            docs = retrieve_context(item["question"])
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

    golden_path = os.path.join(os.path.dirname(__file__), "golden_dataset.json")
    with open(golden_path) as f:
        golden = json.load(f)

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

    def _is_nan(v) -> bool:
        return isinstance(v, float) and math.isnan(v)

    def safe_score(key) -> float:
        # RAGAS reports a per-row NaN when the judge output could not be parsed for
        # that row. Drop None AND NaN so a metric is the mean of the rows that
        # actually scored; if none scored, the metric itself is NaN (guarded below).
        val = scores[key]
        if isinstance(val, list):
            valid = [v for v in val if v is not None and not _is_nan(v)]
            return round(sum(valid) / len(valid), 4) if valid else float("nan")
        return float("nan") if _is_nan(val) else round(float(val), 4)

    metric_keys = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    metrics = {k: safe_score(k) for k in metric_keys}

    # Fail loudly on an all/partially-NaN pipeline instead of writing NaN as if it
    # were a valid benchmark. NaN here means judge/parse failure, not a real score.
    nan_metrics = [k for k, v in metrics.items() if _is_nan(v)]
    if nan_metrics:
        judge_name = (
            settings.ragas_judge_model
            if settings.ragas_judge_provider == "openrouter"
            else settings.ragas_ollama_model
        )
        raise RuntimeError(
            f"RAGAS returned NaN for: {', '.join(nan_metrics)}. This is a pipeline "
            f"failure (judge={settings.ragas_judge_provider}/{judge_name}), not a "
            "model-quality result. Common causes: judge unreachable, judge output "
            "not valid structured JSON, or a langchain-core version mismatch. "
            "No results were written."
        )

    result = {
        **metrics,
        "num_samples": len(eval_data),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # Quality gate: run passes only if the two primary metrics clear their
    # thresholds. Exposed so `make eval` / a manual CI step can fail on it.
    result["passed"] = bool(
        metrics["faithfulness"] >= settings.ragas_faithfulness_min
        and metrics["answer_relevancy"] >= settings.ragas_answer_relevancy_min
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
    }

    print("\n" + "=" * 40)
    print("  RAGAS Evaluation Results")
    print("=" * 40)
    for k, v in results.items():
        if isinstance(v, float):
            status = "PASS" if v >= mins.get(k, 0.0) else "FAIL"
            print(f"  {k:<22} {v:.4f}   [{status}]")
        else:
            print(f"  {k:<22} {v}")
    print("=" * 40)
    print("\nResults saved to evaluation/results/latest.json")

    # Non-zero exit turns this into a real quality gate for `make eval`.
    sys.exit(0 if results["passed"] else 1)
