import logging

from src.graph.state import DueDiligenceState
from src.models.schemas import RiskAnalysis
from src.utils.llm_client import structured_chat

logger = logging.getLogger(__name__)


def risk_agent(state: DueDiligenceState) -> dict:
    """
    Analyze financial risks from retrieved filing context.
    """

    context = "\n\n".join(state.get("retrieved_context", [])[:4])

    messages = [
        {
            "role": "system",
            "content": (
                "You are a financial risk analyst. "
                "Identify key business and financial risks."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Analyze risks from this SEC filing context:\n\n{context}"
            ),
        },
    ]

    logger.info("Running risk analysis")

    result: RiskAnalysis = structured_chat(messages, RiskAnalysis)

    return {
        "identified_risks": [r.model_dump() for r in result.risks],
        "risk_score": result.overall_risk_score,
    }
