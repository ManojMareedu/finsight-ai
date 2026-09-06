"""Network-free tests for src/graph/workflow.route_after_risk.

F10/J7: this conditional edge was entirely untested. If the iteration guard
is dropped, a persistently high risk score loops the graph forever."""

from src.agents.research_agent import _build_queries
from src.graph.workflow import route_after_risk


def test_high_risk_loops_back_to_research():
    state = {"risk_score": 0.9, "iterations": 0}
    assert route_after_risk(state) == "needs_deeper_research"


def test_low_risk_proceeds_to_synthesis():
    state = {"risk_score": 0.2, "iterations": 0}
    assert route_after_risk(state) == "proceed_to_synthesis"


def test_high_risk_but_iteration_bound_reached_proceeds():
    # Without the iterations < 3 guard this would loop forever.
    state = {"risk_score": 0.95, "iterations": 3}
    assert route_after_risk(state) == "proceed_to_synthesis"


def test_missing_state_keys_default_to_safe_values():
    assert route_after_risk({}) == "proceed_to_synthesis"


def test_rerun_queries_target_identified_risks():
    # J13: a re-research pass must search something the first pass didn't,
    # or the loop is a 3x-cost no-op. The second pass's queries should differ
    # from the first pass's and reference the risk categories found.
    first_pass = _build_queries("Acme Corp", [])
    risks = [
        {"category": "Supply Chain", "description": "..."},
        {"category": "Regulatory", "description": "..."},
    ]
    second_pass = _build_queries("Acme Corp", risks)

    assert second_pass != first_pass
    assert any("Supply Chain" in q for q in second_pass)
    assert any("Regulatory" in q for q in second_pass)
