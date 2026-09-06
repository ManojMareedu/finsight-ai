"""Network-free unit tests for the deterministic benchmark logic."""

import math

import src.evaluation.benchmark as bm
import src.evaluation.ragas_eval as rg


class _Doc:
    def __init__(self, content, company):
        self.page_content = content
        self.metadata = {"company": company}


def _report(ragas):
    return {
        "timestamp": "2026-07-15T00:00:00+00:00",
        "num_questions": 3,
        "config": {
            "judge_provider": "openrouter",
            "judge_model": "m",
            "answer_model": "a",
            "temperature": 0,
            "embedding_model": "e",
            "snapshot_pinned_at": "2026-09-05T00:00:00+00:00",
            "min_coverage": rg.MIN_COVERAGE,
        },
        "retrieval": {
            "company_filter_integrity": 1.0,
            "hit_rate": 0.6,
            "recall": 0.7942,
            "k": 8,
        },
        "reliability": {"success_rate": 1.0, "successful": 3, "total": 3},
        "latency_s": {
            "retrieval_mean": 0.1,
            "retrieval_p95": 0.2,
            "total_mean": 5.0,
            "total_p95": 9.0,
        },
        "ragas": ragas,
    }


def test_history_accumulates_across_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(bm, "RESULTS_DIR", str(tmp_path))
    monkeypatch.setattr(bm, "HISTORY_PATH", str(tmp_path / "history.jsonl"))

    bm.append_history("benchmark", "2026-09-01T00:00:00+00:00", {"retrieval_recall": 0.79}, {})
    bm.append_history("benchmark", "2026-09-02T00:00:00+00:00", {"retrieval_recall": 0.60}, {})
    bm.append_history("product_eval", "2026-09-02T00:00:01+00:00", {"numeric_accuracy": 1.0}, {})

    history = bm.load_history("benchmark")
    assert len(history) == 2  # the second run appended, it did not overwrite
    assert [row["metrics"]["retrieval_recall"] for row in history] == [0.79, 0.60]
    assert len(bm.load_history("product_eval")) == 1  # kinds do not bleed into each other


def test_history_survives_a_truncated_line(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    monkeypatch.setattr(bm, "HISTORY_PATH", str(path))
    bm.append_history("benchmark", "t1", {"retrieval_recall": 0.79}, {})
    with open(path, "a") as f:
        f.write('{"kind": "benchmark", "sche')
    bm.append_history("benchmark", "t2", {"retrieval_recall": 0.80}, {})
    assert len(bm.load_history("benchmark")) == 2


def test_regressions_compare_against_the_previous_run():
    previous = {"timestamp": "t1", "metrics": {"retrieval_recall": 0.79, "hit_rate": 0.60}}
    assert bm.regressions({"retrieval_recall": 0.78, "hit_rate": 0.60}, previous) == []
    found = bm.regressions({"retrieval_recall": 0.60, "hit_rate": 0.60}, previous)
    assert len(found) == 1 and "retrieval_recall" in found[0]
    assert bm.regressions({"retrieval_recall": 0.10}, None) == []


def test_content_words_drops_stopwords_and_short():
    words = bm._content_words("The Apple revenue including cash in 2023")
    assert "apple" in words and "revenue" in words and "cash" in words
    assert "the" not in words  # stopword
    assert "in" not in words  # too short (len < 4)
    assert "including" not in words  # stopword
    assert "2023" not in words  # digits excluded by [a-zA-Z]{4,}


def test_percentile_nearest_rank():
    assert bm._percentile([], 95) == 0.0
    assert bm._percentile([1.0], 95) == 1.0
    # 10 values 1..10, p95 -> nearest-rank index ceil(0.95*10)-1 = 9 -> value 10
    assert bm._percentile([float(i) for i in range(1, 11)], 95) == 10.0
    assert bm._percentile([float(i) for i in range(1, 11)], 50) == 5.0


def test_mean():
    assert bm._mean([]) == 0.0
    assert bm._mean([1.0, 2.0, 3.0]) == 2.0


def test_retrieval_passes_the_production_company_filter(monkeypatch):
    seen = {}

    def _capture(query, company=None):
        seen["company"] = company
        return [_Doc("Apple total revenue was 383 billion dollars", "Apple")]

    monkeypatch.setattr(bm, "retrieve_context", _capture)
    monkeypatch.setattr(bm, "_eval_answer", lambda q, c, s: "an answer")
    golden = [{"question": "q", "ground_truth": "Apple revenue", "company": "Apple"}]

    bm.collect_with_metrics(golden, settings=object())
    assert seen["company"] == "Apple"


def test_collect_with_metrics_computes_filter_integrity_and_hits(monkeypatch):
    # 2 of 3 retrieved chunks are the right company -> integrity 2/3.
    # ground truth words: {apple, revenue, billion}; one chunk holds all three.
    docs = [
        _Doc("Apple total revenue was 383 billion dollars", "Apple"),
        _Doc("Apple services segment grew", "Apple"),
        _Doc("Tesla vehicle deliveries", "Tesla"),
    ]
    monkeypatch.setattr(bm, "retrieve_context", lambda q, company=None: docs)
    monkeypatch.setattr(bm, "_eval_answer", lambda q, c, s: "an answer")

    golden = [{"question": "q", "ground_truth": "Apple revenue billion", "company": "Apple"}]
    rows, recs = bm.collect_with_metrics(golden, settings=object())
    rec = recs[0]
    assert rec["company_filter_integrity"] == round(2 / 3, 4)
    assert rec["retrieval_recall"] == 1.0
    assert rec["retrieval_hit"] == 1.0
    assert rec["success"] is True
    assert rec["k"] == 3
    assert "retrieval_latency_s" in rec and "total_latency_s" in rec
    assert len(rows) == 1  # a successful row is passed to RAGAS


def test_hit_rate_misses_when_no_single_chunk_holds_the_answer(monkeypatch):
    # every ground-truth word is retrieved, but spread over four chunks
    docs = [_Doc(w, "Apple") for w in ("apple", "total", "revenue", "billion")]
    monkeypatch.setattr(bm, "retrieve_context", lambda q, company=None: docs)
    monkeypatch.setattr(bm, "_eval_answer", lambda q, c, s: "an answer")
    golden = [{"question": "q", "ground_truth": "apple total revenue billion", "company": "Apple"}]
    _, recs = bm.collect_with_metrics(golden, settings=object())
    assert recs[0]["retrieval_recall"] == 1.0
    assert recs[0]["retrieval_hit"] == 0.0


def test_collect_with_metrics_records_failure(monkeypatch):
    def _boom(q, company=None):
        raise RuntimeError("retriever down")

    monkeypatch.setattr(bm, "retrieve_context", _boom)
    golden = [{"question": "q", "ground_truth": "g", "company": "Apple"}]
    rows, recs = bm.collect_with_metrics(golden, settings=object())
    assert recs[0]["success"] is False
    assert "error" in recs[0]
    assert rows == []  # nothing sent to RAGAS


def test_empty_answer_is_not_success(monkeypatch):
    monkeypatch.setattr(
        bm, "retrieve_context", lambda q, company=None: [_Doc("Apple text", "Apple")]
    )
    monkeypatch.setattr(bm, "_eval_answer", lambda q, c, s: "   ")  # blank
    golden = [{"question": "q", "ground_truth": "apple", "company": "Apple"}]
    rows, recs = bm.collect_with_metrics(golden, settings=object())
    assert recs[0]["success"] is False
    assert rows == []


def test_score_with_coverage_reports_the_denominator():
    scored = rg.score_with_coverage([1.0, 0.5, None, 1.0, 0.5])
    assert scored["scored"] == 4 and scored["total"] == 5
    assert scored["coverage"] == 0.8
    assert scored["value"] == 0.75


def test_metric_scored_on_too_few_rows_is_suppressed():
    values = [1.0] * 7 + [float("nan")] * 3
    scored = rg.score_with_coverage(values)
    assert scored["value"] is None  # not 1.0 averaged over the survivors
    assert scored["scored"] == 7 and scored["total"] == 10
    assert scored["coverage"] == 0.7


def test_score_with_coverage_handles_an_all_nan_metric():
    scored = rg.score_with_coverage([float("nan")] * 4)
    assert scored["value"] is None
    assert scored["scored"] == 0 and scored["coverage"] == 0.0


def test_render_markdown_has_all_sections():
    md = bm.render_markdown(
        _report(
            {
                "faithfulness": {"value": 1.0, "scored": 10, "total": 10, "coverage": 1.0},
                "answer_relevancy": {"value": 0.2, "scored": 9, "total": 10, "coverage": 0.9},
                "context_precision": {"value": None, "scored": 0, "total": 10, "coverage": 0.0},
                "context_recall": {"value": 0.33, "scored": 8, "total": 10, "coverage": 0.8},
            }
        )
    )
    assert "Company filter integrity (k=8)" in md
    assert "Retrieval Hit Rate" in md
    assert "Success Rate" in md
    assert "Metric definitions" in md
    assert "Reproduce" in md
    assert "Scored N" in md
    for name in bm.METRIC_DOCS:
        assert name in md


def test_render_markdown_shows_coverage_instead_of_a_suppressed_value():
    md = bm.render_markdown(
        _report(
            {
                "faithfulness": {"value": None, "scored": 3, "total": 10, "coverage": 0.3},
                "answer_relevancy": None,
                "context_precision": None,
                "context_recall": None,
            }
        )
    )
    assert "suppressed" in md
    assert "3/10" in md


def test_render_markdown_handles_missing_ragas():
    md = bm.render_markdown(_report(None))
    assert "n/a" in md  # RAGAS rows render as n/a, not a crash


def test_compute_ragas_suppresses_a_mostly_unscored_metric(monkeypatch):
    class _Scores:
        def to_pandas(self):
            return {
                "faithfulness": [1.0] * 10,
                "answer_relevancy": [0.5] * 10,
                "context_precision": [math.nan] * 10,
                "context_recall": [1.0] * 5 + [math.nan] * 5,
            }

    monkeypatch.setattr("ragas.evaluate", lambda *a, **k: _Scores())
    monkeypatch.setattr(bm, "_build_judge_llm", lambda s: None)
    monkeypatch.setattr("src.rag.embeddings.get_embeddings", lambda: None)
    monkeypatch.setattr("ragas.embeddings.LangchainEmbeddingsWrapper", lambda e: None)
    monkeypatch.setattr("datasets.Dataset.from_list", staticmethod(lambda rows: rows))

    settings = type("S", (), {"ragas_timeout": 10, "ragas_max_workers": 1})()
    out = bm.compute_ragas([{}], settings)
    assert out["faithfulness"]["value"] == 1.0
    assert out["context_precision"]["value"] is None
    assert out["context_precision"]["scored"] == 0
    assert out["context_recall"]["value"] is None  # 50% coverage, not 1.0
    assert out["context_recall"]["coverage"] == 0.5
