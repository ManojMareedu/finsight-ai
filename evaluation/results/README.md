# Evaluation results

Objective, reproducible evidence for FinSight's retrieval and generation quality.
The corpus is pinned by accession number (`src/evaluation/snapshot.json`) and the
golden set is generated from XBRL company facts, so two runs are comparable.

| File | Produced by | What it is |
|---|---|---|
| `product_eval_latest.md` / `.json` | `python -m src.evaluation.report_eval` | **Product evaluation** — scores the `DueDiligenceReport` the API actually returns: numeric accuracy vs XBRL, citation attribution, signal stability, abstention, injection resistance. Deterministic (no judge) and gated; the LLM-judged unsupported-claim rate is reported separately and never gates. |
| `benchmark_latest.md` / `.json` | `make benchmark` | **Retrieval benchmark** — company-filter integrity, hit rate, keyword recall, latency (mean/p95), success rate over all questions, plus RAGAS with a per-metric scored-N. |
| `latest.json` | `make eval` | **RAGAS quality gate** — the 4 RAGAS metrics with coverage + a pass/fail against thresholds (`sys.exit`). |
| `benchmark_<timestamp>.*`, `product_eval_<timestamp>.*` | either | Per-run snapshots (git-ignored; regenerate anytime). |

## Regenerate

```bash
# one-off: pin the corpus (only when rolling it forward) and ingest it
python -m src.evaluation.dataset pin
CHROMA_PERSIST_DIR=./data/chroma_eval python -m src.evaluation.dataset ingest

# product evaluation — the metrics that gate CI
CHROMA_PERSIST_DIR=./data/chroma_eval python -m src.evaluation.report_eval --companies 5 --runs 3

# retrieval + RAGAS benchmark
make benchmark                               # OpenRouter judge (needs OPENROUTER_API_KEY)
RAGAS_JUDGE_PROVIDER=ollama make benchmark   # fully local, unlimited (needs Ollama running)

# offline, free, no key: dataset traceability check (the CI hard gate)
python -m src.evaluation.dataset check
```

Deterministic metrics need no LLM and are the high-confidence signal. Judged
metrics carry the number of rows the judge actually scored: a metric scored on
under 80% of rows is printed as `suppressed` with its coverage instead of being
averaged over the rows that happened to parse. `.github/workflows/eval.yml`
runs the deterministic gate nightly and degrades to dataset + unit checks when
no model key is present.
