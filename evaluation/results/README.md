# Evaluation results

Objective, reproducible evidence for FinSight's retrieval and generation quality.

| File | Produced by | What it is |
|---|---|---|
| `benchmark_latest.md` / `.json` | `make benchmark` | **Comprehensive benchmark** — retrieval precision@k, recall, latency (mean/p95), success rate (all questions) + RAGAS (capped). The `.md` documents every metric (what / why / acceptable / limitations). |
| `latest.json` | `make eval` | **RAGAS quality gate** — the 4 RAGAS metrics + a pass/fail against thresholds, used as a gate (`sys.exit`). |
| `benchmark_<timestamp>.*` | `make benchmark` | Per-run snapshots (git-ignored; regenerate anytime). |

## Regenerate

```bash
make benchmark                               # strongest free judge (OpenRouter, needs OPENROUTER_API_KEY)
RAGAS_JUDGE_PROVIDER=ollama make benchmark   # fully local, unlimited (needs Ollama running)
```

Deterministic metrics (retrieval precision/recall, latency, success rate) need no
LLM and are the high-confidence signal. RAGAS metrics depend on the judge; a
`null` RAGAS value means the judge could not score that metric (not a zero) — on
free judges `context_precision` is the most parse-fragile. See the repo README's
"Evaluation & Benchmarks" section for the before/after retrieval-tuning story.
