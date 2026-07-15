"""Network-free unit tests for the deterministic benchmark logic."""

import src.evaluation.benchmark as bm


class _Doc:
    def __init__(self, content, company):
        self.page_content = content
        self.metadata = {"company": company}


def test_target_company_maps_prefix():
    assert bm._target_company("apple_10k_2023") == "Apple"
    assert bm._target_company("msft_10k_2023") == "Microsoft"
    assert bm._target_company("unknown_x") == ""


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


def test_collect_with_metrics_computes_precision_recall(monkeypatch):
    # 2 of 3 retrieved chunks are the right company -> precision 2/3.
    # ground truth words: {apple, revenue, billion}; joined text contains all.
    docs = [
        _Doc("Apple total revenue was 383 billion dollars", "Apple"),
        _Doc("Apple services segment grew", "Apple"),
        _Doc("Tesla vehicle deliveries", "Tesla"),
    ]
    monkeypatch.setattr(bm, "retrieve_context", lambda q: docs)
    monkeypatch.setattr(bm, "_eval_answer", lambda q, c, s: "an answer")

    golden = [
        {
            "question": "What was Apple revenue?",
            "ground_truth": "Apple revenue billion",
            "context_source": "apple_10k_2023",
        }
    ]
    rows, recs = bm.collect_with_metrics(golden, settings=object())
    rec = recs[0]
    assert rec["retrieval_precision_at_k"] == round(2 / 3, 4)
    assert rec["retrieval_recall"] == 1.0  # apple, revenue, billion all present
    assert rec["success"] is True
    assert rec["k"] == 3
    assert "retrieval_latency_s" in rec and "total_latency_s" in rec
    assert len(rows) == 1  # a successful row is passed to RAGAS


def test_collect_with_metrics_records_failure(monkeypatch):
    def _boom(q):
        raise RuntimeError("retriever down")

    monkeypatch.setattr(bm, "retrieve_context", _boom)
    golden = [{"question": "q", "ground_truth": "g", "context_source": "apple_10k_2023"}]
    rows, recs = bm.collect_with_metrics(golden, settings=object())
    assert recs[0]["success"] is False
    assert "error" in recs[0]
    assert rows == []  # nothing sent to RAGAS


def test_empty_answer_is_not_success(monkeypatch):
    monkeypatch.setattr(bm, "retrieve_context", lambda q: [_Doc("Apple text", "Apple")])
    monkeypatch.setattr(bm, "_eval_answer", lambda q, c, s: "   ")  # blank
    golden = [{"question": "q", "ground_truth": "apple", "context_source": "apple_10k_2023"}]
    rows, recs = bm.collect_with_metrics(golden, settings=object())
    assert recs[0]["success"] is False
    assert rows == []


def test_render_markdown_has_all_sections():
    report = {
        "timestamp": "2026-07-15T00:00:00+00:00",
        "num_questions": 3,
        "config": {"judge_provider": "openrouter", "judge_model": "m", "answer_model": "a"},
        "retrieval": {"precision_at_k": 0.9625, "recall": 0.7942, "k": 8},
        "reliability": {"success_rate": 1.0, "successful": 3, "total": 3},
        "latency_s": {
            "retrieval_mean": 0.1,
            "retrieval_p95": 0.2,
            "total_mean": 5.0,
            "total_p95": 9.0,
        },
        "ragas": {
            "faithfulness": 1.0,
            "answer_relevancy": 0.2,
            "context_precision": 0.11,
            "context_recall": 0.33,
        },
    }
    md = bm.render_markdown(report)
    assert "Retrieval Precision@8" in md
    assert "0.9625" in md
    assert "Success Rate" in md
    assert "Metric definitions" in md
    assert "Reproduce" in md
    # every documented metric name appears
    for name in bm.METRIC_DOCS:
        assert name in md


def test_render_markdown_handles_missing_ragas():
    report = {
        "timestamp": "t",
        "num_questions": 1,
        "config": {"judge_provider": "ollama", "judge_model": "m", "answer_model": "m"},
        "retrieval": {"precision_at_k": 1.0, "recall": 0.5, "k": 8},
        "reliability": {"success_rate": 1.0, "successful": 1, "total": 1},
        "latency_s": {
            "retrieval_mean": 0.1,
            "retrieval_p95": 0.1,
            "total_mean": 1.0,
            "total_p95": 1.0,
        },
        "ragas": None,
    }
    md = bm.render_markdown(report)
    assert "n/a" in md  # RAGAS rows render as n/a, not a crash
