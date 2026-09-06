"""Unit tests for the per-company response cache in
src/api/routes/analyze.py (item 20).

A cache hit must skip workflow.invoke entirely — that's the whole point, it's
what avoids re-running every LLM call for a repeat request. The TTL is not a
clock: it's keyed on the 10-K filing date, so a fresh filing invalidates the
entry instead of it going stale on a timer (or serving forever)."""

import collections

import fastapi
import pytest
from fastapi.testclient import TestClient

import src.api.routes.analyze as analyze_module

_REPORT = {
    "company_name": "Apple",
    "report_date": "2024-01-01",
    "executive_summary": "Doing fine.",
    "financial_snapshot": {
        "revenue_trend": "up",
        "profitability_summary": "positive",
        "debt_assessment": "low",
        "key_metrics": {},
    },
    "risk_factors": [],
    "competitive_position": "strong",
    "recent_developments": [],
    "investment_signal": "HOLD",
    "confidence_score": 0.8,
    "data_sources_used": ["SEC_10K"],
}


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch):
    monkeypatch.setattr(analyze_module, "_report_cache", {})
    monkeypatch.setattr(analyze_module, "_request_times", collections.defaultdict(list))
    monkeypatch.setattr(analyze_module, "_API_KEY", "")


@pytest.fixture
def client():
    app = fastapi.FastAPI()
    app.include_router(analyze_module.router)
    return TestClient(app)


def test_second_request_within_ttl_skips_workflow(monkeypatch, client):
    calls = {"n": 0}

    def _invoke(state):
        calls["n"] += 1
        return {"final_report": dict(_REPORT)}

    monkeypatch.setattr(analyze_module.workflow, "invoke", _invoke)
    monkeypatch.setattr(analyze_module, "get_company_cik", lambda name: "0000320193")
    monkeypatch.setattr(analyze_module, "get_latest_10k_date", lambda cik: "2024-01-01")

    r1 = client.post("/analyze", json={"company_name": "Apple"})
    assert r1.status_code == 200
    assert calls["n"] == 1

    r2 = client.post("/analyze", json={"company_name": "Apple"})
    assert r2.status_code == 200
    assert calls["n"] == 1, "cache hit must not re-invoke the workflow"


def test_newer_filing_date_invalidates_cache(monkeypatch, client):
    calls = {"n": 0}

    def _invoke(state):
        calls["n"] += 1
        return {"final_report": dict(_REPORT)}

    monkeypatch.setattr(analyze_module.workflow, "invoke", _invoke)
    monkeypatch.setattr(analyze_module, "get_company_cik", lambda name: "0000320193")
    # A newer 10-K than the one baked into _REPORT ("2024-01-01") — the
    # freshness check only runs once a cached entry exists, i.e. from the
    # second request onward.
    monkeypatch.setattr(analyze_module, "get_latest_10k_date", lambda cik: "2025-06-01")

    r1 = client.post("/analyze", json={"company_name": "Apple"})
    assert calls["n"] == 1
    assert r1.json()["report"]["report_date"] == "2024-01-01"

    r2 = client.post("/analyze", json={"company_name": "Apple"})
    assert r2.status_code == 200
    assert calls["n"] == 2, "a newer 10-K filing date must invalidate the cached entry"


def test_freshness_check_failure_falls_back_to_recompute(monkeypatch, client):
    """If EDGAR can't be reached to confirm freshness, recompute rather than
    risk serving a stale report."""
    calls = {"n": 0}

    def _invoke(state):
        calls["n"] += 1
        return {"final_report": dict(_REPORT)}

    monkeypatch.setattr(analyze_module.workflow, "invoke", _invoke)

    def _boom(name):
        raise RuntimeError("EDGAR unreachable")

    monkeypatch.setattr(analyze_module, "get_company_cik", _boom)

    client.post("/analyze", json={"company_name": "Apple"})
    client.post("/analyze", json={"company_name": "Apple"})
    assert calls["n"] == 2
