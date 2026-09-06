import logging

from src.graph.state import DueDiligenceState
from src.utils.data_fetchers import (
    CompanyNotResolvedError,
    get_company_cik,
    get_financials_from_edgar,
    resolve_ticker,
)

logger = logging.getLogger(__name__)


def _build_queries(company: str, identified_risks: list) -> list:
    """
    Build the Tavily search queries for this pass.

    On the first pass there is nothing to target yet, so the queries are
    generic. On a re-research pass (risk_agent routed back with high risk),
    identified_risks carries what the previous pass actually found — search
    those categories instead of repeating the same three generic queries and
    getting back the same three answers.
    """
    categories = [r.get("category") for r in identified_risks if r.get("category")]
    if categories:
        return [f"{company} {category} risk recent developments" for category in categories[:3]]
    return [
        f"{company} stock news earnings 2024 2025",
        f"{company} financial results revenue growth",
        f"{company} risks challenges competitive threats",
    ]


def research_agent(state: DueDiligenceState) -> dict:
    """
    Agent 1: Web Research + Financial Metrics.

    Fetches financial metrics from SEC EDGAR (XBRL company facts) via
    get_financials_from_edgar. Tavily web search is optional — if the API key is missing
    it degrades gracefully and still returns the EDGAR financials. On a re-research
    pass, the web queries target the risks the previous pass surfaced (see
    _build_queries) instead of repeating the first pass verbatim.
    """
    company = state["company_name"]
    provided_ticker = state.get("company_ticker", "")
    if provided_ticker is None:
        raise ValueError("Ticker must not be None")
    ticker = resolve_ticker(company, provided_ticker)

    # Fail closed: if the ticker heuristic can't confidently resolve the
    # company, fall back to the authoritative SEC CIK lookup by full name.
    # If that also fails, refuse to guess rather than analyze the wrong
    # company (see CompanyNotResolvedError docstring).
    cik = get_company_cik(ticker or company)
    if not cik:
        raise CompanyNotResolvedError(
            f"Could not resolve '{company}' to a known ticker or SEC CIK."
        )

    logger.info(f"Research agent running for {company} (ticker: {ticker}, CIK: {cik})")

    web_results: list[str] = []
    news_articles: list[dict] = []

    # --- Tavily web search (optional) ---
    try:
        from src.utils.config import get_settings

        settings = get_settings()

        if settings.tavily_api_key and settings.tavily_api_key != "your_tavily_api_key_here":
            from tavily import TavilyClient

            client = TavilyClient(api_key=settings.tavily_api_key)

            queries = _build_queries(company, state.get("identified_risks", []))

            for query in queries:
                try:
                    resp = client.search(
                        query=query,
                        max_results=3,
                        search_depth="advanced",
                        include_answer=True,
                    )
                    answer = resp.get("answer", "")
                    if answer:
                        web_results.append(answer)

                    for r in resp.get("results", []):
                        news_articles.append(
                            {
                                "title": r.get("title", ""),
                                "url": r.get("url", ""),
                                "snippet": r.get("content", "")[:500],
                            }
                        )
                except Exception as e:
                    logger.warning(f"Tavily search failed for '{query}': {e}")
        else:
            logger.info("Tavily API key not set — skipping web search")

    except Exception as e:
        logger.warning(f"Tavily setup failed: {e}")

    # --- EDGAR financial metrics (always runs, no API key needed) ---
    # This dict is authoritative — synthesis_agent overwrites the LLM's
    # key_metrics with it rather than trusting a free model to re-extract
    # numbers from prose (see synthesis_agent.py). No need to also flatten
    # it into web_search_results; that was pure duplication.
    financial_metrics = get_financials_from_edgar(cik)
    if financial_metrics:
        logger.info(f"EDGAR metrics for {ticker} (CIK {cik}): {financial_metrics}")
    else:
        logger.warning(f"No EDGAR financial metrics returned for CIK {cik}")

    return {
        "web_search_results": web_results,
        "news_articles": news_articles,
        "financial_metrics": financial_metrics,
        "iterations": 1,
    }
