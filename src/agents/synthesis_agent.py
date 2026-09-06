import datetime
import logging

from src.agents.risk_agent import no_evidence, sanitize_web_snippet, wrap_untrusted
from src.graph.state import DueDiligenceState
from src.models.schemas import (
    DueDiligenceReport,
    FinancialSnapshot,
    InvestmentSignal,
)
from src.utils.data_fetchers import get_company_cik, get_latest_10k_date
from src.utils.llm_client import structured_chat

logger = logging.getLogger(__name__)

# Cap on each untrusted block before it goes into the prompt. Filing/web
# context here is not bounded upstream the way risk_agent's is, and an
# unbounded prompt is both a cost problem and, per wrap_untrusted's docstring,
# has to be truncated *before* wrapping — truncating after cuts the closing
# delimiter off and leaves the block looking unterminated.
_MAX_CONTEXT_CHARS = 4000

# The 6 metrics get_financials_from_edgar tries to fill (see data_fetchers.py).
# Used only to score how complete the authoritative data is for confidence.
_EXPECTED_EDGAR_FIELDS = (
    "revenue",
    "revenue_growth_yoy",
    "gross_margin",
    "net_income",
    "eps",
    "debt_ratio",
)

SYNTHESIS_PROMPT = """You are a senior financial analyst writing a due diligence report.

Company: {company}
Data as of: {data_as_of} (most recent 10-K filing date — use this exact date as report_date)

IMPORTANT: This report is based on SEC filings dated {data_as_of}. The analysis
reflects the company's position as of that filing date, not today's date.

Web Research:
{web_context}

Authoritative EDGAR figures (exact — use these numbers in your narrative;
they are merged into key_metrics automatically, do not restate them there):
{edgar_context}

SEC Filing Excerpts:
{filing_context}

Identified Risk Factors:
{risks}

Rules:
- report_date must be exactly: {data_as_of}
- executive_summary: 3-4 sentences, plain language, no jargon
- financial_snapshot.key_metrics: leave this as an empty object {{}}. The authoritative
  EDGAR figures are merged in automatically after you respond — do not guess numbers.
- risk_factors: map each identified risk to the RiskFactor schema exactly, copying its
  source_citation string across verbatim. Never replace a citation with the name of the
  document it came from.
- investment_signal: one of STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL
- confidence_score: 0.0 to 1.0 (this is recomputed from measurable signals afterward,
  but still provide your best estimate)
- data_sources_used: list actual sources (e.g. "SEC 10-K filing 2024-07-30", "EDGAR XBRL facts")
- Base ALL claims only on provided context. Do not invent numbers.
"""


def _compute_confidence(
    financial_metrics: dict,
    retrieved_context: list,
    web_search_results: list,
    degraded: bool,
    data_as_of: str,
) -> float:
    """
    Confidence score derived from measurable signals instead of a free LLM's
    unfounded self-rating. Weighted sum of five signals that already exist in
    state; weights are named below and sum to 1.0.

    The weights are a fixed heuristic, not calibrated against outcomes —
    revisit if we ever have labeled report quality to fit them against.
    """
    edgar_completeness = sum(1 for f in _EXPECTED_EDGAR_FIELDS if financial_metrics.get(f)) / len(
        _EXPECTED_EDGAR_FIELDS
    )

    try:
        age_days = (datetime.date.today() - datetime.date.fromisoformat(data_as_of)).days
        freshness = 1.0 if age_days <= 365 else 0.5 if age_days <= 730 else 0.2
    except (ValueError, TypeError):
        freshness = 0.0  # data_as_of == "date unknown"

    retrieval_depth = min(len(retrieved_context) / 8, 1.0)  # 8 = target chunk count
    risk_parse_ok = 0.0 if degraded else 1.0
    web_search_present = 1.0 if web_search_results else 0.0

    score = (
        0.35 * edgar_completeness
        + 0.25 * freshness
        + 0.20 * retrieval_depth
        + 0.15 * risk_parse_ok
        + 0.05 * web_search_present
    )
    return round(min(max(score, 0.0), 1.0), 3)


NO_EVIDENCE_SUMMARY = (
    "FinSight could not gather any evidence for this company: no SEC filing text was "
    "retrieved and no EDGAR financial figures were found. No assessment was produced. "
    "This is a refusal, not a neutral verdict — check the company name and whether the "
    "company files with the SEC."
)


def _abstention_report(company: str) -> DueDiligenceReport:
    """The report for a company we have nothing on. Deliberately not generated
    by the LLM: with no evidence there is nothing for it to do but invent."""
    return DueDiligenceReport(
        company_name=company,
        executive_summary=NO_EVIDENCE_SUMMARY,
        financial_snapshot=FinancialSnapshot(
            revenue_trend="No data retrieved",
            profitability_summary="No data retrieved",
            debt_assessment="No data retrieved",
            key_metrics={},
        ),
        risk_factors=[],
        competitive_position="No data retrieved",
        recent_developments=[],
        investment_signal=InvestmentSignal.INSUFFICIENT_DATA,
        confidence_score=0.0,
        data_sources_used=[],
        degraded=True,
    )


def synthesis_agent(state: DueDiligenceState) -> dict:
    """
    Agent 4: Report Synthesis.

    Combines all gathered context into a structured DueDiligenceReport.
    Stamps the report with the actual 10-K filing date, not today's date,
    so readers know exactly how current the underlying data is.
    """
    company = state["company_name"]

    if no_evidence(state):
        logger.warning(
            f"No filing chunks and no EDGAR figures for {company} — abstaining instead of "
            "generating a report"
        )
        return {"final_report": _abstention_report(company).model_dump(), "research_complete": True}

    # --- Get actual filing date to stamp the report honestly ---
    # This runs inside the function where 'company' exists
    data_as_of = "date unknown"
    try:
        cik = get_company_cik(company)
        if cik:
            filing_date = get_latest_10k_date(cik)
            if filing_date:
                data_as_of = filing_date
                logger.info(f"Filing date for {company}: {data_as_of}")
    except Exception as e:
        logger.warning(f"Could not fetch filing date for {company}: {e}")

    # --- Build context strings ---
    financial_metrics = state.get("financial_metrics", {}) or {}
    edgar_context = (
        "\n".join(f"{k}: {v}" for k, v in financial_metrics.items())
        or "No EDGAR figures available."
    )
    raw_web_results = state.get("web_search_results", [])[:5]
    web_context = "\n".join(sanitize_web_snippet(w) for w in raw_web_results)[:_MAX_CONTEXT_CHARS]
    filing_context = "\n---\n".join(state.get("retrieved_context", [])[:6])[:_MAX_CONTEXT_CHARS]
    risks = state.get("identified_risks", [])

    risk_lines = [
        f"- [{r.get('severity', 'MEDIUM')}] {r.get('category', '')}: "
        f"{r.get('description', '')}\n"
        f"  source_citation: {r.get('source_citation', '')}"
        for r in risks
    ]
    risk_summary = "\n".join(risk_lines)[:_MAX_CONTEXT_CHARS]

    prompt = SYNTHESIS_PROMPT.format(
        company=company,
        data_as_of=data_as_of,
        edgar_context=edgar_context,
        web_context=wrap_untrusted(web_context) if web_context else "No web data available.",
        filing_context=(
            wrap_untrusted(filing_context) if filing_context else "No filing context available."
        ),
        # risk_summary is built from risk_agent's category/description fields,
        # which are LLM output derived from the same untrusted filing/web text
        # — it needs the same delimiter treatment as the other two blocks
        # rather than landing in the prompt as if it were our own instruction.
        risks=wrap_untrusted(risk_summary) if risk_lines else "No risks identified.",
    )

    logger.info(f"Generating final report for {company} (data as of {data_as_of})")

    report: DueDiligenceReport = structured_chat(
        messages=[{"role": "user", "content": prompt}],
        schema=DueDiligenceReport,
    )

    # Safety net: if the LLM ignored the date instruction, set it explicitly
    if report.report_date != data_as_of and data_as_of != "date unknown":
        logger.warning(
            f"LLM returned report_date={report.report_date}, "
            f"overriding with actual filing date {data_as_of}"
        )
        report.report_date = data_as_of

    # EDGAR wins: overwrite whatever the LLM wrote for key_metrics with the
    # authoritative dict fetched by research_agent. Log every disagreement
    # and every EDGAR field the LLM dropped, so drift is visible in logs.
    if financial_metrics:
        llm_metrics = report.financial_snapshot.key_metrics
        for key, edgar_value in financial_metrics.items():
            llm_value = llm_metrics.get(key)
            if llm_value is not None and str(llm_value) != str(edgar_value):
                logger.warning(
                    f"{company}: LLM key_metrics.{key}={llm_value!r} disagreed with "
                    f"EDGAR value {edgar_value!r} — EDGAR overrides"
                )
        for key in financial_metrics:
            if key not in llm_metrics:
                logger.warning(f"{company}: LLM omitted EDGAR metric '{key}'")
        report.financial_snapshot.key_metrics = dict(financial_metrics)

    # The disclaimer is not the LLM's to rewrite — restore the schema default.
    report.disclaimer = DueDiligenceReport.model_fields["disclaimer"].default

    # Degradation is part of the report, not just an input to confidence.
    report.degraded = state.get("degraded", False)

    # Confidence score comes from measurable signals, not the LLM's guess
    # (dropped entirely rather than blended — see _compute_confidence).
    report.confidence_score = _compute_confidence(
        financial_metrics=financial_metrics,
        retrieved_context=state.get("retrieved_context", []),
        web_search_results=state.get("web_search_results", []),
        degraded=state.get("degraded", False),
        data_as_of=data_as_of,
    )

    logger.info(
        f"Synthesis complete for {company}: "
        f"signal={report.investment_signal}, "
        f"confidence={report.confidence_score}, "
        f"report_date={report.report_date}"
    )

    return {
        "final_report": report.model_dump(),
        "research_complete": True,
    }
