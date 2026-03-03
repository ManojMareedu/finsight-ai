
import logging

from src.graph.state import DueDiligenceState
from src.models.schemas import DueDiligenceReport
from src.utils.data_fetchers import get_company_cik, get_latest_10k_date
from src.utils.llm_client import structured_chat

logger = logging.getLogger(__name__)

SYNTHESIS_PROMPT = """You are a senior financial analyst writing a due diligence report.

Company: {company}
Data as of: {data_as_of} (most recent 10-K filing date — use this exact date as report_date)

IMPORTANT: This report is based on SEC filings dated {data_as_of}. The analysis
reflects the company's position as of that filing date, not today's date.

Web Research and Live Financial Data:
{web_context}

SEC Filing Excerpts:
{filing_context}

Identified Risk Factors:
{risks}

Rules:
- report_date must be exactly: {data_as_of}
- executive_summary: 3-4 sentences, plain language, no jargon
- financial_snapshot.key_metrics: extract revenue, revenue_growth_yoy, gross_margin,
  net_income, eps, debt_ratio from the web context above. Use exact values shown.
  Only say "not available" if the number is genuinely absent from the context.
- risk_factors: map each identified risk to the RiskFactor schema exactly
- investment_signal: one of STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL
- confidence_score: 0.0 to 1.0, lower if data is limited or filing is over 12 months old
- data_sources_used: list actual sources (e.g. "SEC 10-K filing 2024-07-30", "EDGAR XBRL facts")
- Base ALL claims only on provided context. Do not invent numbers.
"""


def synthesis_agent(state: DueDiligenceState) -> dict:
    """
    Agent 4: Report Synthesis.

    Combines all gathered context into a structured DueDiligenceReport.
    Stamps the report with the actual 10-K filing date, not today's date,
    so readers know exactly how current the underlying data is.
    """
    company = state["company_name"]

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
    web_context = "\n".join(state.get("web_search_results", [])[:5])
    filing_context = "\n---\n".join(state.get("retrieved_context", [])[:6])
    risks = state.get("identified_risks", [])

    risk_lines = [
        f"- [{r.get('severity', 'MEDIUM')}] {r.get('category', '')}: "
        f"{r.get('description', '')}"
        for r in risks
    ]
    risk_summary = "\n".join(risk_lines) if risk_lines else "No risks identified."

    prompt = SYNTHESIS_PROMPT.format(
        company=company,
        data_as_of=data_as_of,
        web_context=web_context or "No web data available.",
        filing_context=filing_context or "No filing context available.",
        risks=risk_summary,
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
