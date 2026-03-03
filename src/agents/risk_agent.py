
import logging

from src.graph.state import DueDiligenceState
from src.utils.llm_client import chat
import json

logger = logging.getLogger(__name__)

RISK_PROMPT = """You are a financial risk analyst. Based on the context below, identify
3 to 6 key risk factors for {company}.

Context from SEC filing and news:
{context}

Return a JSON array only. No markdown. No explanation. Example format:
[
  {{
    "category": "Market Risk",
    "description": "Exposure to interest rate changes affecting loan portfolio",
    "severity": "HIGH",
    "source_citation": "SEC 10-K Risk Factors section"
  }}
]

Severity must be one of: LOW, MEDIUM, HIGH, CRITICAL.
Always return at least 3 risk factors even if context is limited.
"""


def risk_agent(state: DueDiligenceState) -> dict:
    """
    Agent 3: Risk Assessment.

    Reads retrieved filing context and web research, identifies risk factors,
    and calculates a risk score. Score > 0.7 triggers a deeper research loop
    via the conditional edge in workflow.py.
    """
    company = state["company_name"]

    # Combine filing context and web results for richer risk analysis
    filing_context = state.get("retrieved_context", [])[:5]
    web_context = state.get("web_search_results", [])[:3]
    combined = "\n---\n".join(filing_context + web_context)

    if not combined.strip():
        logger.warning(f"Risk agent has no context for {company} — using minimal fallback")
        combined = f"Company: {company}. No filing context retrieved."

    prompt = RISK_PROMPT.format(company=company, context=combined[:4000])

    logger.info(f"Running risk analysis for {company}")

    raw = chat([{"role": "user", "content": prompt}])

    # Parse the JSON array the LLM returns
    risks: list[dict] = []
    try:
        clean = raw.strip()
        if "```" in clean:
            clean = clean.replace("```json", "").replace("```", "").strip()
        start = clean.find("[")
        end = clean.rfind("]") + 1
        if start != -1 and end > start:
            risks = json.loads(clean[start:end])
    except Exception as e:
        logger.warning(f"Risk JSON parse failed: {e}. Raw: {raw[:300]}")

    # Normalise severity field — LLM sometimes returns lowercase
    valid_severities = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    for r in risks:
        sev = str(r.get("severity", "MEDIUM")).upper()
        r["severity"] = sev if sev in valid_severities else "MEDIUM"
        # Ensure all required fields exist
        r.setdefault("category", "General Risk")
        r.setdefault("description", "Risk identified from filing context")
        r.setdefault("source_citation", "SEC 10-K")

    # Derive a 0.0-1.0 risk score from severity distribution
    severity_weights = {"CRITICAL": 1.0, "HIGH": 0.7, "MEDIUM": 0.4, "LOW": 0.1}
    if risks:
        risk_score = round(
            sum(severity_weights.get(r["severity"], 0.4) for r in risks) / len(risks),
            3,
        )
    else:
        risk_score = 0.3  # default moderate if parsing failed
        risks = [
            {
                "category": "Data Unavailable",
                "description": "Could not extract risk factors from available context.",
                "severity": "MEDIUM",
                "source_citation": "System fallback",
            }
        ]

    logger.info(f"Risk agent: {len(risks)} risks identified, score={risk_score}")

    return {
        "identified_risks": risks,
        "risk_score": risk_score,
    }