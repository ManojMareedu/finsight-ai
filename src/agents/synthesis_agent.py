from src.graph.state import DueDiligenceState
from src.utils.llm_client import structured_chat
from src.models.schemas import FinalReport
import logging

logger = logging.getLogger(__name__)


def synthesis_agent(state: DueDiligenceState) -> dict:
    """
    Generate final analyst-style report.
    """

    context = "\n\n".join(state.get("retrieved_context", [])[:3])

    risks = state.get("identified_risks", [])
    risk_score = state.get("risk_score", 0)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior financial analyst. "
                "Generate a concise investment due diligence summary."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Company: {state.get('company_name')}\n\n"
                f"Risk score: {risk_score}\n\n"
                f"Identified risks: {risks}\n\n"
                f"Filing context:\n{context}"
            ),
        },
    ]

    logger.info("Generating final report")

    result: FinalReport = structured_chat(messages, FinalReport)

    return {
        "final_report": result.model_dump()
    }