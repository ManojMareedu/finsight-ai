"""Offline tests for the golden-set generator and the committed dataset."""

import pytest

import src.evaluation.dataset as ds

ACCESSION = "0000000000-26-000001"

FACTS = {
    "Revenues": {
        "units": {
            "USD": [
                {
                    "val": 1000.0,
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "accn": ACCESSION,
                    "form": "10-K",
                    "filed": "2026-02-01",
                },
                {
                    "val": 800.0,
                    "start": "2024-01-01",
                    "end": "2024-12-31",
                    "accn": ACCESSION,
                    "form": "10-K",
                    "filed": "2026-02-01",
                },
                {
                    "val": 9999.0,
                    "start": "2023-01-01",
                    "end": "2023-12-31",
                    "accn": "0000000000-24-000001",
                    "form": "10-K",
                    "filed": "2024-02-01",
                },
                {
                    "val": 250.0,
                    "start": "2025-10-01",
                    "end": "2025-12-31",
                    "accn": ACCESSION,
                    "form": "10-K",
                    "filed": "2026-02-01",
                },
            ]
        }
    },
    "GrossProfit": {
        "units": {
            "USD": [
                {
                    "val": 400.0,
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "accn": ACCESSION,
                    "form": "10-K",
                }
            ]
        }
    },
    "NetIncomeLoss": {
        "units": {
            "USD": [
                {
                    "val": 100.0,
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "accn": ACCESSION,
                    "form": "10-K",
                }
            ]
        }
    },
    "EarningsPerShareBasic": {
        "units": {
            "USD/shares": [
                {
                    "val": 1.234,
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "accn": ACCESSION,
                    "form": "10-K",
                }
            ]
        }
    },
}

ENTRY = {
    "company": "Testco",
    "ticker": "TCO",
    "sector": "Technology",
    "accession": ACCESSION,
    "form": "10-K",
}


def test_labels_come_only_from_the_pinned_accession():
    labels = ds.build_labels(FACTS, ACCESSION)
    # 9999.0 belongs to an earlier filing and must not leak into the label
    assert labels["revenue"]["value"] == "$1,000"
    assert labels["revenue"]["period_end"] == "2025-12-31"
    assert labels["revenue_growth_yoy"]["value"] == "25.0%"
    assert labels["gross_margin"]["value"] == "40.0%"
    assert labels["eps"]["value"] == "$1.23"


def test_quarterly_periods_are_not_treated_as_full_years():
    facts = ds._annual_facts(FACTS, ("Revenues",), "USD", ACCESSION)
    assert [f["val"] for f in facts] == [1000.0, 800.0]


def test_no_label_when_the_filing_does_not_report_the_concept():
    labels = ds.build_labels({}, ACCESSION)
    assert labels == {}


def test_item_is_traceable_to_an_accession_and_concept():
    labels = ds.build_labels(FACTS, ACCESSION)
    item = ds._item(ENTRY, "revenue", labels["revenue"])
    assert item is not None
    assert item["accession"] == ACCESSION
    assert item["concept"] == "Revenues"
    assert item["label_source"] == "xbrl"
    assert item["value"] in item["ground_truth"]


def test_item_refuses_to_emit_without_a_verifiable_label():
    label = {"value": "", "concept": "Revenues", "period_end": "2025-12-31"}
    assert ds._item(ENTRY, "revenue", label) is None
    assert ds._item(ENTRY, "revenue", {"value": "$1,000", "period_end": "2025-12-31"}) is None
    unpinned = {**ENTRY, "accession": ""}
    assert (
        ds._item(
            unpinned,
            "revenue",
            {"value": "$1,000", "concept": "Revenues", "period_end": "2025-12-31"},
        )
        is None
    )


def test_check_flags_untraceable_and_mislabelled_items():
    snapshot = {"companies": [{"company": "Testco", "accession": ACCESSION}]}
    golden = [
        {
            "question": "q",
            "ground_truth": "Testco reported $1,000",
            "company": "Testco",
            "accession": "0000000000-99-000001",
            "label_source": "xbrl",
            "concept": "Revenues",
            "value": "$1,000",
            "sector": "Technology",
        },
        {
            "question": "q2",
            "ground_truth": "Testco reported something else",
            "company": "Testco",
            "accession": ACCESSION,
            "label_source": "xbrl",
            "concept": "Revenues",
            "value": "$2,000",
            "sector": "Technology",
        },
        {
            "question": "q3",
            "ground_truth": "gt",
            "company": "Unpinned Corp",
            "accession": ACCESSION,
            "label_source": "manual",
            "section": "Item 1A",
            "sector": "Technology",
        },
    ]
    problems = ds.check(golden, snapshot)
    assert any("accession does not match" in p for p in problems)
    assert any("does not contain the labelled value" in p for p in problems)
    assert any("not pinned in snapshot" in p for p in problems)


def test_committed_dataset_is_traceable_and_broad():
    problems = ds.check(ds.load_golden(), ds.load_snapshot())
    assert problems == [], problems


def test_manual_items_are_marked_and_pinned():
    golden = ds.load_golden()
    manual = [i for i in golden if i["label_source"] == "manual"]
    assert manual, "the qualitative items should survive in the set"
    assert all(i.get("section") and i.get("accession") for i in manual)


@pytest.mark.parametrize("form", ["20-F", "40-F"])
def test_non_10k_filers_get_no_numeric_labels(form):
    # get_financials_from_edgar only reads form == "10-K" facts, so labelling a
    # foreign private issuer would score production's blind spot as an error.
    snapshot = {"companies": [{**ENTRY, "form": form, "cik": "1"}]}
    assert ds.build_golden(snapshot) == []


# --- balance-sheet labels ------------------------------------------------------

BALANCE_SHEET = {
    "Assets": {
        "units": {
            "USD": [
                {
                    "val": 2000.0,
                    "end": "2025-12-31",
                    "accn": ACCESSION,
                    "form": "10-K",
                    "filed": "2026-02-01",
                }
            ]
        }
    },
    "Liabilities": {
        "units": {
            "USD": [
                {
                    "val": 1500.0,
                    "end": "2025-12-31",
                    "accn": ACCESSION,
                    "form": "10-K",
                    "filed": "2026-02-01",
                }
            ]
        }
    },
}


def test_instant_balance_sheet_facts_get_labels():
    # Assets/Liabilities carry no start date. Requiring one produced no labels
    # for the two metrics production reads from them.
    labels = ds.build_labels(BALANCE_SHEET, ACCESSION)
    assert labels["total_assets"]["value"] == "$2,000"
    assert labels["debt_ratio"]["value"] == "75.0%"


def test_no_debt_ratio_when_the_two_sides_have_different_dates():
    facts = {
        "Assets": BALANCE_SHEET["Assets"],
        "Liabilities": {
            "units": {
                "USD": [
                    {
                        "val": 1500.0,
                        "end": "2024-12-31",
                        "accn": ACCESSION,
                        "form": "10-K",
                        "filed": "2026-02-01",
                    }
                ]
            }
        },
    }
    labels = ds.build_labels(facts, ACCESSION)
    assert "total_assets" in labels
    assert "debt_ratio" not in labels


def test_liabilities_is_an_input_not_a_golden_item():
    labels = ds.build_labels(BALANCE_SHEET, ACCESSION)
    assert "liabilities" not in labels
