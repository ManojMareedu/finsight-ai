from langgraph.graph import StateGraph, END
from src.graph.state import DueDiligenceState

from src.agents.filing_agent import filing_agent
from src.agents.risk_agent import risk_agent
from src.agents.synthesis_agent import synthesis_agent


def build_workflow():
    """
    Build LangGraph workflow connecting all agents.
    """

    graph = StateGraph(DueDiligenceState)

    # Nodes
    graph.add_node("filing", filing_agent)
    graph.add_node("risk", risk_agent)
    graph.add_node("synthesis", synthesis_agent)

    # Flow
    graph.set_entry_point("filing")

    graph.add_edge("filing", "risk")
    graph.add_edge("risk", "synthesis")
    graph.add_edge("synthesis", END)

    return graph.compile()