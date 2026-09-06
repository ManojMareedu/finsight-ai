# FinSight AI — Product Evaluation

Scores the DueDiligenceReport the API actually returns, not a stand-alone RAG answer.

- **Generated:** 2026-09-06T11:47:12.369010+00:00
- **Companies:** 4 (Apple, Microsoft, NVIDIA, Adobe)
- **Runs per company:** 2
- **Answer model:** `openrouter/free` at temperature 0
- **Judge model:** `not run`
- **Embeddings:** `sentence-transformers/all-MiniLM-L6-v2`
- **Corpus pinned at:** 2026-09-05T12:00:44.099512+00:00
- **Wall clock per report:** 44.8521s mean, 91.4s p95 over 14 reports

## Deterministic metrics (no LLM calls, these are the gate)

| Metric | Value | Scored N | Gate |
|---|---|---|---|
| numeric_accuracy | 1.0000 | 31 | >= 0.99 |
| citation_attribution | 0.8182 | 11 | >= 0.90 |
| signal_stability | n/a | 0 | >= 0.80 |
| abstention_correctness | 1.0000 | 3 | >= 0.95 |
| injection_resistance | 0 payloads moved the signal | 4/8 landed | == 0 |

31 of 31 key metrics were compared as raw numbers against the pinned XBRL facts rather than as formatted strings.

## Result

FAILED

- citation_attribution: 0.8182 < 0.90
- signal_stability: not scored (no data)
