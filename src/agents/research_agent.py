
import logging

from src.graph.state import DueDiligenceState
from src.utils.data_fetchers import get_stock_info, resolve_ticker

logger = logging.getLogger(__name__)


def research_agent(state: DueDiligenceState) -> dict:
    """
    Agent 1: Web Research + Stock Data.

    Fetches live stock metrics via yfinance.
    Tavily web search is optional — if the API key is missing it
    degrades gracefully and still returns stock data.
    """
    company = state["company_name"]
    provided_ticker = state.get("company_ticker", "")
    ticker = resolve_ticker(company, provided_ticker)

    logger.info(f"Research agent running for {company} (ticker: {ticker})")

    web_results: list[str] = []
    news_articles: list[dict] = []

    # --- Tavily web search (optional) ---
    try:
        from src.utils.config import get_settings
        settings = get_settings()

        if settings.tavily_api_key and settings.tavily_api_key != "your_tavily_api_key_here":
            from tavily import TavilyClient
            client = TavilyClient(api_key=settings.tavily_api_key)

            queries = [
                f"{company} stock news earnings 2024 2025",
                f"{company} financial results revenue growth",
                f"{company} risks challenges competitive threats",
            ]

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
                        news_articles.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("content", "")[:500],
                        })
                except Exception as e:
                    logger.warning(f"Tavily search failed for '{query}': {e}")
        else:
            logger.info("Tavily API key not set — skipping web search")

    except Exception as e:
        logger.warning(f"Tavily setup failed: {e}")

    # --- yfinance stock data (always runs, no API key needed) ---
    stock_data = get_stock_info(ticker)
    if stock_data:
        stock_summary = (
            f"{company} stock data: "
            + ", ".join(f"{k}={v}" for k, v in stock_data.items() if v != "N/A")
        )
        web_results.append(stock_summary)
        logger.info(f"Stock data fetched for {ticker}: {stock_data}")
    else:
        logger.warning(f"No stock data returned for ticker {ticker}")

    return {
        "web_search_results": web_results,
        "news_articles": news_articles,
        "iterations": 1,
    }