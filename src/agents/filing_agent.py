import logging

from src.graph.state import DueDiligenceState
from src.rag.retriever import retrieve_context

logger = logging.getLogger(__name__)


def filing_agent(state: DueDiligenceState) -> dict:
    """
    Retrieves relevant filing context from vector DB.
    """

    company = state["company_name"]

    query = (
        f"Key financial performance, risks, revenue growth, "
        f"and profitability discussion for {company}"
    )

    logger.info(f"Retrieving filing context for {company}")

    docs = retrieve_context(query, company=company)

    context = [d.page_content for d in docs]

    return {
        "retrieved_context": context,
        "filing_chunks": context,
    }
