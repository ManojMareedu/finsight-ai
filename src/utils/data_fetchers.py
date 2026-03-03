
import logging
import re
from typing import Optional

import requests

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EDGAR_HEADERS = {
    "User-Agent": "FinSightAI manoj.mareedu.pro@gmail.com"
}

# Fallback map for common companies where name-to-ticker is ambiguous
KNOWN_TICKERS: dict[str, str] = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "tesla": "TSLA",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "nvidia": "NVDA",
    "netflix": "NFLX",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "berkshire": "BRK-B",
    "johnson": "JNJ",
    "walmart": "WMT",
    "visa": "V",
    "mastercard": "MA",
    "salesforce": "CRM",
    "adobe": "ADBE",
    "intel": "INTC",
    "amd": "AMD",
    "qualcomm": "QCOM",
    "broadcom": "AVGO",
    "oracle": "ORCL",
    "ibm": "IBM",
    "spotify": "SPOT",
    "uber": "UBER",
    "airbnb": "ABNB",
    "palantir": "PLTR",
    "coinbase": "COIN",
}


def resolve_ticker(company_name: str, provided_ticker: str = "") -> str:
    """
    Returns the best ticker guess for a company name.
    Priority: provided_ticker > known map > first 4 chars of name (last resort).
    """
    if provided_ticker and provided_ticker.strip():
        return provided_ticker.strip().upper()
    name_lower = company_name.lower().strip()
    if name_lower in KNOWN_TICKERS:
        return KNOWN_TICKERS[name_lower]
    # Try partial match (e.g. "Apple Inc" -> "apple")
    for key, ticker in KNOWN_TICKERS.items():
        if key in name_lower:
            return ticker
    # Last resort: first 4 chars uppercased
    return re.sub(r"[^A-Z]", "", company_name.upper())[:4]


def get_company_cik(company_name: str) -> Optional[str]:
    """
    Use EDGAR's company_tickers.json for reliable CIK lookup.
    No scraping - official SEC endpoint, updated daily.
    """
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=EDGAR_HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        tickers = resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch EDGAR company list: {e}")
        return None

    name_lower = company_name.lower().strip()

    # Pass 1: exact ticker match (e.g. user typed "TSLA")
    for entry in tickers.values():
        if name_lower == entry["ticker"].lower():
            return str(entry["cik_str"])

    # Pass 2: company name contains the search term
    for entry in tickers.values():
        if name_lower in entry["title"].lower():
            return str(entry["cik_str"])

    # Pass 3: search term contains a word from the company name (looser)
    name_words = [w for w in name_lower.split() if len(w) > 3]
    for entry in tickers.values():
        title_lower = entry["title"].lower()
        if any(word in title_lower for word in name_words):
            return str(entry["cik_str"])

    logger.warning(f"No CIK found for: {company_name}")
    return None


def _clean_filing_text(raw: str) -> str:
    """
    Strip HTML/XBRL tags from SEC filings.
    10-K primary documents are usually .htm files with heavy markup.
    Without this, chunks fed to the LLM are unreadable.
    """
    # Remove XBRL inline tags entirely
    raw = re.sub(r"<ix:[^>]+>", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"</ix:[^>]+>", "", raw, flags=re.IGNORECASE)

    # Parse with BeautifulSoup to strip remaining HTML
    soup = BeautifulSoup(raw, "html.parser")

    # Remove script, style, and hidden elements
    for tag in soup(["script", "style", "head", "meta", "link"]):
        tag.decompose()

    text = soup.get_text(separator="\n")

    # Collapse excessive whitespace
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if len(line) > 20]  # drop junk short lines
    text = "\n".join(lines)

    return text


def get_latest_10k_text(cik: str, max_chars: int = 50000) -> str:
    """
    Download and clean the most recent 10-K filing from SEC EDGAR.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"

    try:
        data = requests.get(url, headers=EDGAR_HEADERS, timeout=15).json()
    except Exception as e:
        logger.error(f"Failed to fetch submissions for CIK {cik}: {e}")
        return ""

    filings = data.get("filings", {}).get("recent", {})

    forms = filings.get("form", [])
    accnums = filings.get("accessionNumber", [])
    primary_docs = filings.get("primaryDocument", [])

    for form, accnum, primary_doc in zip(forms, accnums, primary_docs):
        if form != "10-K":
            continue

        acc_no_dash = accnum.replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{acc_no_dash}/{primary_doc}"
        )

        try:
            resp = requests.get(filing_url, headers=EDGAR_HEADERS, timeout=30)
            resp.raise_for_status()
            raw = resp.text
        except Exception as e:
            logger.warning(f"Failed to download filing {filing_url}: {e}")
            return ""

        cleaned = _clean_filing_text(raw)
        logger.info(
            f"10-K fetched for CIK {cik}: "
            f"{len(raw):,} raw chars -> {len(cleaned):,} cleaned chars"
        )
        return cleaned[:max_chars]

    logger.warning(f"No 10-K filing found for CIK {cik}")
    return ""


def get_stock_info(ticker: str) -> dict:
    """
    Get financial metrics from SEC EDGAR company facts API.
    Same API already used for filings — no rate limits, no API key, official data.
    Falls back to empty dict gracefully if CIK lookup fails.
    """
    cik = get_company_cik(ticker)  # try ticker as search term first
    if not cik:
        logger.warning(f"Could not resolve CIK for {ticker} — skipping financials")
        return {}
    return get_financials_from_edgar(cik)

def get_financials_from_edgar(cik: str) -> dict:
    """
    Pull key financial metrics from SEC EDGAR XBRL company facts.
    Endpoint: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json
    Returns the most recent reported value for each metric.
    """
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"

    try:
        resp = requests.get(url, headers=EDGAR_HEADERS, timeout=15)
        resp.raise_for_status()
        facts = resp.json()
    except Exception as e:
        logger.warning(f"EDGAR company facts fetch failed for CIK {cik}: {e}")
        return {}

    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    result = {}

    # Each helper pulls the most recent annual (10-K) value for a GAAP concept
    revenue = _latest_annual(us_gaap, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ])

    if revenue:
        result["revenue"] = _fmt_large(revenue)
    
    revenue_growth = _revenue_growth(us_gaap, [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    ])

    if revenue_growth is not None:
        result["revenue_growth_yoy"] = f"{revenue_growth:.1f}%"


    net_income = _latest_annual(us_gaap, ["NetIncomeLoss"])
    if net_income:
        result["net_income"] = _fmt_large(net_income)

    gross_profit = _latest_annual(us_gaap, ["GrossProfit"])
    if gross_profit and revenue:
        margin = (gross_profit / revenue) * 100
        # Sanity check: margin under 2% means EDGAR GrossProfit
        # excludes major cost items for this company (common with
        # retailers like Amazon who bury fulfillment in COGS).
        # Drop it rather than show a misleading number.
        if margin >= 2.0:
            result["gross_margin"] = f"{margin:.1f}%"
        else:
            logger.warning(
                f"Gross margin {margin:.1f}% < 2% for CIK {cik} — "
                f"likely incomplete GAAP GrossProfit, dropping"
    )

    eps = _latest_annual(us_gaap, [
        "EarningsPerShareBasic",
        "EarningsPerShareDiluted",
    ])
    if eps:
        result["eps"] = f"${eps:.2f}"

    assets = _latest_annual(us_gaap, ["Assets"])
    if assets:
        result["total_assets"] = _fmt_large(assets)

    liabilities = _latest_annual(us_gaap, ["Liabilities"])
    if liabilities and assets:
        debt_ratio = (liabilities / assets) * 100
        result["debt_ratio"] = f"{debt_ratio:.1f}%"

    rd = _latest_annual(us_gaap, ["ResearchAndDevelopmentExpense"])
    if rd:
        result["r_and_d_spend"] = _fmt_large(rd)

    if "revenue" in result and "net_income" in result:
        try:
            rev = _parse_fmt_large(result["revenue"])
            ni = _parse_fmt_large(result["net_income"])
            if rev and ni and ni > rev:
                logger.warning(
                    f"Sanity check failed for CIK {cik}: "
                    f"net_income {result['net_income']} > revenue {result['revenue']}. "
                    f"Dropping unreliable derived metrics."
                )
                result.pop("net_income", None)
                result.pop("gross_margin", None)
        except Exception:
            pass

    # Sanity check: gross margin over 100% is only valid for software/services companies
    # Values like 570% always indicate a period mismatch
    if "gross_margin" in result:
        try:
            gm = float(result["gross_margin"].replace("%", ""))
            if gm > 100:
                logger.warning(
                    f"Gross margin {result['gross_margin']} > 100% for CIK {cik} — dropping"
                )
                result.pop("gross_margin", None)
        except Exception:
            pass

    logger.info(f"EDGAR financials for CIK {cik}: {result}")
    return result


def _latest_annual(us_gaap: dict, concept_names: list) -> Optional[float]:
    """
    Try all concept names, return the value with the most recent fiscal year end.
    Previously returned on the first concept with any data — this caused stale
    values when a company switches GAAP concepts between filing years (e.g.
    Nvidia moved from RevenueFromContract... to Revenues after FY2022).
    """
    import datetime

    best_entry: Optional[dict] = None
    best_end: str = ""

    for concept in concept_names:
        data = us_gaap.get(concept, {})
        units = data.get("units", {})
        values = units.get("USD") or units.get("USD/shares") or []

        annual = [
            v for v in values
            if v.get("form") == "10-K" and v.get("val") is not None
        ]
        if not annual:
            continue

        # Deduplicate by fiscal year end, keep latest filing per period
        by_period: dict[str, dict] = {}
        for entry in annual:
            end = entry.get("end", "")
            filed = entry.get("filed", "")
            if not end:
                continue
            if end not in by_period or filed > by_period[end].get("filed", ""):
                by_period[end] = entry

        # Filter to confirmed full-year periods (300+ days)
        for entry in by_period.values():
            start = entry.get("start", "")
            end = entry.get("end", "")
            if not start or not end:
                continue
            try:
                days = (
                    datetime.date.fromisoformat(end) -
                    datetime.date.fromisoformat(start)
                ).days
                if days < 300:
                    continue
            except Exception:
                continue

            # Keep track of the entry with the most recent fiscal year end
            if end > best_end:
                best_end = end
                best_entry = entry

    if best_entry:
        return float(best_entry["val"])
    return None


def _fmt_large(value: float) -> str:
    """Format large dollar amounts into readable strings."""
    if value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.0f}"

def _parse_fmt_large(value_str: str) -> Optional[float]:
    """Parse a _fmt_large formatted string back to float for sanity checks."""
    try:
        clean = value_str.replace("$", "").replace(",", "").strip()
        if clean.endswith("T"):
            return float(clean[:-1]) * 1_000_000_000_000
        if clean.endswith("B"):
            return float(clean[:-1]) * 1_000_000_000
        if clean.endswith("M"):
            return float(clean[:-1]) * 1_000_000
        return float(clean)
    except Exception:
        return None

def _revenue_growth(us_gaap: dict, concept_names: list) -> Optional[float]:
    """
    Calculate YoY revenue growth using the two most recent full-year values
    across all concept names. Handles companies that switch GAAP concepts.
    """
    import datetime

    all_full_year: list[dict] = []

    for concept in concept_names:
        data = us_gaap.get(concept, {})
        values = data.get("units", {}).get("USD", [])

        annual = [
            v for v in values
            if v.get("form") == "10-K" and v.get("val") is not None
        ]
        if not annual:
            continue

        # Deduplicate by period end
        by_period: dict[str, dict] = {}
        for entry in annual:
            end = entry.get("end", "")
            filed = entry.get("filed", "")
            if not end:
                continue
            if end not in by_period or filed > by_period[end].get("filed", ""):
                by_period[end] = entry

        # Filter to full-year periods
        for entry in by_period.values():
            start = entry.get("start", "")
            end = entry.get("end", "")
            if not start or not end:
                continue
            try:
                days = (
                    datetime.date.fromisoformat(end) -
                    datetime.date.fromisoformat(start)
                ).days
                if days >= 300:
                    all_full_year.append(entry)
            except Exception:
                continue

    if len(all_full_year) < 2:
        return None

    # Deduplicate across concepts by period end date
    by_period_final: dict[str, dict] = {}
    for entry in all_full_year:
        end = entry.get("end", "")
        filed = entry.get("filed", "")
        if end not in by_period_final or filed > by_period_final[end].get("filed", ""):
            by_period_final[end] = entry

    sorted_entries = sorted(
        by_period_final.values(),
        key=lambda x: x.get("end", ""),
        reverse=True
    )

    if len(sorted_entries) < 2:
        return None

    current = float(sorted_entries[0]["val"])
    previous = float(sorted_entries[1]["val"])
    if previous == 0:
        return None
    return ((current - previous) / previous) * 100

def get_latest_10k_date(cik: str) -> Optional[str]:
    """
    Returns the filing date of the most recent 10-K for a company.
    Used to stamp reports with the actual data date, not today's date.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    try:
        data = requests.get(url, headers=EDGAR_HEADERS, timeout=15).json()
        filings = data.get("filings", {}).get("recent", {})
        for form, date in zip(
            filings.get("form", []),
            filings.get("filingDate", [])
        ):
            if form == "10-K":
                return date  # e.g. "2024-07-30"
    except Exception as e:
        logger.warning(f"Could not fetch 10-K date for CIK {cik}: {e}")
    return None