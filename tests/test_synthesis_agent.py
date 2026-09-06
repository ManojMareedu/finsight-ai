"""Network-free unit tests for src/agents/synthesis_agent.py post-validation
overrides: EDGAR wins over the LLM's key_metrics, the disclaimer can't be
rewritten by the LLM, and confidence_score is derived from measurable signals
rather than trusted from the LLM."""

import src.agents.synthesis_agent as syn_module
from src.agents.synthesis_agent import synthesis_agent
from src.models.schemas import (
    DueDiligenceReport,
    FinancialSnapshot,
    InvestmentSignal,
    RiskFactor,
    RiskLevel,
)


def _report(key_metrics, disclaimer="LLM rewrote this and it should not stick"):
    return DueDiligenceReport(
        company_name="Apple",
        executive_summary="A short summary.",
        financial_snapshot=FinancialSnapshot(
            revenue_trend="up",
            profitability_summary="healthy",
            debt_assessment="low",
            key_metrics=key_metrics,
        ),
        risk_factors=[
            RiskFactor(
                category="Market Risk",
                description="FX exposure",
                severity=RiskLevel.MEDIUM,
                source_citation="10-K",
            )
        ],
        competitive_position="strong",
        recent_developments=["new product"],
        investment_signal=InvestmentSignal.HOLD,
        confidence_score=0.99,  # LLM's own guess — must not survive
        data_sources_used=["SEC 10-K"],
        disclaimer=disclaimer,
    )


def _state(**overrides):
    state = {
        "company_name": "Apple",
        "web_search_results": ["some web context"],
        "retrieved_context": ["chunk 1", "chunk 2"],
        "identified_risks": [],
        "financial_metrics": {"revenue": "$391.00B", "eps": "$6.13"},
        "degraded": False,
    }
    state.update(overrides)
    return state


def _patch(monkeypatch, report):
    monkeypatch.setattr(syn_module, "get_company_cik", lambda company: "320193")
    monkeypatch.setattr(syn_module, "get_latest_10k_date", lambda cik: "2024-11-01")
    monkeypatch.setattr(syn_module, "structured_chat", lambda **kwargs: report)


def test_edgar_overrides_wrong_llm_key_metric(monkeypatch):
    # LLM disagrees with EDGAR on revenue and invents a metric EDGAR didn't provide.
    report = _report(key_metrics={"revenue": "$1.00B", "made_up": "42"})
    _patch(monkeypatch, report)

    out = synthesis_agent(_state())

    metrics = out["final_report"]["financial_snapshot"]["key_metrics"]
    assert metrics == {"revenue": "$391.00B", "eps": "$6.13"}  # EDGAR wins, exactly


def test_disclaimer_restored_after_llm_rewrite(monkeypatch):
    report = _report(key_metrics={})
    _patch(monkeypatch, report)

    out = synthesis_agent(_state())

    disclaimer = out["final_report"]["disclaimer"]
    assert disclaimer == DueDiligenceReport.model_fields["disclaimer"].default
    assert "does not constitute financial advice" in disclaimer.lower()


def test_confidence_drops_when_degraded_and_metrics_missing(monkeypatch):
    report = _report(key_metrics={})
    _patch(monkeypatch, report)

    healthy = synthesis_agent(
        _state(degraded=False, financial_metrics={"revenue": "$391B", "eps": "$6.13"})
    )["final_report"]["confidence_score"]

    degraded_and_sparse = synthesis_agent(
        _state(degraded=True, financial_metrics={}, retrieved_context=[])
    )["final_report"]["confidence_score"]

    assert degraded_and_sparse < healthy


def test_no_evidence_abstains_instead_of_reporting(monkeypatch):
    def _must_not_be_called(**kwargs):
        raise AssertionError("the LLM must not be asked to write a report with no evidence")

    monkeypatch.setattr(syn_module, "structured_chat", _must_not_be_called)

    report = synthesis_agent(_state(retrieved_context=[], financial_metrics={}))["final_report"]

    assert report["investment_signal"] == "INSUFFICIENT_DATA"
    assert report["confidence_score"] == 0.0
    assert report["degraded"] is True
    assert report["risk_factors"] == []
    assert "could not gather any evidence" in report["executive_summary"]


def test_risk_citations_reach_the_synthesis_prompt(monkeypatch):
    prompts = []

    def _capture(messages, schema):
        prompts.append(messages[0]["content"])
        return _report(key_metrics={})

    monkeypatch.setattr(syn_module, "get_company_cik", lambda company: "320193")
    monkeypatch.setattr(syn_module, "get_latest_10k_date", lambda cik: "2024-11-01")
    monkeypatch.setattr(syn_module, "structured_chat", _capture)

    synthesis_agent(
        _state(
            identified_risks=[
                {
                    "category": "Supply Chain",
                    "description": "Concentration of manufacturing",
                    "severity": "HIGH",
                    "source_citation": "Item 1A: substantially all of our manufacturing is "
                    "performed by outsourcing partners",
                }
            ]
        )
    )

    assert "substantially all of our manufacturing" in prompts[0]
