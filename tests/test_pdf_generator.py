"""PDF smoke test with markup-hostile input.

Reportlab's Paragraph mini-language parses embedded markup (including
<img src=...>), so unescaped LLM/request-controlled text is an injection
sink. This must render without raising and must not interpret the markup."""

import base64
import re
import zlib

from src.models.schemas import DueDiligenceReport, InvestmentSignal
from src.utils.pdf_generator import generate_pdf

HOSTILE = '<img src="x"/> & <b>unbalanced</b'


def _hostile_report() -> DueDiligenceReport:
    return DueDiligenceReport(
        company_name=HOSTILE,
        report_date=HOSTILE,
        executive_summary=HOSTILE,
        financial_snapshot={
            "revenue_trend": "up",
            "profitability_summary": "ok",
            "debt_assessment": "low",
            "key_metrics": {},
        },
        risk_factors=[
            {
                "category": HOSTILE,
                "description": HOSTILE,
                "severity": "HIGH",
                "source_citation": HOSTILE,
            }
        ],
        competitive_position=HOSTILE,
        recent_developments=[HOSTILE],
        investment_signal="HOLD",
        confidence_score=0.5,
        data_sources_used=["test"],
        disclaimer=HOSTILE,
    )


def test_pdf_renders_markup_hostile_input_without_raising():
    pdf_bytes = generate_pdf(_hostile_report())
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 0


def _pdf_text(pdf_bytes: bytes) -> str:
    """Reportlab ascii85s and deflates its content streams, so the words on the
    page are not in the raw bytes."""
    streams = re.findall(rb"stream\n(.*?)endstream", pdf_bytes, re.S)
    decoded = (zlib.decompress(base64.a85decode(s.strip(), adobe=True)) for s in streams)
    return b"".join(decoded).decode("latin-1")


def test_abstention_is_visible_in_the_pdf_the_user_downloads():
    report = _hostile_report()
    report.investment_signal = InvestmentSignal.INSUFFICIENT_DATA
    report.confidence_score = 0.0
    report.degraded = True

    text = _pdf_text(generate_pdf(report))

    assert "NO EVIDENCE GATHERED" in text
    assert "INSUFFICIENT_DATA" in text
