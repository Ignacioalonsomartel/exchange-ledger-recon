"""
Tests for recon.py.

Most tests build a tiny exchange file and ledger file containing ONE situation,
run the reconciliation with the real config.yaml, and check the result.
The last tests run the full sample data and grade it against the answer key
written by generate_sample_data.py.

Run from the project folder:  python -m pytest
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # so "import recon" finds recon.py in the project folder

import recon  # noqa: E402

CONFIG = recon.load_config(ROOT / "config.yaml")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def exchange_row(ref, timestamp="2026-10-05T15:00:00Z", txn_type="TRADE_BUY", asset="BTC",
                 quantity=1.0, usd_value=60000.00):
    return {"exchange_txn_id": ref, "timestamp_utc": timestamp, "txn_type": txn_type,
            "asset": asset, "quantity": quantity, "usd_value": usd_value}


def ledger_row(ref, booking_date="2026-10-05", txn_type="TRADE_BUY", asset="BTC",
               quantity=1.0, usd_value=60000.00, entry_id=None):
    return {"ledger_entry_id": entry_id or f"LG-{ref}", "external_ref": ref, "booking_date": booking_date,
            "txn_type": txn_type, "asset": asset, "quantity": quantity, "usd_value": usd_value, "memo": "test"}


def run(tmp_path, exchange_rows, ledger_rows):
    """Write the rows to CSV files, load them the same way the CLI does, and reconcile."""
    exchange_path = tmp_path / "exchange.csv"
    ledger_path = tmp_path / "ledger.csv"
    pd.DataFrame(exchange_rows, columns=recon.EXCHANGE_COLUMNS).to_csv(exchange_path, index=False)
    pd.DataFrame(ledger_rows, columns=recon.LEDGER_COLUMNS).to_csv(ledger_path, index=False)
    exchange = recon.load_exchange(exchange_path, CONFIG["reporting_timezone"])
    ledger = recon.load_ledger(ledger_path)
    breaks, stats = recon.reconcile(exchange, ledger, CONFIG)
    return breaks


def only_break(breaks):
    """Most tests plant exactly one break; return it as a dict."""
    assert len(breaks) == 1, f"expected exactly one break, got {len(breaks)}:\n{breaks}"
    return breaks.iloc[0].to_dict()


# A clean pair that sits alongside each planted break, so every test also proves
# that normal rows stay out of the output.
CLEAN_EX = exchange_row("EX-CLEAN")
CLEAN_LG = ledger_row("EX-CLEAN")


# ---------------------------------------------------------------------------
# Clean rows
# ---------------------------------------------------------------------------

def test_clean_rows_produce_no_breaks(tmp_path):
    exchange = [
        exchange_row("EX-1"),
        exchange_row("EX-2", txn_type="DEPOSIT", asset="USDC", quantity=5000.0, usd_value=5000.0),
        exchange_row("EX-3", txn_type="FEE", asset="USDC", quantity=-12.5, usd_value=-12.5),
        # 01:30 UTC on Oct 6 is 9:30 PM on Oct 5 in San Juan: it must match a ledger date of Oct 5.
        exchange_row("EX-4", timestamp="2026-10-06T01:30:00Z", txn_type="TRADE_SELL", asset="ETH",
                     quantity=-3.0, usd_value=-7200.0),
    ]
    ledger = [
        ledger_row("EX-1"),
        ledger_row("EX-2", txn_type="DEPOSIT", asset="USDC", quantity=5000.0, usd_value=5000.0),
        ledger_row("EX-3", txn_type="FEE", asset="USDC", quantity=-12.5, usd_value=-12.5),
        ledger_row("EX-4", booking_date="2026-10-05", txn_type="TRADE_SELL", asset="ETH",
                   quantity=-3.0, usd_value=-7200.0),
    ]
    breaks = run(tmp_path, exchange, ledger)
    assert breaks.empty


# ---------------------------------------------------------------------------
# TIMING
# ---------------------------------------------------------------------------

def test_timing_break_one_day_late(tmp_path):
    breaks = run(tmp_path, [CLEAN_EX, exchange_row("EX-T")],
                 [CLEAN_LG, ledger_row("EX-T", booking_date="2026-10-06")])
    brk = only_break(breaks)
    assert brk["category"] == "TIMING"
    assert brk["reason_code"] == "DATE_WITHIN_WINDOW"
    assert brk["rule_id"] == "CORE-TIMING"
    assert brk["day_gap"] == 1


def test_timing_window_edge(tmp_path):
    """2 days apart is still TIMING; 3 days apart is outside the window and needs review."""
    breaks = run(tmp_path, [exchange_row("EX-2D"), exchange_row("EX-3D")],
                 [ledger_row("EX-2D", booking_date="2026-10-07"), ledger_row("EX-3D", booking_date="2026-10-08")])
    by_ref = breaks.set_index("txn_ref")
    assert by_ref.loc["EX-2D", "category"] == "TIMING"
    assert by_ref.loc["EX-3D", "category"] == "NEEDS_REVIEW"
    assert by_ref.loc["EX-3D", "reason_code"] == "DATE_OUTSIDE_WINDOW"
    assert by_ref.loc["EX-3D", "rule_id"] == "CORE-DATE"


# ---------------------------------------------------------------------------
# STANDING
# ---------------------------------------------------------------------------

def test_standing_dust(tmp_path):
    dust = exchange_row("EX-DUST", txn_type="DEPOSIT", asset="USDC", quantity=0.42, usd_value=0.42)
    brk = only_break(run(tmp_path, [CLEAN_EX, dust], [CLEAN_LG]))
    assert brk["category"] == "STANDING"
    assert brk["rule_id"] == "STND-001"
    assert brk["rule_owner"] == "Crypto Ops - Fund Accounting"


def test_standing_staking_reward(tmp_path):
    reward = exchange_row("EX-STK", txn_type="STAKING_REWARD", asset="SOL", quantity=0.11, usd_value=16.50)
    brk = only_break(run(tmp_path, [CLEAN_EX, reward], [CLEAN_LG]))
    assert brk["category"] == "STANDING"
    assert brk["rule_id"] == "STND-002"


def test_standing_fee_rounding(tmp_path):
    fee_ex = exchange_row("EX-FEE", txn_type="FEE", quantity=-0.00012345, usd_value=-7.65)
    fee_lg = ledger_row("EX-FEE", txn_type="FEE", quantity=-0.000123, usd_value=-7.63)
    brk = only_break(run(tmp_path, [CLEAN_EX, fee_ex], [CLEAN_LG, fee_lg]))
    assert brk["category"] == "STANDING"
    assert brk["rule_id"] == "STND-003"


def test_fee_difference_too_large_is_not_rounding(tmp_path):
    fee_ex = exchange_row("EX-FEE", txn_type="FEE", quantity=-0.00051234, usd_value=-31.75)
    fee_lg = ledger_row("EX-FEE", txn_type="FEE", quantity=-0.00041234, usd_value=-25.55)
    brk = only_break(run(tmp_path, [fee_ex], [fee_lg]))
    assert brk["category"] == "NEEDS_REVIEW"
    assert brk["reason_code"] == "QTY_MISMATCH"


def test_fee_rounding_with_date_gap_is_not_standing(tmp_path):
    """A standing rule only covers its exact situation: rounding PLUS a date gap needs a person."""
    fee_ex = exchange_row("EX-FEE", txn_type="FEE", quantity=-0.00012345, usd_value=-7.65)
    fee_lg = ledger_row("EX-FEE", booking_date="2026-10-06", txn_type="FEE", quantity=-0.000123, usd_value=-7.63)
    brk = only_break(run(tmp_path, [fee_ex], [fee_lg]))
    assert brk["category"] == "NEEDS_REVIEW"
    assert brk["reason_code"] == "MULTIPLE_DIFFERENCES"


# ---------------------------------------------------------------------------
# NEEDS REVIEW
# ---------------------------------------------------------------------------

def test_missing_in_ledger(tmp_path):
    missing = exchange_row("EX-MISS", txn_type="WITHDRAWAL", asset="USDC", quantity=-25000.0, usd_value=-25000.0)
    brk = only_break(run(tmp_path, [CLEAN_EX, missing], [CLEAN_LG]))
    assert brk["category"] == "NEEDS_REVIEW"
    assert brk["reason_code"] == "MISSING_IN_LEDGER"
    assert brk["rule_id"] == "CORE-MISSING"


def test_missing_on_exchange(tmp_path):
    ghost = ledger_row("EX-GHOST", txn_type="DEPOSIT", quantity=0.8, usd_value=49600.0)
    brk = only_break(run(tmp_path, [CLEAN_EX], [CLEAN_LG, ghost]))
    assert brk["category"] == "NEEDS_REVIEW"
    assert brk["reason_code"] == "MISSING_ON_EXCHANGE"


def test_quantity_mismatch(tmp_path):
    brk = only_break(run(tmp_path, [CLEAN_EX, exchange_row("EX-Q", asset="SOL", quantity=120.5)],
                         [CLEAN_LG, ledger_row("EX-Q", asset="SOL", quantity=125.0)]))
    assert brk["category"] == "NEEDS_REVIEW"
    assert brk["reason_code"] == "QTY_MISMATCH"
    assert brk["quantity_diff"] == pytest.approx(-4.5)


def test_sign_flip_is_a_quantity_mismatch(tmp_path):
    brk = only_break(run(tmp_path, [exchange_row("EX-S", txn_type="DEPOSIT", asset="USDC", quantity=10000.0)],
                         [ledger_row("EX-S", txn_type="DEPOSIT", asset="USDC", quantity=-10000.0)]))
    assert brk["reason_code"] == "QTY_MISMATCH"


def test_asset_mismatch(tmp_path):
    brk = only_break(run(tmp_path, [CLEAN_EX, exchange_row("EX-A", asset="USDC", quantity=50000.0)],
                         [CLEAN_LG, ledger_row("EX-A", asset="USDT", quantity=50000.0)]))
    assert brk["category"] == "NEEDS_REVIEW"
    assert brk["reason_code"] == "ASSET_MISMATCH"
    assert brk["rule_id"] == "CORE-ASSET"


def test_duplicate_ledger_ref_gives_one_line_per_ledger_row(tmp_path):
    breaks = run(tmp_path, [CLEAN_EX, exchange_row("EX-D")],
                 [CLEAN_LG, ledger_row("EX-D", entry_id="LG-1"), ledger_row("EX-D", entry_id="LG-2")])
    assert len(breaks) == 2
    assert set(breaks["reason_code"]) == {"DUPLICATE_LEDGER_REF"}
    assert set(breaks["ledger_entry_id"]) == {"LG-1", "LG-2"}
    assert set(breaks["category"]) == {"NEEDS_REVIEW"}


def test_duplicate_exchange_id_gives_one_line_per_exchange_row(tmp_path):
    breaks = run(tmp_path, [CLEAN_EX, exchange_row("EX-D"), exchange_row("EX-D")],
                 [CLEAN_LG, ledger_row("EX-D")])
    assert len(breaks) == 2
    assert set(breaks["reason_code"]) == {"DUPLICATE_EXCHANGE_ID"}
    assert set(breaks["category"]) == {"NEEDS_REVIEW"}


def test_multiple_differences(tmp_path):
    brk = only_break(run(tmp_path, [exchange_row("EX-M", asset="ETH", quantity=20.0)],
                         [ledger_row("EX-M", booking_date="2026-10-06", asset="ETH", quantity=2.0)]))
    assert brk["category"] == "NEEDS_REVIEW"
    assert brk["reason_code"] == "MULTIPLE_DIFFERENCES"
    assert "QTY_MISMATCH" in brk["detail"] and "DATE_WITHIN_WINDOW" in brk["detail"]


def test_ambiguous_standing_rule(tmp_path):
    """A tiny staking reward fits both dust and staking: flag it, don't pick a rule."""
    tiny = exchange_row("EX-AMB", txn_type="STAKING_REWARD", asset="ETH", quantity=0.00015, usd_value=0.36)
    brk = only_break(run(tmp_path, [CLEAN_EX, tiny], [CLEAN_LG]))
    assert brk["category"] == "NEEDS_REVIEW"
    assert brk["reason_code"] == "AMBIGUOUS_STANDING_RULE"
    assert "STND-001" in brk["detail"] and "STND-002" in brk["detail"]


# ---------------------------------------------------------------------------
# Bad input stops the run
# ---------------------------------------------------------------------------

def test_missing_column_stops_the_run(tmp_path):
    path = tmp_path / "exchange.csv"
    path.write_text("exchange_txn_id,timestamp_utc,asset,quantity,usd_value\nEX-1,2026-10-05T15:00:00Z,BTC,1,1\n")
    with pytest.raises(recon.ReconError, match="missing column"):
        recon.load_exchange(path, CONFIG["reporting_timezone"])


def test_non_numeric_quantity_stops_the_run(tmp_path):
    path = tmp_path / "ledger.csv"
    pd.DataFrame([ledger_row("EX-1", quantity="one")]).to_csv(path, index=False)
    with pytest.raises(recon.ReconError, match="not a number on CSV line"):
        recon.load_ledger(path)


def test_cli_error_writes_nothing(tmp_path):
    output = tmp_path / "out"
    code = recon.main(["--exchange", str(tmp_path / "nope.csv"), "--ledger", str(ROOT / "data" / "ledger.csv"),
                       "--config", str(ROOT / "config.yaml"), "--output", str(output)])
    assert code == 1
    assert not output.exists()


# ---------------------------------------------------------------------------
# Full sample data vs. the answer key
# ---------------------------------------------------------------------------

def test_sample_data_matches_answer_key(tmp_path):
    """Every planted break lands in the expected bucket, and nothing else is flagged."""
    exchange = recon.load_exchange(ROOT / "data" / "exchange.csv", CONFIG["reporting_timezone"])
    ledger = recon.load_ledger(ROOT / "data" / "ledger.csv")
    breaks, stats = recon.reconcile(exchange, ledger, CONFIG)
    answer_key = pd.read_csv(ROOT / "data" / "planted_breaks.csv")

    # One row per reference (duplicates produce two lines with the same reference and outcome).
    actual = breaks[["txn_ref", "category", "reason_code", "rule_id"]].drop_duplicates()
    assert actual["txn_ref"].is_unique

    expected = answer_key.rename(columns={
        "expected_category": "category", "expected_reason_code": "reason_code", "expected_rule_id": "rule_id",
    })[["txn_ref", "category", "reason_code", "rule_id"]]

    compared = expected.merge(actual, on="txn_ref", how="outer", suffixes=("_expected", "_actual"), indicator=True)
    not_flagged = compared[compared["_merge"] == "left_only"]
    unexpected = compared[compared["_merge"] == "right_only"]
    wrong_bucket = compared[
        (compared["_merge"] == "both")
        & ((compared["category_expected"] != compared["category_actual"])
           | (compared["reason_code_expected"] != compared["reason_code_actual"])
           | (compared["rule_id_expected"] != compared["rule_id_actual"]))
    ]
    assert not_flagged.empty, f"Planted breaks the tool missed:\n{not_flagged}"
    assert unexpected.empty, f"Breaks the tool raised that were not planted:\n{unexpected}"
    assert wrong_bucket.empty, f"Breaks classified differently than expected:\n{wrong_bucket}"

    # 34 planted items + 1 extra line each for the two duplicate cases.
    assert len(breaks) == 36
    assert stats["clean_pairs"] == 166


def test_cli_writes_both_output_files(tmp_path):
    code = recon.main(["--exchange", str(ROOT / "data" / "exchange.csv"), "--ledger", str(ROOT / "data" / "ledger.csv"),
                       "--config", str(ROOT / "config.yaml"), "--output", str(tmp_path)])
    assert code == 0
    breaks = pd.read_csv(tmp_path / "breaks.csv")
    assert list(breaks.columns) == recon.BREAK_COLUMNS
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "## Needs review" in summary
    assert "Decision needed" in summary
