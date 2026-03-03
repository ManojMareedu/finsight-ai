# src/agents/filing_agent.py
import logging

import chromadb

from src.graph.state import DueDiligenceState
from src.rag.ingestion import ingest_company_filing
from src.rag.retriever import retrieve_context
from src.utils.config import get_settings
from src.utils.data_fetchers import resolve_ticker

logger = logging.getLogger(__name__)


def filing_agent(state: DueDiligenceState) -> dict:
    """
    Agent 2: Financial Filing RAG.

    Checks if the company filing already exists in ChromaDB.
    If not, fetches from SEC EDGAR and ingests it on-demand.
    Then retrieves relevant chunks for financial analysis.
    """
    company = state["company_name"]
    provided_ticker = state.get("company_ticker", "")
    if provided_ticker is None:
        raise ValueError("Ticker must not be None")
    ticker = resolve_ticker(company, provided_ticker)
    settings = get_settings()

    # --- On-demand ingestion ---
    # Check if we already have chunks for this company before hitting EDGAR
    try:
        client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        collection = client.get_or_create_collection("financial_filings")
        existing = collection.get(where={"company": company}, limit=1)
        already_ingested = len(existing["ids"]) > 0
    except Exception as e:
        logger.warning(f"ChromaDB check failed for {company}: {e}")
        already_ingested = False

    if not already_ingested:
        logger.info(f"No filing found for {company} — ingesting from SEC EDGAR...")
        count = ingest_company_filing(company, ticker, settings.chroma_persist_dir)
        if count == 0:
            logger.warning(
                f"Ingestion returned 0 chunks for {company}. "
                f"CIK lookup may have failed or the 10-K had no readable text."
            )
    else:
        logger.info(f"Filing already in ChromaDB for {company} — skipping ingestion")

    # --- Retrieval ---
    # Use multiple queries so the synthesis agent gets diverse context
    queries = [
        f"{company} revenue profit financial performance results",
        f"{company} risk factors business challenges regulatory",
        f"{company} competitive position market share industry",
        f"{company} future outlook growth strategy guidance",
    ]

    all_chunks: list[str] = []
    for query in queries:
        try:
            docs = retrieve_context(query, company=company)
            all_chunks.extend([d.page_content for d in docs])
        except Exception as e:
            logger.warning(f"Retrieval failed for query '{query}': {e}")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_chunks: list[str] = []
    for chunk in all_chunks:
        if chunk not in seen:
            seen.add(chunk)
            unique_chunks.append(chunk)

    logger.info(f"Filing agent retrieved {len(unique_chunks)} unique chunks for {company}")

    return {
        "filing_chunks": unique_chunks,
        "retrieved_context": unique_chunks[:8],  # top 8 chunks passed to synthesis
    }
