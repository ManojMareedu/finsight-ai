# src/evaluation/dataset.py
"""Pinned evaluation corpus and the programmatically labelled golden set.

The benchmark must not move when SEC publishes a new filing, so every company
is pinned to one accession number in ``snapshot.json``. Numeric labels are read
out of that exact filing's XBRL company-facts, so a label is traceable to a
concept plus an accession instead of being written by hand or by a model.
Qualitative items that have no programmatic label live in ``manual_items.json``
and carry ``label_source: "manual"``.

Only the accession list is committed, not the filings: the pinned annual
reports are several MB of cleaned text and are re-fetchable at any time from
the pinned URL, so committing them buys nothing but repo weight.

    python -m src.evaluation.dataset pin      # refresh snapshot.json from EDGAR
    python -m src.evaluation.dataset build    # regenerate golden_dataset.json
    python -m src.evaluation.dataset ingest   # ingest the pinned filings
    python -m src.evaluation.dataset check    # offline integrity check (CI gate)
"""
import argparse
import datetime
import json
import logging
import os
from typing import Any, Optional, cast

import requests

from src.utils.data_fetchers import (
    _clean_filing_text,
    _edgar_headers,
    _fmt_large,
    _throttle_edgar,
    get_company_cik,
)
from src.utils.retry import retry_with_backoff

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HERE = os.path.dirname(__file__)
SNAPSHOT_PATH = os.path.join(HERE, "snapshot.json")
GOLDEN_PATH = os.path.join(HERE, "golden_dataset.json")
MANUAL_PATH = os.path.join(HERE, "manual_items.json")

# Mirrors the default model in src/rag/embeddings.py. Recorded in every report
# so a score can be tied to the embedding that produced it.
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Same cap as production ingestion (src/rag/ingestion.py).
MAX_FILING_CHARS = 150000

# Forms accepted as "the annual report" when pinning. Numeric labels are only
# generated from 10-K, because get_financials_from_edgar filters XBRL facts to
# form == "10-K" and would score every 20-F/40-F label as a miss.
ANNUAL_FORMS = ("10-K", "20-F", "40-F")

# (company name, ticker, sector). The name is what gets stored as the chunk
# `company` metadata, so it is also what the eval passes as the retrieval
# filter. Chosen for spread: 11 sectors, two banks, two REITs, three
# loss-making issuers, small/mid caps, and one foreign private issuer.
COMPANIES: list[tuple[str, str, str]] = [
    ("Apple", "AAPL", "Technology"),
    ("Microsoft", "MSFT", "Technology"),
    ("NVIDIA", "NVDA", "Technology"),
    ("Adobe", "ADBE", "Technology"),
    ("Tesla", "TSLA", "Consumer Discretionary"),
    ("Crocs", "CROX", "Consumer Discretionary"),
    ("Steven Madden", "SHOO", "Consumer Discretionary"),
    ("Rivian Automotive", "RIVN", "Consumer Discretionary"),
    ("Alphabet", "GOOGL", "Communication Services"),
    ("Netflix", "NFLX", "Communication Services"),
    ("JPMorgan Chase", "JPM", "Financials"),
    ("Zions Bancorporation", "ZION", "Financials"),
    ("Realty Income", "O", "Real Estate"),
    ("Prologis", "PLD", "Real Estate"),
    ("Johnson & Johnson", "JNJ", "Health Care"),
    ("Moderna", "MRNA", "Health Care"),
    ("ConocoPhillips", "COP", "Energy"),
    ("Devon Energy", "DVN", "Energy"),
    ("Caterpillar", "CAT", "Industrials"),
    ("Ryder System", "R", "Industrials"),
    ("Coca-Cola", "KO", "Consumer Staples"),
    ("Church & Dwight", "CHD", "Consumer Staples"),
    ("NextEra Energy", "NEE", "Utilities"),
    ("Nucor", "NUE", "Materials"),
    ("Sony Group", "SONY", "Technology"),
]

# concept candidates and XBRL unit per label, in the same priority order
# get_financials_from_edgar uses, so a label is comparable to what production
# actually emits into DueDiligenceReport.financial_snapshot.key_metrics.
_LABEL_CONCEPTS: dict[str, tuple[tuple[str, ...], str]] = {
    "revenue": (
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
        "USD",
    ),
    "net_income": (("NetIncomeLoss",), "USD"),
    "gross_profit": (("GrossProfit",), "USD"),
    "eps": (("EarningsPerShareBasic", "EarningsPerShareDiluted"), "USD/shares"),
    "r_and_d_spend": (("ResearchAndDevelopmentExpense",), "USD"),
    # Balance-sheet concepts: instant facts, no start date. _annual_facts keeps
    # them for that reason, and production reads them the same way.
    "total_assets": (("Assets",), "USD"),
    "liabilities": (("Liabilities",), "USD"),
}

_QUESTIONS: dict[str, tuple[str, str]] = {
    "revenue": (
        "What were {company} total revenues in fiscal {fy}?",
        "{company} reported total revenue of {value} for the fiscal year ended {end}.",
    ),
    "revenue_growth_yoy": (
        "How much did {company} revenue grow year over year in fiscal {fy}?",
        "{company} revenue grew {value} year over year in the fiscal year ended {end}.",
    ),
    "net_income": (
        "What net income did {company} report for fiscal {fy}?",
        "{company} reported net income of {value} for the fiscal year ended {end}.",
    ),
    "gross_margin": (
        "What was {company} gross margin in fiscal {fy}?",
        "{company} reported a gross margin of {value} for the fiscal year ended {end}.",
    ),
    "eps": (
        "What were {company} earnings per share in fiscal {fy}?",
        "{company} reported earnings per share of {value} for the fiscal year ended {end}.",
    ),
    "r_and_d_spend": (
        "How much did {company} spend on research and development in fiscal {fy}?",
        "{company} spent {value} on research and development in the fiscal year ended {end}.",
    ),
    "total_assets": (
        "What were {company} total assets at the end of fiscal {fy}?",
        "{company} reported total assets of {value} as of {end}.",
    ),
    "debt_ratio": (
        "What share of {company} total assets was funded by liabilities at the end of "
        "fiscal {fy}?",
        "{company} reported total liabilities equal to {value} of total assets as of {end}.",
    ),
}


def _edgar_json(url: str) -> dict:
    def _fetch() -> requests.Response:
        r = requests.get(url, headers=_edgar_headers(), timeout=30)
        r.raise_for_status()
        return r

    _throttle_edgar()
    return dict(retry_with_backoff(_fetch).json())


def pin_filings() -> dict:
    """Resolve every company to its most recent annual filing and freeze the
    accession number. Re-run only when the corpus is deliberately rolled
    forward — the point of the file is that it does not change on its own."""
    entries: list[dict[str, Any]] = []
    for name, ticker, sector in COMPANIES:
        cik = get_company_cik(ticker)
        if not cik:
            logger.warning("No CIK for %s (%s)", name, ticker)
            continue
        recent = (
            _edgar_json(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json")
            .get("filings", {})
            .get("recent", {})
        )
        filing = next(
            (
                (form, accession, filed, doc)
                for form, accession, filed, doc in zip(
                    recent.get("form", []),
                    recent.get("accessionNumber", []),
                    recent.get("filingDate", []),
                    recent.get("primaryDocument", []),
                )
                if form in ANNUAL_FORMS
            ),
            None,
        )
        if filing is None:
            logger.warning("No annual filing for %s (%s)", name, ticker)
            continue
        form, accession, filed, doc = filing
        entries.append(
            {
                "company": name,
                "ticker": ticker,
                "sector": sector,
                "cik": cik,
                "form": form,
                "accession": accession,
                "filing_date": filed,
                "url": (
                    "https://www.sec.gov/Archives/edgar/data/"
                    f"{int(cik)}/{accession.replace('-', '')}/{doc}"
                ),
            }
        )
        logger.info("Pinned %s %s %s", name, form, accession)
    return {
        "pinned_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "embedding_model": EMBEDDING_MODEL,
        "max_filing_chars": MAX_FILING_CHARS,
        "companies": entries,
    }


def _annual_facts(
    us_gaap: dict, concepts: tuple[str, ...], unit: str, accession: str
) -> list[dict]:
    """Full-year facts reported *in the pinned filing*, newest period first.

    Same period rule as get_financials_from_edgar (a full year between start
    and end, or no start at all for the instant balance-sheet concepts) so a
    label is comparable to the production value, but restricted to one
    accession so it cannot drift to a later filing.
    """
    facts: list[dict] = []
    for concept in concepts:
        for entry in us_gaap.get(concept, {}).get("units", {}).get(unit, []):
            if entry.get("accn") != accession or entry.get("val") is None:
                continue
            start, end = entry.get("start"), entry.get("end")
            if not end:
                continue
            if start:
                try:
                    days = (
                        datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)
                    ).days
                except ValueError:
                    continue
                if days < 300:
                    continue
            facts.append({**entry, "concept": concept})

    facts.sort(key=lambda e: (e["end"], e.get("filed", "")), reverse=True)
    deduped: list[dict] = []
    seen: set[str] = set()
    for fact in facts:
        if fact["end"] in seen:
            continue
        seen.add(fact["end"])
        deduped.append(fact)
    return deduped


def _label(fact: dict, value: str, raw: float) -> dict:
    # `raw` is the figure before formatting. The product eval compares against
    # it rather than against `value`, so a bug in the formatter both sides
    # share cannot make the label and the report agree on the same wrong string.
    return {
        "value": value,
        "raw": raw,
        "concept": fact["concept"],
        "period_end": fact["end"],
        "filed": fact.get("filed", ""),
    }


def build_labels(us_gaap: dict, accession: str) -> dict[str, dict]:
    """Production-formatted key metrics for one pinned filing. A metric the
    filing does not report is simply absent — never guessed."""
    picked = {
        metric: _annual_facts(us_gaap, concepts, unit, accession)
        for metric, (concepts, unit) in _LABEL_CONCEPTS.items()
    }
    labels: dict[str, dict] = {}

    revenue = picked["revenue"]
    if revenue:
        current = float(revenue[0]["val"])
        labels["revenue"] = _label(revenue[0], _fmt_large(current), current)
        if len(revenue) > 1 and float(revenue[1]["val"]) != 0:
            previous = float(revenue[1]["val"])
            growth = (current - previous) / previous * 100
            labels["revenue_growth_yoy"] = _label(revenue[0], f"{growth:.1f}%", growth)

    net_income = picked["net_income"]
    if net_income:
        value = float(net_income[0]["val"])
        # Production drops net income above revenue as a period mismatch.
        if not revenue or value <= float(revenue[0]["val"]):
            labels["net_income"] = _label(net_income[0], _fmt_large(value), value)

    gross_profit = picked["gross_profit"]
    if gross_profit and revenue and gross_profit[0]["end"] == revenue[0]["end"]:
        margin = float(gross_profit[0]["val"]) / float(revenue[0]["val"]) * 100
        # Below 2% means GrossProfit excludes major cost items; above 100% is a
        # period mismatch. Production drops both, so neither gets a label.
        if 2.0 <= margin <= 100.0:
            labels["gross_margin"] = _label(gross_profit[0], f"{margin:.1f}%", margin)

    eps = picked["eps"]
    if eps:
        per_share = float(eps[0]["val"])
        labels["eps"] = _label(eps[0], f"${per_share:.2f}", per_share)

    rd = picked["r_and_d_spend"]
    if rd:
        spend = float(rd[0]["val"])
        labels["r_and_d_spend"] = _label(rd[0], _fmt_large(spend), spend)

    assets = picked["total_assets"]
    liabilities = picked["liabilities"]
    if assets:
        total = float(assets[0]["val"])
        labels["total_assets"] = _label(assets[0], _fmt_large(total), total)
        # Production divides the two most recent values without checking that
        # they share a balance-sheet date; the label only exists when they do,
        # so a mismatched pair is scored as a miss rather than blessed.
        if liabilities and liabilities[0]["end"] == assets[0]["end"]:
            ratio = float(liabilities[0]["val"]) / total * 100
            labels["debt_ratio"] = _label(liabilities[0], f"{ratio:.1f}%", ratio)

    return labels


def _item(entry: dict, metric: str, label: dict) -> Optional[dict]:
    """One golden item, or None when the label is not verifiable. Every emitted
    item carries the accession and XBRL concept it came from."""
    if not label.get("value") or not label.get("concept") or not entry.get("accession"):
        return None
    fiscal_year = label["period_end"][:4]
    question, answer = _QUESTIONS[metric]
    fields = {"company": entry["company"], "fy": fiscal_year, "value": label["value"]}
    return {
        "question": question.format(**fields),
        "ground_truth": answer.format(**fields, end=label["period_end"]),
        "company": entry["company"],
        "ticker": entry["ticker"],
        "sector": entry["sector"],
        "metric": metric,
        "value": label["value"],
        "raw_value": label.get("raw"),
        "concept": label["concept"],
        "period_end": label["period_end"],
        "fiscal_year": fiscal_year,
        "accession": entry["accession"],
        "form": entry["form"],
        "section": "Financial Statements (XBRL company facts)",
        "label_source": "xbrl",
        "context_source": f"{entry['ticker'].lower()}_{fiscal_year}",
    }


def _manual_items(snapshot: dict) -> list[dict]:
    """Hand-written qualitative items, stamped with the pinned accession of the
    company they refer to. An item naming a company that is not pinned is
    dropped rather than shipped untraceable."""
    if not os.path.exists(MANUAL_PATH):
        return []
    with open(MANUAL_PATH) as f:
        manual = json.load(f)
    by_company = {e["company"]: e for e in snapshot["companies"]}
    items = []
    for raw in manual:
        entry = by_company.get(raw["company"])
        if entry is None:
            logger.warning("Manual item for unpinned company %s — dropped", raw["company"])
            continue
        items.append(
            {
                **raw,
                "ticker": entry["ticker"],
                "sector": entry["sector"],
                "accession": entry["accession"],
                "form": entry["form"],
                "label_source": "manual",
                "context_source": f"{entry['ticker'].lower()}_{entry['filing_date'][:4]}",
            }
        )
    return items


def build_golden(snapshot: dict) -> list[dict]:
    items: list[dict] = []
    for entry in snapshot["companies"]:
        if entry["form"] != "10-K":
            # get_financials_from_edgar only reads form == "10-K" facts, so a
            # 20-F/40-F filer contributes filing text to the corpus but no
            # numeric labels — labelling it would score production's blind
            # spot as a hallucination.
            logger.info("Skipping numeric labels for %s (%s)", entry["company"], entry["form"])
            continue
        facts = _edgar_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{entry['cik'].zfill(10)}.json"
        )
        labels = build_labels(facts.get("facts", {}).get("us-gaap", {}), entry["accession"])
        emitted = [
            item
            for item in (_item(entry, m, lb) for m, lb in labels.items() if m in _QUESTIONS)
            if item
        ]
        logger.info("%s: %d labelled items", entry["company"], len(emitted))
        items.extend(emitted)
    return items + _manual_items(snapshot)


def ingest_snapshot(chroma_dir: str) -> int:
    """Ingest the pinned filings with production's chunking so retrieval
    measures the shipped configuration. Kept here rather than in
    src/rag/ingestion.py because that path always fetches the latest filing."""
    import chromadb

    from src.rag.embeddings import get_embeddings
    from src.rag.ingestion import clean_sec_text, splitter

    snapshot = load_snapshot()
    client = chromadb.PersistentClient(path=chroma_dir)
    collection = client.get_or_create_collection(
        name="financial_filings", metadata={"hnsw:space": "cosine"}
    )
    embeddings_model = get_embeddings()

    total = 0
    for entry in snapshot["companies"]:
        _throttle_edgar()

        def _fetch() -> requests.Response:
            r = requests.get(entry["url"], headers=_edgar_headers(), timeout=60)
            r.raise_for_status()
            return r

        try:
            raw = retry_with_backoff(_fetch).text
        except Exception as e:  # noqa: BLE001 - one bad filing must not stop the corpus
            logger.warning("Fetch failed for %s: %s", entry["company"], e)
            continue

        text = clean_sec_text(_clean_filing_text(raw)[:MAX_FILING_CHARS])
        chunks = splitter.create_documents(
            texts=[text],
            metadatas=[
                {
                    "company": entry["company"],
                    "ticker": entry["ticker"],
                    "source": "SEC_10K",
                    "cik": entry["cik"],
                    "accession": entry["accession"],
                }
            ],
        )
        texts = [c.page_content for c in chunks]
        collection.upsert(
            ids=[f"{entry['cik']}_{i}" for i in range(len(texts))],
            embeddings=cast(Any, embeddings_model.embed_documents(texts)),
            metadatas=cast(Any, [c.metadata for c in chunks]),
            documents=texts,
        )
        total += len(texts)
        logger.info("Ingested %d chunks for %s", len(texts), entry["company"])
    return total


def load_snapshot() -> dict:
    with open(SNAPSHOT_PATH) as f:
        return dict(json.load(f))


def load_golden() -> list[dict]:
    with open(GOLDEN_PATH) as f:
        return list(json.load(f))


def check(golden: list[dict], snapshot: dict) -> list[str]:
    """Offline integrity check. Returns the list of problems found so CI can
    fail on an untraceable or unlabelled dataset instead of scoring it."""
    problems: list[str] = []
    pinned = {e["company"]: e["accession"] for e in snapshot["companies"]}

    for i, item in enumerate(golden):
        where = f"item {i} ({item.get('question', '?')[:50]})"
        for field in ("question", "ground_truth", "company", "accession", "label_source"):
            if not item.get(field):
                problems.append(f"{where}: missing {field}")
        if item.get("company") not in pinned:
            problems.append(f"{where}: company not pinned in snapshot")
        elif item["accession"] != pinned[item["company"]]:
            problems.append(f"{where}: accession does not match the pinned filing")
        if item.get("label_source") == "xbrl":
            if not item.get("concept") or not item.get("value"):
                problems.append(f"{where}: XBRL item without a concept or value")
            elif item["value"] not in item["ground_truth"]:
                problems.append(f"{where}: ground truth does not contain the labelled value")
            if item.get("raw_value") is None:
                problems.append(f"{where}: XBRL item without the raw value the eval compares")
        elif item.get("label_source") == "manual" and not item.get("section"):
            problems.append(f"{where}: manual item without a section reference")

    companies = {item["company"] for item in golden}
    sectors = {item.get("sector") for item in golden}
    if len(golden) < 100:
        problems.append(f"dataset has {len(golden)} items, expected at least 100")
    if len(companies) < 20:
        problems.append(f"dataset covers {len(companies)} companies, expected at least 20")
    if len(sectors) < 6:
        problems.append(f"dataset covers {len(sectors)} sectors, expected at least 6")
    return problems


def _write(path: str, payload: Any) -> None:
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    logger.info("Wrote %s", path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["pin", "build", "ingest", "check"])
    args = parser.parse_args()

    if args.command == "pin":
        _write(SNAPSHOT_PATH, pin_filings())
        return 0
    if args.command == "build":
        _write(GOLDEN_PATH, build_golden(load_snapshot()))
        return 0
    if args.command == "ingest":
        from src.utils.config import get_settings

        print(f"Ingested {ingest_snapshot(get_settings().chroma_persist_dir)} chunks")
        return 0

    problems = check(load_golden(), load_snapshot())
    for problem in problems:
        print(f"FAIL {problem}")
    print("dataset check: " + ("FAILED" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
