import logging
import re
import time
from functools import lru_cache
from typing import Iterator, Optional

import requests
from bs4 import BeautifulSoup

from src.utils.config import get_settings
from src.utils.retry import retry_with_backoff

logger = logging.getLogger(__name__)


def _edgar_headers() -> dict:
    return {"User-Agent": get_settings().sec_edgar_user_agent}


# This is a per-process floor, not a cluster-wide limiter — every fork/worker
# gets its own 100ms clock. Fine for a single container; would need a shared
# token bucket if this ever runs as more than one process against SEC's 10
# req/s policy.
_EDGAR_MIN_INTERVAL = 0.1
_last_edgar_call = 0.0


def _throttle_edgar() -> None:
    global _last_edgar_call
    elapsed = time.monotonic() - _last_edgar_call
    if elapsed < _EDGAR_MIN_INTERVAL:
        time.sleep(_EDGAR_MIN_INTERVAL - elapsed)
    _last_edgar_call = time.monotonic()


# Common legal suffixes stripped before matching a company name against
# KNOWN_TICKERS, so "Apple Inc" still matches "apple" without letting
# substring matching hit unrelated companies (see resolve_ticker).
_LEGAL_SUFFIX_RE = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|ltd|llc|plc)\b\.?")


class CompanyNotResolvedError(Exception):
    """Raised when a company name cannot be confidently resolved to a ticker
    or SEC CIK. Callers must fail closed (e.g. HTTP 422) instead of guessing —
    a wrong guess silently analyzes the wrong company."""


# Fallback map for common companies where name-to-ticker is ambiguous.
#
# T0-4: this duplicates data that _get_edgar_tickers() also has, but it isn't
# dead weight — it's the only ticker lookup that works before any network call
# (or SEC list cache) exists, so research_agent/filing_agent get a real ticker
# for logging/metadata on the very first request instead of "" until the
# cache warms. Deleting it means resolve_ticker degrades to "provided_ticker
# or nothing" and every offline/pure-function test for it goes with it — kept
# for now; get_company_cik remains the sole source of truth for the CIK either
# way, so this map cannot cause a wrong-company analysis.
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


def resolve_ticker(company_name: str, provided_ticker: str = "") -> Optional[str]:
    """
    Returns the best ticker guess for a company name, or None if it cannot be
    resolved with confidence. Priority: provided_ticker > exact match against
    KNOWN_TICKERS (after stripping legal suffixes like "Inc"/"Corp").

    No more substring guessing and no more "first 4 letters" fallback — those
    silently matched the wrong company (e.g. "Intelsat" -> INTC via substring
    "intel", "Zeta Corp" -> "ZETA" via truncation). A caller getting None back
    must resolve via the authoritative SEC CIK lookup or fail closed.
    """
    if provided_ticker and provided_ticker.strip():
        return provided_ticker.strip().upper()
    name_lower = company_name.lower().strip()
    if name_lower in KNOWN_TICKERS:
        return KNOWN_TICKERS[name_lower]
    stripped = _LEGAL_SUFFIX_RE.sub("", name_lower).strip()
    if stripped in KNOWN_TICKERS:
        return KNOWN_TICKERS[stripped]
    return None


def get_company_cik(company_name: str) -> Optional[str]:
    """
    Use EDGAR's company_tickers.json for reliable CIK lookup.
    No scraping - official SEC endpoint, updated daily.

    The ticker list is fetched once per process (see _get_edgar_tickers) rather
    than re-downloaded on every call — get_company_cik runs several times per
    analysis (research, filing ingestion, synthesis).
    """
    try:
        tickers = _get_edgar_tickers()
    except Exception as e:
        logger.error(f"Failed to fetch EDGAR company list: {e}")
        return None

    name_lower = company_name.lower().strip()

    # Pass 1: exact ticker match (e.g. user typed "TSLA")
    for ticker, _title, cik in tickers:
        if name_lower == ticker.lower():
            return cik

    # Pass 2: company name contains the search term
    for _ticker, title, cik in tickers:
        if name_lower in title.lower():
            return cik

    # Pass 3: a whole word from the company name matches a whole word in the
    # SEC title (tightened from substring matching, which let "Intelsat"
    # match "Intel" and "American Airlines" match "American Express").
    name_words = [w for w in name_lower.split() if len(w) > 3]
    for _ticker, title, cik in tickers:
        title_words = set(title.lower().split())
        if any(word in title_words for word in name_words):
            return cik

    logger.warning(f"No CIK found for: {company_name}")
    return None


@lru_cache(maxsize=1)
def _get_edgar_tickers() -> tuple[tuple[str, str, str], ...]:
    """
    Fetch and cache SEC company_tickers.json once per process.

    Returns an immutable tuple of (ticker, title, cik_str) in the SEC's original
    order so the three-pass search in get_company_cik behaves identically. On a
    fetch error the exception propagates (and is NOT cached), so the next call
    retries — the caller downgrades it to a None CIK.
    """

    def _fetch():
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_edgar_headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r

    _throttle_edgar()
    resp = retry_with_backoff(_fetch)
    data = resp.json()
    return tuple((e["ticker"], e["title"], str(e["cik_str"])) for e in data.values())


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


def _submission_pages(data: dict) -> Iterator[dict]:
    """The recent-submissions block first, then the older pages behind it.

    ``filings.recent`` holds only the newest 1000 submissions. A heavy filer
    (JPMorgan has filed ~26,000; its recent window reaches back barely a year)
    can push its own 10-K out of that block, at which point the report loses
    both its filing text and its data date with no error anywhere. The older
    pages are fetched lazily, so a company whose 10-K is in the recent window
    still costs exactly one request.
    """
    filings = data.get("filings", {})
    yield filings.get("recent", {})
    for page in filings.get("files", []):
        name = page.get("name", "")
        if not name:
            continue

        def _fetch(name: str = name):
            r = requests.get(
                f"https://data.sec.gov/submissions/{name}", headers=_edgar_headers(), timeout=15
            )
            r.raise_for_status()
            return r

        try:
            _throttle_edgar()
            yield dict(retry_with_backoff(_fetch).json())
        except Exception as e:
            logger.warning(f"Could not fetch older submissions page {name}: {e}")


def _latest_10k(data: dict) -> Optional[tuple[str, str, str]]:
    """(accession, primary document, filing date) of the newest 10-K, or None."""
    for page in _submission_pages(data):
        for form, accnum, filed, doc in zip(
            page.get("form", []),
            page.get("accessionNumber", []),
            page.get("filingDate", []),
            page.get("primaryDocument", []),
        ):
            if form == "10-K":
                return accnum, doc, filed
    return None


def _fetch_submissions_json(cik: str) -> Optional[dict]:
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"

    def _fetch():
        r = requests.get(url, headers=_edgar_headers(), timeout=15)
        r.raise_for_status()
        return r

    try:
        _throttle_edgar()
        return dict(retry_with_backoff(_fetch).json())
    except Exception as e:
        logger.error(f"Failed to fetch submissions for CIK {cik}: {e}")
        return None


def get_latest_10k_text(cik: str, max_chars: int = 50000) -> str:
    """
    Download and clean the most recent 10-K filing from SEC EDGAR.
    """
    data = _fetch_submissions_json(cik)
    if data is None:
        return ""

    latest = _latest_10k(data)
    if latest is not None:
        accnum, primary_doc, _ = latest

        acc_no_dash = accnum.replace("-", "")
        filing_url = (
            f"https://www.sec.gov/Archives/edgar/data/" f"{int(cik)}/{acc_no_dash}/{primary_doc}"
        )

        def _fetch_filing():
            r = requests.get(filing_url, headers=_edgar_headers(), timeout=30)
            r.raise_for_status()
            return r

        try:
            _throttle_edgar()
            resp = retry_with_backoff(_fetch_filing)
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
    return ""


def get_financials_from_edgar(cik: str) -> dict:
    """
    Pull key financial metrics from SEC EDGAR XBRL company facts.
    Endpoint: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json
    Returns the most recent reported value for each metric.
    """
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json"

    def _fetch():
        r = requests.get(url, headers=_edgar_headers(), timeout=15)
        r.raise_for_status()
        return r

    try:
        _throttle_edgar()
        facts = retry_with_backoff(_fetch).json()
    except Exception as e:
        logger.warning(f"EDGAR company facts fetch failed for CIK {cik}: {e}")
        return {}

    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    result = {}

    # Each helper pulls the most recent annual (10-K) value for a GAAP concept
    revenue = _latest_annual(
        us_gaap,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ],
    )

    if revenue:
        result["revenue"] = _fmt_large(revenue)

    revenue_growth = _revenue_growth(
        us_gaap,
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ],
    )

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

    eps = _latest_annual(
        us_gaap,
        [
            "EarningsPerShareBasic",
            "EarningsPerShareDiluted",
        ],
    )
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

        annual = [v for v in values if v.get("form") == "10-K" and v.get("val") is not None]
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

        # Keep confirmed full-year periods (300+ days). Balance-sheet concepts
        # (Assets, Liabilities) are instant facts and carry no start date at
        # all — they are point-in-time by definition, so the duration check
        # cannot apply to them. Requiring a start silently dropped every one of
        # them, which is why total_assets and debt_ratio never reached a report.
        for entry in by_period.values():
            start = entry.get("start", "")
            end = entry.get("end", "")
            if not end:
                continue
            if start:
                try:
                    days = (
                        datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)
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
    # Scale on magnitude, not the signed value, so a net loss gets the same
    # T/B/M treatment as a profit instead of falling through every threshold
    # (all of them are false for a negative number) into an unscaled figure.
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    if magnitude >= 1_000_000_000_000:
        return f"{sign}${magnitude / 1_000_000_000_000:.2f}T"
    if magnitude >= 1_000_000_000:
        return f"{sign}${magnitude / 1_000_000_000:.2f}B"
    if magnitude >= 1_000_000:
        return f"{sign}${magnitude / 1_000_000:.2f}M"
    return f"{sign}${magnitude:,.0f}"


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
    Calculate YoY revenue growth from the two most recent full-year values
    within a single GAAP concept. Never diffs across concepts: pooling
    RevenueFromContractWithCustomerExcludingAssessedTax against Revenues (the
    Nvidia case _latest_annual's docstring calls out) compares two different
    measures and produces a confidently wrong number. Each concept is checked
    independently and the candidate with the most recent qualifying pair wins.
    """
    import datetime

    best_growth: Optional[float] = None
    best_end: str = ""

    for concept in concept_names:
        data = us_gaap.get(concept, {})
        values = data.get("units", {}).get("USD", [])

        annual = [v for v in values if v.get("form") == "10-K" and v.get("val") is not None]
        if not annual:
            continue

        # Deduplicate by period end, keep latest filing per period
        by_period: dict[str, dict] = {}
        for entry in annual:
            end = entry.get("end", "")
            filed = entry.get("filed", "")
            if not end:
                continue
            if end not in by_period or filed > by_period[end].get("filed", ""):
                by_period[end] = entry

        full_year = []
        for entry in by_period.values():
            start = entry.get("start", "")
            end = entry.get("end", "")
            if not start or not end:
                continue
            try:
                days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
                if days >= 300:
                    full_year.append(entry)
            except Exception:
                continue

        if len(full_year) < 2:
            continue

        full_year.sort(key=lambda x: x["end"], reverse=True)
        current, previous = full_year[0], full_year[1]

        # The two periods must be adjacent fiscal years, not just the two
        # most recent full-year filings on file — a gap (e.g. a restated or
        # skipped year) makes the delta meaningless as a YoY rate.
        try:
            gap_days = (
                datetime.date.fromisoformat(current["end"])
                - datetime.date.fromisoformat(previous["end"])
            ).days
        except Exception:
            continue
        if not (300 <= gap_days <= 430):
            continue

        if previous["val"] == 0:
            continue

        growth = ((float(current["val"]) - float(previous["val"])) / float(previous["val"])) * 100

        if abs(growth) > 300:
            logger.warning(
                f"Revenue growth {growth:.1f}% for concept {concept} exceeds the "
                "plausible bound (>300%) — dropping instead of reporting a likely "
                "bad comparison"
            )
            continue

        if current["end"] > best_end:
            best_end = current["end"]
            best_growth = round(growth, 3)

    return best_growth


def get_latest_10k_date(cik: str) -> Optional[str]:
    """
    Returns the filing date of the most recent 10-K for a company.
    Used to stamp reports with the actual data date, not today's date.
    """
    data = _fetch_submissions_json(cik)
    if data is None:
        return None
    latest = _latest_10k(data)
    return latest[2] if latest else None  # e.g. "2024-07-30"
