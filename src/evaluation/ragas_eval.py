# src/evaluation/ragas_eval.py
import json
import datetime
import logging
import os

from ragas import evaluate
from ragas.metrics import (
    faithfulness,
    answer_relevancy,
    context_precision,
    context_recall,
)
from datasets import Dataset

from src.rag.retriever import retrieve_context
from src.utils.llm_client import chat

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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

            answer = chat([
                {
                    "role": "system",
                    "content": "Answer the question based only on the provided context. Be concise and factual.",
                },
                {
                    "role": "user",
                    "content": f"Context:\n{chr(10).join(contexts[:3])}\n\nQuestion: {item['question']}",
                },
            ])

            eval_data.append({
                "question":     item["question"],
                "answer":       answer,
                "contexts":     contexts,
                "ground_truth": item["ground_truth"],
            })

        except Exception as e:
            logger.warning(f"Eval item failed: {e}")

    return eval_data


def run_evaluation() -> dict:
    golden_path = os.path.join(
        os.path.dirname(__file__), "golden_dataset.json"
    )
    with open(golden_path) as f:
        golden = json.load(f)

    logger.info(f"Loaded {len(golden)} golden items")

    eval_data = collect_eval_data(golden)

    if not eval_data:
        raise RuntimeError(
            "No evaluation data collected. "
            "Make sure ChromaDB has filings ingested."
        )

    logger.info(f"Running RAGAS evaluation on {len(eval_data)} samples...")

    from langchain_ollama import ChatOllama
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from src.rag.embeddings import get_embeddings

    ragas_llm = ChatOllama(
        model="llama3.2",
        temperature=0,
        num_predict=512,
    )

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
        "faithfulness":      safe_score("faithfulness"),
        "answer_relevancy":  safe_score("answer_relevancy"),
        "context_precision": safe_score("context_precision"),
        "context_recall":    safe_score("context_recall"),
        "num_samples":       len(eval_data),
        "timestamp":         datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    os.makedirs("evaluation/results", exist_ok=True)
    with open("evaluation/results/latest.json", "w") as f:
        json.dump(result, f, indent=2)

    return result


if __name__ == "__main__":
    results = run_evaluation()

    print("\n" + "=" * 40)
    print("  RAGAS Evaluation Results")
    print("=" * 40)
    for k, v in results.items():
        if isinstance(v, float):
            status = "PASS" if (
                (k == "faithfulness"     and v >= 0.70) or
                (k == "answer_relevancy" and v >= 0.65) or
                k not in ("faithfulness", "answer_relevancy")
            ) else "FAIL"
            print(f"  {k:<22} {v:.4f}   [{status}]")
        else:
            print(f"  {k:<22} {v}")
    print("=" * 40)
    print(f"\nResults saved to evaluation/results/latest.json")