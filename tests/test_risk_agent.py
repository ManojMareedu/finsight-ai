"""Unit tests for src/agents/risk_agent.py with the LLM call mocked.

Covers JSON parsing, severity normalization, score derivation, and the
fallback path when the model returns unparseable output."""

import json

import src.agents.risk_agent as risk_module
from src.agents.risk_agent import risk_agent


def _state():
    return {
        "company_name": "Apple",
        "retrieved_context": ["some 10-K risk text"],
        "web_search_results": ["some web context"],
    }


def _patch_chat(monkeypatch, raw):
    monkeypatch.setattr(risk_module, "chat", lambda *a, **k: raw)


def test_score_from_severity_distribution(monkeypatch):
    risks = [
        {"category": "A", "description": "d", "severity": "HIGH", "source_citation": "s"},
        {"category": "B", "description": "d", "severity": "HIGH", "source_citation": "s"},
        {"category": "C", "description": "d", "severity": "CRITICAL", "source_citation": "s"},
    ]
    _patch_chat(monkeypatch, json.dumps(risks))
    out = risk_agent(_state())
    # (0.7 + 0.7 + 1.0) / 3 = 0.8
    assert out["risk_score"] == 0.8
    assert len(out["identified_risks"]) == 3


def test_severity_normalized_and_defaulted(monkeypatch):
    risks = [
        {"category": "A", "description": "d", "severity": "high"},  # lowercase
        {"category": "B", "description": "d", "severity": "bogus"},  # invalid -> MEDIUM
    ]
    _patch_chat(monkeypatch, json.dumps(risks))
    out = risk_agent(_state())
    sevs = [r["severity"] for r in out["identified_risks"]]
    assert sevs == ["HIGH", "MEDIUM"]
    # missing fields backfilled
    assert all("source_citation" in r for r in out["identified_risks"])


def test_markdown_fenced_json_is_parsed(monkeypatch):
    risks = [{"category": "A", "description": "d", "severity": "LOW", "source_citation": "s"}]
    _patch_chat(monkeypatch, "```json\n" + json.dumps(risks) + "\n```")
    out = risk_agent(_state())
    assert out["identified_risks"][0]["severity"] == "LOW"
    assert out["risk_score"] == 0.1


def test_unparseable_output_uses_fallback(monkeypatch):
    _patch_chat(monkeypatch, "the model refused and wrote prose with no array")
    out = risk_agent(_state())
    assert out["risk_score"] == 0.3
    assert len(out["identified_risks"]) == 1
    assert out["identified_risks"][0]["category"] == "Data Unavailable"
    assert out["degraded"] is True


def test_json_array_of_strings_does_not_raise(monkeypatch):
    # A model returning ["competition", "supply chain"] used to raise
    # AttributeError from r.get(...) on a plain string and 500 the analysis.
    _patch_chat(monkeypatch, json.dumps(["competition", "supply chain"]))
    out = risk_agent(_state())
    # No dicts survive the isinstance filter -> falls back like unparseable output.
    assert out["risk_score"] == 0.3
    assert out["identified_risks"][0]["category"] == "Data Unavailable"
    assert out["degraded"] is True


def test_llm_call_failure_degrades_instead_of_raising(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(risk_module, "chat", _boom)
    out = risk_agent(_state())  # must not raise
    assert out["risk_score"] == 0.3
    assert out["degraded"] is True


def test_no_filing_and_no_financials_is_degraded(monkeypatch):
    # The model still answers confidently with nothing to go on; the report must
    # not inherit that confidence.
    risks = [{"category": "A", "description": "d", "severity": "HIGH", "source_citation": "s"}] * 3
    _patch_chat(monkeypatch, json.dumps(risks))

    out = risk_agent(
        {
            "company_name": "Qqqqq Industries",
            "retrieved_context": [],
            "web_search_results": ["a search result about something else"],
            "financial_metrics": {},
        }
    )

    assert out["degraded"] is True
