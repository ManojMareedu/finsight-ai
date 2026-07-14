# src/evaluation/ragas_eval.py
import datetime
import json
import logging
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

    Both backends are free: "ollama" runs a local model, "openrouter" reuses the
    configured free OpenRouter model. Neither is a paid dependency.
    """
    provider = settings.ragas_judge_provider.lower()

    if provider == "openrouter":
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        return ChatOpenAI(
            model=settings.primary_model,
            base_url=settings.openrouter_base_url,
            api_key=SecretStr(settings.openrouter_api_key),
            temperature=0,
        )

    # Default: local Ollama
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.ragas_ollama_model,
        temperature=0,
        num_predict=512,
    )


def collect_eval_data(golden: list) -> list:
    eval_data = []
    for i, item in enumerate(golden):
        logger.info(f"Processing item {i+1}/{len(golden)}: {item['question'][:60]}...")
        try:
            docs = retrieve_context(item["question"])
            contexts = [d.page_content for d in docs]

            if not contexts:
                logger.warning(f"No context retrieved for: {item['question']}")
                continue

            answer = chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Answer the question based only on the provided context. "
                            "Be concise and factual."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Context:\n{chr(10).join(contexts[:3])}\n\n"
                            f"Question: {item['question']}"
                        ),
                    },
                ]
            )

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

    return eval_data


def run_evaluation() -> dict:
    golden_path = os.path.join(os.path.dirname(__file__), "golden_dataset.json")
    with open(golden_path) as f:
        golden = json.load(f)

    logger.info(f"Loaded {len(golden)} golden items")

    eval_data = collect_eval_data(golden)

    if not eval_data:
        raise RuntimeError(
            "No evaluation data collected. " "Make sure ChromaDB has filings ingested."
        )

    logger.info(f"Running RAGAS evaluation on {len(eval_data)} samples...")

    from ragas.embeddings import LangchainEmbeddingsWrapper

    from src.rag.embeddings import get_embeddings

    settings = get_settings()
    ragas_llm = _build_judge_llm(settings)

    ragas_embeddings = LangchainEmbeddingsWrapper(get_embeddings())

    dataset = Dataset.from_list(eval_data)

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
    )

    def safe_score(key):
        val = scores[key]
        if isinstance(val, list):
            valid = [v for v in val if v is not None]
            return round(sum(valid) / len(valid), 4) if valid else 0.0
        return round(float(val), 4)

    result = {
        "faithfulness": safe_score("faithfulness"),
        "answer_relevancy": safe_score("answer_relevancy"),
        "context_precision": safe_score("context_precision"),
        "context_recall": safe_score("context_recall"),
        "num_samples": len(eval_data),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    # Quality gate: run passes only if the two primary metrics clear their
    # thresholds. Exposed so `make eval` / a manual CI step can fail on it.
    result["passed"] = bool(
        result["faithfulness"] >= settings.ragas_faithfulness_min
        and result["answer_relevancy"] >= settings.ragas_answer_relevancy_min
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
