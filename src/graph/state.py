from operator import add
from typing import Annotated, List, Optional, TypedDict


class DueDiligenceState(TypedDict):
    # INPUT
    company_name: str
    company_ticker: Optional[str]

    # Research agent outputs
    web_search_results: List[str]
    news_articles: List[dict]

    # Filing RAG outputs
    filing_chunks: List[str]
    retrieved_context: List[str]

    # Risk agent outputs
    identified_risks: List[dict]
    risk_score: float

    # Synthesis output
    final_report: Optional[dict]

    # Control flow
    research_complete: bool
    iterations: Annotated[int, add]   # reducer → increments
    error_messages: List[str]
