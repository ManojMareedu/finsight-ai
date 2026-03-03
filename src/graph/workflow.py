
import logging

from langgraph.graph import END, StateGraph

from src.agents.filing_agent import filing_agent
from src.agents.research_agent import research_agent
from src.agents.risk_agent import risk_agent
from src.agents.synthesis_agent import synthesis_agent
from src.graph.state import DueDiligenceState

logger = logging.getLogger(__name__)


def route_after_risk(state: DueDiligenceState) -> str:
    """
    Conditional edge after risk assessment.
    If risk is high and we have not looped more than twice,
    send back to research for a deeper pass.
    Otherwise proceed to synthesis.
    """
    risk_score = state.get("risk_score", 0.0)
    iterations = state.get("iterations", 0)

    if risk_score > 0.7 and iterations < 3:
        logger.info(
            f"Risk score {risk_score} > 0.7 and iterations={iterations} "
            f"— looping back to research"
        )
        return "needs_deeper_research"

    return "proceed_to_synthesis"


def build_workflow():
    """
    Build and compile the LangGraph workflow.

    Flow:
        research -> filing -> risk ---[low risk]---> synthesis -> END
                                  \--[high risk]--> research (loop, max 3x)
    """
    graph = StateGraph(DueDiligenceState)

    # Register nodes
    graph.add_node("research", research_agent)
    graph.add_node("filing", filing_agent)
    graph.add_node("risk", risk_agent)
    graph.add_node("synthesis", synthesis_agent)

    # Entry point
    graph.set_entry_point("research")

    # Fixed edges
    graph.add_edge("research", "filing")
    graph.add_edge("filing", "risk")

    # Conditional edge after risk
    graph.add_conditional_edges(
        "risk",
        route_after_risk,
        {
            "needs_deeper_research": "research",
            "proceed_to_synthesis": "synthesis",
        },
    )

    graph.add_edge("synthesis", END)

    return graph.compile()