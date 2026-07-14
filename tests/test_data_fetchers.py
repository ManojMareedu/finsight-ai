"""Network-free unit tests for src/utils/data_fetchers.py pure logic.

Covers ticker resolution, large-number formatting round-trips, the annual-value
selection that handles GAAP concept switches, YoY growth, and the sanity checks in
get_financials_from_edgar (HTTP mocked, no network)."""

import src.utils.data_fetchers as df
from src.utils.data_fetchers import (
    _fmt_large,
    _latest_annual,
    _parse_fmt_large,
    _revenue_growth,
    resolve_ticker,
)

# --- resolve_ticker -----------------------------------------------------------


def test_resolve_ticker_prefers_provided():
    assert resolve_ticker("Apple", "aapl") == "AAPL"


def test_resolve_ticker_known_map_exact():
    assert resolve_ticker("Apple") == "AAPL"
    assert resolve_ticker("nvidia") == "NVDA"


def test_resolve_ticker_partial_match():
    assert resolve_ticker("Apple Inc") == "AAPL"


def test_resolve_ticker_fallback_strips_non_alpha():
    # Unknown company -> first 4 uppercased alpha chars
    assert resolve_ticker("Zeta Corp") == "ZETA"


# --- _fmt_large / _parse_fmt_large -------------------------------------------


def test_fmt_large_scales():
    assert _fmt_large(1_500_000_000_000) == "$1.50T"
    assert _fmt_large(2_500_000_000) == "$2.50B"
    assert _fmt_large(3_500_000) == "$3.50M"
    assert _fmt_large(500) == "$500"


def test_fmt_parse_round_trip():
    for val in (1_500_000_000_000, 2_500_000_000, 3_500_000):
        assert _parse_fmt_large(_fmt_large(val)) == val


def test_parse_fmt_large_bad_input_returns_none():
    assert _parse_fmt_large("not a number") is None


# --- _latest_annual -----------------------------------------------------------


def _usd_entry(val, start, end, filed, form="10-K"):
    return {"val": val, "start": start, "end": end, "filed": filed, "form": form}


def test_latest_annual_picks_most_recent_full_year():
    us_gaap = {
        "Revenues": {
            "units": {
                "USD": [
                    _usd_entry(100, "2022-01-01", "2022-12-31", "2023-02-01"),
                    _usd_entry(120, "2023-01-01", "2023-12-31", "2024-02-01"),
                    # a quarter (short period) must be ignored
                    _usd_entry(30, "2023-10-01", "2023-12-31", "2024-02-01"),
                ]
            }
        }
    }
    assert _latest_annual(us_gaap, ["Revenues"]) == 120.0


def test_latest_annual_handles_concept_switch():
    # Company reported under an old concept, then switched to a new one with a
    # more recent fiscal year — the newest end must win across concepts.
    us_gaap = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": [_usd_entry(100, "2021-01-01", "2021-12-31", "2022-02-01")]}
        },
        "Revenues": {"units": {"USD": [_usd_entry(150, "2022-01-01", "2022-12-31", "2023-02-01")]}},
    }
    concepts = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]
    assert _latest_annual(us_gaap, concepts) == 150.0


def test_latest_annual_none_when_no_annual_data():
    assert _latest_annual({}, ["Revenues"]) is None


# --- _revenue_growth ----------------------------------------------------------


def test_revenue_growth_computes_yoy():
    us_gaap = {
        "Revenues": {
            "units": {
                "USD": [
                    _usd_entry(100, "2022-01-01", "2022-12-31", "2023-02-01"),
                    _usd_entry(110, "2023-01-01", "2023-12-31", "2024-02-01"),
                ]
            }
        }
    }
    growth = _revenue_growth(us_gaap, ["Revenues"])
    assert growth is not None
    assert round(growth, 1) == 10.0


def test_revenue_growth_none_with_single_year():
    us_gaap = {
        "Revenues": {"units": {"USD": [_usd_entry(100, "2022-01-01", "2022-12-31", "2023-02-01")]}}
    }
    assert _revenue_growth(us_gaap, ["Revenues"]) is None


# --- get_financials_from_edgar sanity checks (HTTP mocked) --------------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _facts(concepts):
    return {"facts": {"us-gaap": concepts}}


def test_edgar_drops_net_income_greater_than_revenue(monkeypatch):
    # net_income > revenue is impossible -> both net_income and gross_margin dropped
    rev = _usd_entry(100, "2023-01-01", "2023-12-31", "2024-02-01")
    ni = _usd_entry(200, "2023-01-01", "2023-12-31", "2024-02-01")
    concepts = {
        "Revenues": {"units": {"USD": [rev]}},
        "NetIncomeLoss": {"units": {"USD": [ni]}},
    }
    monkeypatch.setattr(df.requests, "get", lambda *a, **k: _FakeResp(_facts(concepts)))
    result = df.get_financials_from_edgar("320193")
    assert "revenue" in result
    assert "net_income" not in result


def test_edgar_drops_impossible_gross_margin(monkeypatch):
    # GrossProfit > Revenue -> margin > 100% -> dropped as a period mismatch
    rev = _usd_entry(100, "2023-01-01", "2023-12-31", "2024-02-01")
    gp = _usd_entry(570, "2023-01-01", "2023-12-31", "2024-02-01")
    concepts = {
        "Revenues": {"units": {"USD": [rev]}},
        "GrossProfit": {"units": {"USD": [gp]}},
    }
    monkeypatch.setattr(df.requests, "get", lambda *a, **k: _FakeResp(_facts(concepts)))
    result = df.get_financials_from_edgar("320193")
    assert "gross_margin" not in result


def test_edgar_returns_empty_on_fetch_failure(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(df.requests, "get", _boom)
    assert df.get_financials_from_edgar("320193") == {}


# --- CIK lookup caching (P2-4) -----------------------------------------------


def test_company_tickers_fetched_once_across_lookups(monkeypatch):
    df._get_edgar_tickers.cache_clear()
    calls = {"n": 0}

    payload = {
        "0": {"ticker": "AAPL", "title": "Apple Inc.", "cik_str": 320193},
        "1": {"ticker": "MSFT", "title": "Microsoft Corp", "cik_str": 789019},
    }

    def _counting_get(*a, **k):
        calls["n"] += 1
        return _FakeResp(payload)

    monkeypatch.setattr(df.requests, "get", _counting_get)

    # Three lookups (as happens across research/filing/synthesis) -> one download
    assert df.get_company_cik("AAPL") == "320193"
    assert df.get_company_cik("Microsoft") == "789019"
    assert df.get_company_cik("Apple") == "320193"
    assert calls["n"] == 1

    df._get_edgar_tickers.cache_clear()


def test_cik_fetch_failure_not_cached(monkeypatch):
    df._get_edgar_tickers.cache_clear()

    def _boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(df.requests, "get", _boom)
    assert df.get_company_cik("Apple") is None  # error -> None, not cached

    payload = {"0": {"ticker": "AAPL", "title": "Apple Inc.", "cik_str": 320193}}
    monkeypatch.setattr(df.requests, "get", lambda *a, **k: _FakeResp(payload))
    assert df.get_company_cik("Apple") == "320193"  # retried successfully

    df._get_edgar_tickers.cache_clear()
