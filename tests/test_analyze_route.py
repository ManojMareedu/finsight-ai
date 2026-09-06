"""Network-free tests for POST /analyze via FastAPI TestClient, workflow.invoke
mocked. F10/J7: zero coverage previously existed for this route — exactly
where a wrong investment signal, or a silent guess on an unresolvable
company, would ship undetected."""

import collections
import time

import pytest
from fastapi.testclient import TestClient

import src.api.routes.analyze as analyze_route
from src.api.main import app
from src.utils.data_fetchers import CompanyNotResolvedError

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    # F-6: this module's tests share the rate limiter and response cache with
    # every other test file that imports src.api.routes.analyze — reset both
    # so results don't depend on test order.
    monkeypatch.setattr(analyze_route, "_request_times", collections.defaultdict(list))
    monkeypatch.setattr(analyze_route, "_report_cache", {})


_FAKE_REPORT = {
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


def test_analyze_happy_path(monkeypatch):
    monkeypatch.setattr(
        analyze_route.workflow, "invoke", lambda state: {"final_report": _FAKE_REPORT}
    )
    resp = client.post("/analyze", json={"company_name": "Apple"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["company"] == "Apple"
    assert body["report"]["investment_signal"] == "HOLD"


def test_analyze_unresolvable_company_fails_closed(monkeypatch):
    def _boom(state):
        raise CompanyNotResolvedError("cannot resolve company to a ticker/CIK")

    monkeypatch.setattr(analyze_route.workflow, "invoke", _boom)
    resp = client.post("/analyze", json={"company_name": "Zeta Corp"})
    assert resp.status_code == 422


def test_analyze_dispatches_blocking_call_to_a_thread(monkeypatch):
    """Item 18: workflow.invoke must run off the event loop. If this ever goes
    back to a direct/awaited call, this fails."""
    calls = []

    async def _fake_to_thread(fn, *args):
        calls.append(fn)
        return fn(*args)

    monkeypatch.setattr(analyze_route.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(analyze_route, "_run_pipeline", lambda company, ticker: dict(_FAKE_REPORT))

    resp = client.post("/analyze", json={"company_name": "Apple"})
    assert resp.status_code == 200
    assert calls == [analyze_route._run_pipeline]


def test_job_submit_returns_id_and_poll_returns_finished_report(monkeypatch):
    monkeypatch.setattr(
        analyze_route.workflow, "invoke", lambda state: {"final_report": dict(_FAKE_REPORT)}
    )

    submit = client.post("/analyze/jobs", json={"company_name": "Apple"})
    assert submit.status_code == 200
    body = submit.json()
    job_id = body["job_id"]
    assert body["status"] == "pending"

    import time as _time

    poll = None
    for _ in range(100):
        poll = client.get(f"/analyze/jobs/{job_id}")
        if poll.json()["status"] != "pending":
            break
        _time.sleep(0.02)

    assert poll is not None and poll.json()["status"] == "done"
    assert poll.json()["report"]["investment_signal"] == "HOLD"


def test_poll_unknown_job_id_404s():
    resp = client.get("/analyze/jobs/does-not-exist")
    assert resp.status_code == 404


def test_job_ids_are_unguessable(monkeypatch):
    """T2-2: ids must not be a counter or uuid1 — both would make polling an
    enumeration attack, not just unauthenticated volume."""
    monkeypatch.setattr(
        analyze_route.workflow, "invoke", lambda state: {"final_report": dict(_FAKE_REPORT)}
    )
    ids = [
        client.post("/analyze/jobs", json={"company_name": "Apple"}).json()["job_id"]
        for _ in range(2)
    ]
    assert ids[0] != ids[1]
    for job_id in ids:
        assert len(job_id) >= 40
        assert not all(c in "0123456789abcdef" for c in job_id), "looks like a uuid hex id"


def test_job_submission_rejected_at_capacity(monkeypatch):
    monkeypatch.setattr(analyze_route, "_MAX_JOBS", 1)
    monkeypatch.setattr(
        analyze_route,
        "_jobs",
        {"existing": {"status": "pending", "created": time.time(), "company": "X"}},
    )
    resp = client.post("/analyze/jobs", json={"company_name": "Apple"})
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers
