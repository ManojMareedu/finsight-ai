"""Validation tests for src/models/schemas.py (Pydantic v2)."""

import pytest
from pydantic import ValidationError

from src.models.schemas import (
    AnalyzeRequest,
    DueDiligenceReport,
    FinancialSnapshot,
    InvestmentSignal,
    RiskFactor,
    RiskLevel,
)


def _valid_report_kwargs(**overrides):
    kwargs = dict(
        company_name="Apple Inc.",
        executive_summary="A short summary.",
        financial_snapshot=FinancialSnapshot(
            revenue_trend="up",
            profitability_summary="healthy",
            debt_assessment="low",
            key_metrics={"revenue": "$391.00B"},
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
        confidence_score=0.8,
        data_sources_used=["SEC 10-K"],
    )
    kwargs.update(overrides)
    return kwargs


def test_confidence_score_upper_bound_rejected():
    with pytest.raises(ValidationError):
        DueDiligenceReport(**_valid_report_kwargs(confidence_score=1.5))


def test_confidence_score_lower_bound_rejected():
    with pytest.raises(ValidationError):
        DueDiligenceReport(**_valid_report_kwargs(confidence_score=-0.1))


def test_invalid_investment_signal_rejected():
    with pytest.raises(ValidationError):
        DueDiligenceReport(**_valid_report_kwargs(investment_signal="MAYBE"))


def test_valid_report_has_default_date_and_disclaimer():
    report = DueDiligenceReport(**_valid_report_kwargs())
    assert report.report_date  # default_factory populated
    assert "AI-generated" in report.disclaimer


def test_analyze_request_min_length():
    with pytest.raises(ValidationError):
        AnalyzeRequest(company_name="A")  # min_length=2


def test_analyze_request_defaults():
    req = AnalyzeRequest(company_name="Apple")
    assert req.include_pdf is False
    assert req.company_ticker is None
