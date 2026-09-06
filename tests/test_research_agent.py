"""Network-free unit test for src/agents/research_agent.py fail-closed company
resolution: when neither the ticker heuristic nor the SEC CIK lookup can
resolve a company, the agent must raise instead of silently analyzing the
wrong company (or a None ticker crashing downstream)."""

import pytest

import src.agents.research_agent as research_module
from src.agents.research_agent import research_agent
from src.utils.data_fetchers import CompanyNotResolvedError


def test_unresolvable_company_raises_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(research_module, "resolve_ticker", lambda *a, **k: None)
    monkeypatch.setattr(research_module, "get_company_cik", lambda *a, **k: None)

    state = {"company_name": "Some Totally Unknown Shell LLC", "company_ticker": ""}

    with pytest.raises(CompanyNotResolvedError):
        research_agent(state)
