"""Network-free unit tests for auth + rate limiting on POST /analyze.

workflow.invoke is mocked — no LLM/EDGAR/Tavily calls."""

import collections

import fastapi
import pytest
from fastapi.testclient import TestClient

import src.api.routes.analyze as analyze_module

_REPORT = {
    "company_name": "Apple",
    "report_date": "2024-01-01",
    "executive_summary": "summary",
    "financial_snapshot": {
        "revenue_trend": "up",
        "profitability_summary": "ok",
        "debt_assessment": "low",
        "key_metrics": {},
    },
    "risk_factors": [],
    "competitive_position": "fine",
    "recent_developments": [],
    "investment_signal": "HOLD",
    "confidence_score": 0.5,
    "data_sources_used": ["test"],
    "degraded": False,
}


class _FakeWorkflow:
    def invoke(self, state):
        return {"final_report": _REPORT}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(analyze_module, "workflow", _FakeWorkflow())
    # F-6: reset shared module-level state so these tests don't depend on
    # what ran before them (rate-limit hits, or a cached report from another
    # test file that would otherwise skip _FakeWorkflow entirely and hit
    # EDGAR for a freshness check).
    monkeypatch.setattr(analyze_module, "_request_times", collections.defaultdict(list))
    monkeypatch.setattr(analyze_module, "_report_cache", {})
    app = fastapi.FastAPI()
    app.include_router(analyze_module.router)
    return TestClient(app)


def _post(client):
    return client.post("/analyze", json={"company_name": "Apple"})


def test_no_key_configured_allows_request(client, monkeypatch):
    monkeypatch.setattr(analyze_module, "_API_KEY", "")
    resp = _post(client)
    assert resp.status_code == 200


def test_wrong_key_rejected_when_key_configured(client, monkeypatch):
    monkeypatch.setattr(analyze_module, "_API_KEY", "secret")
    resp = client.post("/analyze", json={"company_name": "Apple"}, headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_correct_key_allowed_when_key_configured(client, monkeypatch):
    monkeypatch.setattr(analyze_module, "_API_KEY", "secret")
    resp = client.post("/analyze", json={"company_name": "Apple"}, headers={"x-api-key": "secret"})
    assert resp.status_code == 200


def test_rate_limit_returns_429_past_the_cap(client, monkeypatch):
    monkeypatch.setattr(analyze_module, "_API_KEY", "")
    for _ in range(analyze_module._RATE_LIMIT):
        assert _post(client).status_code == 200
    resp = _post(client)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_poll_rejected_without_key_when_key_configured(client, monkeypatch):
    monkeypatch.setattr(analyze_module, "_API_KEY", "secret")
    monkeypatch.setattr(
        analyze_module, "_jobs", {"jid": {"status": "pending", "created": 0.0, "company": "Apple"}}
    )
    resp = client.get("/analyze/jobs/jid")
    assert resp.status_code == 401


def test_poll_allowed_without_key_when_none_configured(client, monkeypatch):
    monkeypatch.setattr(analyze_module, "_API_KEY", "")
    monkeypatch.setattr(
        analyze_module, "_jobs", {"jid": {"status": "pending", "created": 0.0, "company": "Apple"}}
    )
    resp = client.get("/analyze/jobs/jid")
    assert resp.status_code == 200
