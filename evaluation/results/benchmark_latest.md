# FinSight AI — Benchmark Report

- **Generated:** 2026-07-15T10:03:22.405473+00:00
- **Questions:** 10
- **Judge:** `openrouter` / `openai/gpt-oss-20b:free`
- **Answer model:** `openrouter/free`
- **RAGAS samples:** 3 (deterministic metrics cover all 10)

## Results

| Metric | Value |
|---|---|
| Retrieval Precision@8 | 1.0000 |
| Retrieval Recall | 0.7942 |
| Success Rate | 1.0000 (10/10) |
| Latency total (mean) | 17.5738s |
| Latency total (p95) | 50.5876s |
| Latency retrieval (mean) | 0.7239s |
| RAGAS Faithfulness | 0.8333 |
| RAGAS Answer Relevancy | 0.5372 |
| RAGAS Context Precision | n/a |
| RAGAS Context Recall | 0.3333 |

## Metric definitions

### retrieval_precision_at_k
- **Measures:** Fraction of the top-k retrieved chunks that belong to the company the question is about (chunk `company` metadata == target).
- **Why it matters:** Cross-company contamination directly degrades answer grounding; this is the cleanest objective signal of retrieval precision.
- **Acceptable:** >= 0.90 is good for this 3-company corpus.
- **Limitations:** Company-level, not passage-level relevance; a right-company but off-topic chunk still counts as a hit.

### retrieval_recall
- **Measures:** Mean fraction of ground-truth content words present in the concatenated retrieved chunks (keyword-overlap proxy).
- **Why it matters:** Approximates whether the answer-supporting text was retrieved, with no LLM cost.
- **Acceptable:** >= 0.70 for the qualitative questions in this set.
- **Limitations:** Lexical, not semantic; and exact financial figures live in XBRL (fetched separately), not the 10-K text, so numeric questions cannot reach 1.0 here.

### success_rate
- **Measures:** Fraction of questions that produced a non-empty answer with no exception in retrieval or generation.
- **Why it matters:** Reliability of the end-to-end path under the current judge/config.
- **Acceptable:** 1.0 expected; anything lower is a reliability regression.
- **Limitations:** Does not judge answer quality — only that the pipeline ran.

### latency_total_p95_s
- **Measures:** 95th-percentile end-to-end latency (retrieval + generation) per question, in seconds.
- **Why it matters:** Tail latency is what users feel; p95 is more honest than the mean.
- **Acceptable:** Dominated by the LLM; local Ollama is slower than hosted.
- **Limitations:** Small N makes p95 coarse; embedding model load excluded (warm).

### faithfulness
- **Measures:** RAGAS: are the answer's claims supported by the retrieved context (no hallucination)?
- **Why it matters:** The core trust metric for a RAG system.
- **Acceptable:** >= 0.70.
- **Limitations:** Judge-dependent; weak judges are noisy.

### answer_relevancy
- **Measures:** RAGAS: how well the answer addresses the question.
- **Why it matters:** Catches evasive or off-topic answers.
- **Acceptable:** >= 0.65.
- **Limitations:** Depends on the generated answer, not retrieval; noisiest metric on weak local judges.

### context_precision
- **Measures:** RAGAS: are the retrieved contexts relevant to the ground truth, and ranked well?
- **Why it matters:** LLM-judged retrieval precision.
- **Acceptable:** >= 0.60.
- **Limitations:** Most call-heavy RAGAS metric and the most parse-fragile; treat with caution on free judges.

### context_recall
- **Measures:** RAGAS: is the ground truth attributable to the retrieved contexts?
- **Why it matters:** LLM-judged retrieval recall.
- **Acceptable:** >= 0.60.
- **Limitations:** Judge-dependent; small N is noisy.

## Reproduce

```bash
# strongest free judge (needs OPENROUTER_API_KEY, ~50 calls/day free):
RAGAS_JUDGE_PROVIDER=openrouter python -m src.evaluation.benchmark
# fully local / unlimited (needs Ollama running):
RAGAS_JUDGE_PROVIDER=ollama RAGAS_OLLAMA_MODEL=gemma4:e4b-mlx \
  python -m src.evaluation.benchmark
```

Retrieval, latency, and success-rate metrics are deterministic and need no LLM. RAGAS metrics depend on the judge; `null` means the judge could not score that metric (not a zero).
