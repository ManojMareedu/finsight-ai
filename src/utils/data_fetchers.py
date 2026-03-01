import logging
from typing import Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

EDGAR_HEADERS = {
    "User-Agent": "FinSightAI manoj.mareedu.pro@gmail.com"
}


def get_company_cik(company_name: str) -> Optional[str]:
    """
    Robust CIK lookup using SEC company_tickers.json
    """
    url = "https://www.sec.gov/files/company_tickers.json"

    data = requests.get(
        url,
        headers=EDGAR_HEADERS,
        timeout=10
    ).json()

    company_lower = company_name.lower()

    for _, item in data.items():
        if company_lower in item["title"].lower():
            return str(item["cik_str"])

    return None


def get_latest_10k_text(cik: str, max_chars: int = 50000) -> str:
    """
    Download latest 10-K filing text using SEC submission API.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"

    data = requests.get(
        url,
        headers=EDGAR_HEADERS,
        timeout=15
    ).json()

    filings = data.get("filings", {}).get("recent", {})

    for form, accnum, primary_doc in zip(
        filings.get("form", []),
        filings.get("accessionNumber", []),
        filings.get("primaryDocument", [])
    ):
        if form == "10-K":
            acc_no_dash = accnum.replace("-", "")
            filing_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc_no_dash}/{primary_doc}"
            )

            text = requests.get(
                filing_url,
                headers=EDGAR_HEADERS,
                timeout=30
            ).text

            return text[:max_chars]

    return ""


def get_stock_info(ticker: str) -> dict:
    """
    Get financial metrics via yfinance.
    """
    try:
        info = yf.Ticker(ticker).info
        return {
            "market_cap": str(info.get("marketCap", "N/A")),
            "pe_ratio": str(info.get("trailingPE", "N/A")),
            "revenue_growth": str(info.get("revenueGrowth", "N/A")),
            "profit_margins": str(info.get("profitMargins", "N/A")),
        }
    except Exception as e:
        logger.warning(f"yfinance failed: {e}")
        return {}
