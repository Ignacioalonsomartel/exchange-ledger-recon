"""
generate_sample_data.py

Creates SYNTHETIC (fake) data for the reconciliation demo:

  data/exchange.csv        what a crypto exchange's transaction export might look like
  data/ledger.csv          what our internal ledger might look like for the same account
  data/planted_breaks.csv  the answer key: every difference planted on purpose,
                           and the bucket recon.py is expected to put it in

Nothing here is real. IDs are made up, prices are illustrative (not market
data), and there are no wallet addresses or account numbers.

The random seed is fixed, so running this script twice produces identical files.

Run:  python generate_sample_data.py
"""

import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

SEED = 42
LOCAL_TZ = ZoneInfo("America/Puerto_Rico")  # must match reporting_timezone in config.yaml
FIRST_DAY = date(2026, 10, 1)
LAST_DAY = date(2026, 10, 14)
CLEAN_TRANSACTIONS = 166  # plus 34 planted exchange rows = 200 exchange rows
OUTPUT_DIR = Path(__file__).parent / "data"

# Illustrative starting prices in USD. Not market data.
START_PRICES = {"BTC": 62000.00, "ETH": 2400.00, "SOL": 150.00, "USDC": 1.00, "USDT": 1.00}

# Sign convention for quantity: positive = into our account, negative = out of it.
SIGN = {
    "TRADE_BUY": 1,
    "TRADE_SELL": -1,
    "DEPOSIT": 1,
    "WITHDRAWAL": -1,
    "FEE": -1,
    "STAKING_REWARD": 1,
}

# Typical size ranges (in units of the asset) for the clean, everyday transactions.
TRADE_SIZE = {"BTC": (0.05, 2.0), "ETH": (0.5, 30.0), "SOL": (10.0, 800.0)}
TRANSFER_SIZE = {"BTC": (0.1, 5.0), "ETH": (1.0, 50.0), "USDC": (1_000.0, 250_000.0)}

rng = random.Random(SEED)

# These lists are filled in as transactions are created, then written to CSV at the end.
exchange_rows = []
ledger_rows = []
planted_breaks = []
used_exchange_ids = set()


def all_days():
    """Every calendar day in the sample period, in order."""
    days = []
    day = FIRST_DAY
    while day <= LAST_DAY:
        days.append(day)
        day += timedelta(days=1)
    return days


def build_daily_prices():
    """One illustrative USD price per asset per day: a small random walk (+/- 2% a day)."""
    prices = {}
    current = dict(START_PRICES)
    for day in all_days():
        for asset in ("BTC", "ETH", "SOL"):
            current[asset] = round(current[asset] * (1 + rng.uniform(-0.02, 0.02)), 2)
        prices[day] = dict(current)
    return prices


PRICES = build_daily_prices()


def new_exchange_id():
    """A unique, made-up exchange transaction ID such as EX-482913."""
    while True:
        candidate = f"EX-{rng.randint(100000, 999999)}"
        if candidate not in used_exchange_ids:
            used_exchange_ids.add(candidate)
            return candidate


def utc_timestamp(local_day, hour=None):
    """
    Pick a time on local_day (San Juan time) and return it in UTC, the way an
    exchange reports it, e.g. "2026-10-15T01:40:00Z".

    San Juan is UTC-4, so anything after 8 PM local shows up as the NEXT day in UTC.
    """
    if hour is None:
        hour = rng.randint(0, 23)
    local_time = time(hour, rng.randint(0, 59), rng.randint(0, 59))
    local_dt = datetime.combine(local_day, local_time, tzinfo=LOCAL_TZ)
    return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_quantity(quantity):
    return f"{quantity:.8f}"


def format_usd(value):
    return f"{value:.2f}"


def add_transaction(
    local_day,
    txn_type,
    asset,
    quantity,
    hour=None,
    book_in_ledger=True,
    ledger_day=None,
    ledger_quantity=None,
    ledger_asset=None,
    ledger_copies=1,
    exchange_copies=1,
):
    """
    Create one exchange row and, normally, the matching ledger row.

    By default the ledger row is an exact copy: same date, asset and quantity,
    with external_ref pointing at the exchange ID. The ledger_* arguments let us
    plant one specific difference. Returns the exchange_txn_id so the answer key
    can refer to it.
    """
    txn_id = new_exchange_id()
    price = PRICES[local_day][asset]
    timestamp = utc_timestamp(local_day, hour)

    for _ in range(exchange_copies):
        exchange_rows.append(
            {
                "exchange_txn_id": txn_id,
                "timestamp_utc": timestamp,
                "txn_type": txn_type,
                "asset": asset,
                "quantity": format_quantity(quantity),
                "usd_value": format_usd(quantity * price),
            }
        )

    if not book_in_ledger:
        return txn_id

    booked_day = ledger_day or local_day
    booked_quantity = quantity if ledger_quantity is None else ledger_quantity
    booked_asset = ledger_asset or asset
    booked_price = PRICES[local_day][booked_asset]

    for _ in range(ledger_copies):
        ledger_rows.append(
            {
                "external_ref": txn_id,
                "booking_date": booked_day.isoformat(),
                "txn_type": txn_type,
                "asset": booked_asset,
                "quantity": format_quantity(booked_quantity),
                "usd_value": format_usd(booked_quantity * booked_price),
                "memo": f"{txn_type.replace('_', ' ').title()} {booked_asset}",
            }
        )
    return txn_id


def add_ledger_only(booking_day, txn_type, asset, quantity, external_ref):
    """A ledger row with no exchange counterpart (used to plant MISSING_ON_EXCHANGE)."""
    ledger_rows.append(
        {
            "external_ref": external_ref,
            "booking_date": booking_day.isoformat(),
            "txn_type": txn_type,
            "asset": asset,
            "quantity": format_quantity(quantity),
            "usd_value": format_usd(quantity * PRICES[booking_day][asset]),
            "memo": f"{txn_type.replace('_', ' ').title()} {asset}",
        }
    )


def plant(txn_ref, category, reason_code, rule_id, description):
    """Record a planted break in the answer key."""
    planted_breaks.append(
        {
            "txn_ref": txn_ref,
            "expected_category": category,
            "expected_reason_code": reason_code,
            "expected_rule_id": rule_id,
            "description": description,
        }
    )


def add_clean_transactions():
    """Everyday activity where the exchange and the ledger agree exactly."""
    for _ in range(CLEAN_TRANSACTIONS):
        day = rng.choice(all_days())
        kind = rng.choices(["trade", "deposit", "withdrawal", "fee"], weights=[50, 15, 15, 20])[0]

        if kind == "trade":
            asset = rng.choice(["BTC", "ETH", "SOL"])
            txn_type = rng.choice(["TRADE_BUY", "TRADE_SELL"])
            size = round(rng.uniform(*TRADE_SIZE[asset]), 4)
        elif kind in ("deposit", "withdrawal"):
            asset = rng.choice(["BTC", "ETH", "USDC"])
            txn_type = "DEPOSIT" if kind == "deposit" else "WITHDRAWAL"
            decimals = 2 if asset == "USDC" else 4
            size = round(rng.uniform(*TRANSFER_SIZE[asset]), decimals)
        else:
            asset = "USDC"
            txn_type = "FEE"
            size = round(rng.uniform(1.0, 150.0), 2)

        add_transaction(day, txn_type, asset, SIGN[txn_type] * size)


def add_planted_breaks():
    """Deliberate differences, one or more of each type, with the expected outcome recorded."""
    def d(day):
        """Shorthand used below: d(3) means Oct 3, 2026."""
        return date(2026, 10, day)

    # ---- TIMING: same transaction, booked a day or two later --------------------
    ref = add_transaction(d(3), "DEPOSIT", "BTC", 1.25, hour=10, ledger_day=d(4))
    plant(ref, "TIMING", "DATE_WITHIN_WINDOW", "CORE-TIMING",
          "BTC deposit on exchange 10/03, booked in ledger 10/04 (1 day)")

    ref = add_transaction(d(9), "WITHDRAWAL", "ETH", -12.5, hour=11, ledger_day=d(11))
    plant(ref, "TIMING", "DATE_WITHIN_WINDOW", "CORE-TIMING",
          "ETH withdrawal on exchange 10/09, booked 10/11 (2 days: edge of the window)")

    ref = add_transaction(d(13), "TRADE_SELL", "SOL", -340.0, hour=9, ledger_day=d(14))
    plant(ref, "TIMING", "DATE_WITHIN_WINDOW", "CORE-TIMING",
          "SOL sell on exchange 10/13, booked 10/14 (1 day)")

    # ---- STANDING STND-001: dust under $1, never booked individually --------------
    ref = add_transaction(d(5), "DEPOSIT", "USDC", 0.42, book_in_ledger=False)
    plant(ref, "STANDING", "STANDING_RULE_MATCH", "STND-001", "USDC dust deposit of $0.42")

    ref = add_transaction(d(11), "DEPOSIT", "SOL", 0.003, book_in_ledger=False)
    plant(ref, "STANDING", "STANDING_RULE_MATCH", "STND-001", "SOL dust deposit of 0.003 SOL (under $1)")

    # ---- STANDING STND-002: daily SOL staking rewards, ledger books monthly ------
    for day in all_days():
        reward = round(rng.uniform(0.09, 0.12), 6)
        ref = add_transaction(day, "STAKING_REWARD", "SOL", reward, book_in_ledger=False)
        plant(ref, "STANDING", "STANDING_RULE_MATCH", "STND-002",
              f"Daily SOL staking reward {day.isoformat()}, ledger accrues at month-end")

    # ---- STANDING STND-003: BTC fees, exchange 8 decimals vs ledger 6 decimals ---
    for day, fee in [(d(2), -0.00012345), (d(7), -0.00048761), (d(12), -0.00031297)]:
        ref = add_transaction(day, "FEE", "BTC", fee, ledger_quantity=round(fee, 6))
        plant(ref, "STANDING", "STANDING_RULE_MATCH", "STND-003",
              f"BTC fee {fee:.8f} on exchange, {round(fee, 6):.6f} in ledger (rounding)")

    # ---- NEEDS REVIEW: everything that needs a human decision ----------------------
    ref = add_transaction(d(6), "WITHDRAWAL", "USDC", -25_000.00, book_in_ledger=False)
    plant(ref, "NEEDS_REVIEW", "MISSING_IN_LEDGER", "CORE-MISSING",
          "USDC 25,000 withdrawal on exchange, never booked in ledger")

    ref = add_transaction(d(10), "TRADE_BUY", "ETH", 3.5, book_in_ledger=False)
    plant(ref, "NEEDS_REVIEW", "MISSING_IN_LEDGER", "CORE-MISSING",
          "ETH 3.5 buy on exchange, never booked in ledger")

    ghost_ref = new_exchange_id()  # an ID the exchange never actually issued
    add_ledger_only(d(7), "DEPOSIT", "BTC", 0.8, external_ref=ghost_ref)
    plant(ghost_ref, "NEEDS_REVIEW", "MISSING_ON_EXCHANGE", "CORE-MISSING",
          "Ledger shows a 0.8 BTC deposit the exchange has no record of")

    ref = add_transaction(d(8), "TRADE_BUY", "SOL", 120.5, ledger_quantity=125.0)
    plant(ref, "NEEDS_REVIEW", "QTY_MISMATCH", "CORE-QTY",
          "SOL buy: exchange 120.5, ledger 125.0")

    ref = add_transaction(d(12), "DEPOSIT", "USDC", 10_000.00, ledger_quantity=-10_000.00)
    plant(ref, "NEEDS_REVIEW", "QTY_MISMATCH", "CORE-QTY",
          "USDC deposit of +10,000 booked in ledger as -10,000 (sign flipped)")

    ref = add_transaction(d(4), "DEPOSIT", "USDC", 50_000.00, ledger_asset="USDT")
    plant(ref, "NEEDS_REVIEW", "ASSET_MISMATCH", "CORE-ASSET",
          "USDC 50,000 deposit booked in ledger as USDT")

    ref = add_transaction(d(9), "TRADE_SELL", "ETH", -8.0, ledger_copies=2)
    plant(ref, "NEEDS_REVIEW", "DUPLICATE_LEDGER_REF", "CORE-DUPLICATE",
          "ETH 8.0 sell booked twice in ledger")

    ref = add_transaction(d(11), "TRADE_BUY", "BTC", 0.4, exchange_copies=2)
    plant(ref, "NEEDS_REVIEW", "DUPLICATE_EXCHANGE_ID", "CORE-DUPLICATE",
          "BTC 0.4 buy appears twice in the exchange export (same ID)")

    ref = add_transaction(d(2), "TRADE_SELL", "BTC", -0.75, hour=14, ledger_day=d(8))
    plant(ref, "NEEDS_REVIEW", "DATE_OUTSIDE_WINDOW", "CORE-DATE",
          "BTC sell on exchange 10/02, booked 10/08 (6 days, outside 2-day window)")

    ref = add_transaction(d(5), "DEPOSIT", "ETH", 20.0, hour=15, ledger_day=d(6), ledger_quantity=2.0)
    plant(ref, "NEEDS_REVIEW", "MULTIPLE_DIFFERENCES", "CORE-MULTI",
          "ETH deposit: exchange 20.0 on 10/05, ledger 2.0 on 10/06 (date AND quantity)")

    ref = add_transaction(d(14), "STAKING_REWARD", "ETH", 0.00015, book_in_ledger=False)
    plant(ref, "NEEDS_REVIEW", "AMBIGUOUS_STANDING_RULE", "CORE-AMBIGUOUS",
          "Tiny ETH staking reward: matches BOTH dust (STND-001) and staking (STND-002)")

    ref = add_transaction(d(13), "FEE", "BTC", -0.00051234, ledger_quantity=-0.00041234)
    plant(ref, "NEEDS_REVIEW", "QTY_MISMATCH", "CORE-QTY",
          "BTC fee off by 0.0001: too large to be rounding (STND-003 must NOT apply)")


def write_files():
    OUTPUT_DIR.mkdir(exist_ok=True)

    exchange = pd.DataFrame(exchange_rows).sort_values("timestamp_utc", kind="stable")
    exchange.to_csv(OUTPUT_DIR / "exchange.csv", index=False)

    # Ledger IDs are numbered in booking order, like a real ledger would.
    ledger = pd.DataFrame(ledger_rows).sort_values(["booking_date", "external_ref"], kind="stable")
    ledger.insert(0, "ledger_entry_id", [f"LG-{n:06d}" for n in range(1, len(ledger) + 1)])
    ledger.to_csv(OUTPUT_DIR / "ledger.csv", index=False)

    pd.DataFrame(planted_breaks).to_csv(OUTPUT_DIR / "planted_breaks.csv", index=False)

    print(f"exchange.csv:        {len(exchange)} rows")
    print(f"ledger.csv:          {len(ledger)} rows")
    print(f"planted_breaks.csv:  {len(planted_breaks)} planted breaks")


def main():
    add_clean_transactions()
    add_planted_breaks()
    write_files()


if __name__ == "__main__":
    main()
