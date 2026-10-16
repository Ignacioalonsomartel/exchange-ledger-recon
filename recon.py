"""
recon.py - reconcile an exchange export against the internal ledger.

Usage:
    python recon.py --exchange data/exchange.csv --ledger data/ledger.csv --config config.yaml

What it does, in order:
    1. Load and validate the config and both CSV files. Any problem stops the run.
    2. Convert exchange timestamps (UTC) to local dates.
    3. Pull out duplicate IDs first - they always need a human.
    4. Pair every remaining exchange row with the ledger row that references it.
    5. Classify every difference as TIMING, STANDING or NEEDS_REVIEW.
    6. Write output/breaks.csv and output/summary.md.

Control principles: this tool never changes the input files, never "forces" a
match and never books anything. It only classifies and reports. Every break
carries a reason code and the rule that classified it. Anything ambiguous goes
to NEEDS_REVIEW.
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

EXCHANGE_COLUMNS = ["exchange_txn_id", "timestamp_utc", "txn_type", "asset", "quantity", "usd_value"]
LEDGER_COLUMNS = [
    "ledger_entry_id", "external_ref", "booking_date", "txn_type", "asset", "quantity", "usd_value", "memo",
]

# Built-in rules. Each reason code maps to the rule ID written into breaks.csv,
# so a reviewer can always see which rule made the decision.
CORE_RULE_FOR_REASON = {
    "DATE_WITHIN_WINDOW": "CORE-TIMING",
    "MISSING_IN_LEDGER": "CORE-MISSING",
    "MISSING_ON_EXCHANGE": "CORE-MISSING",
    "QTY_MISMATCH": "CORE-QTY",
    "ASSET_MISMATCH": "CORE-ASSET",
    "DATE_OUTSIDE_WINDOW": "CORE-DATE",
    "MULTIPLE_DIFFERENCES": "CORE-MULTI",
    "DUPLICATE_EXCHANGE_ID": "CORE-DUPLICATE",
    "DUPLICATE_LEDGER_REF": "CORE-DUPLICATE",
    "AMBIGUOUS_STANDING_RULE": "CORE-AMBIGUOUS",
}

STANDING_RULE_TYPES = ["unmatched_below_usd", "unmatched_txn_type", "paired_quantity_rounding"]

# Column order of output/breaks.csv
BREAK_COLUMNS = [
    "break_id", "category", "reason_code", "rule_id", "rule_owner", "txn_ref", "presence", "txn_type",
    "exchange_date", "ledger_date", "day_gap", "exchange_asset", "ledger_asset",
    "exchange_quantity", "ledger_quantity", "quantity_diff", "exchange_usd", "ledger_usd",
    "ledger_entry_id", "detail",
]

# NEEDS_REVIEW is listed first in every output: it is the work that needs a person.
CATEGORY_ORDER = ["NEEDS_REVIEW", "TIMING", "STANDING"]


class ReconError(Exception):
    """A problem with the inputs or config. The run stops and nothing is written."""


# ---------------------------------------------------------------------------
# 1. Loading and validation
# ---------------------------------------------------------------------------

def load_config(path):
    path = Path(path)
    if not path.exists():
        raise ReconError(f"Config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    for key in ["reporting_timezone", "tolerances", "standing_rules"]:
        if key not in config:
            raise ReconError(f"Config is missing '{key}'")
    for key in ["quantity", "date_window_days"]:
        if key not in config["tolerances"]:
            raise ReconError(f"Config is missing tolerances.{key}")

    try:
        ZoneInfo(config["reporting_timezone"])
    except Exception:
        raise ReconError(f"Unknown reporting_timezone: {config['reporting_timezone']}")

    seen_ids = set()
    for rule in config["standing_rules"]:
        for key in ["id", "name", "description", "owner", "type"]:
            if key not in rule:
                raise ReconError(f"Standing rule {rule.get('id', '(no id)')} is missing '{key}'")
        if rule["id"] in seen_ids:
            raise ReconError(f"Standing rule id {rule['id']} is used twice")
        seen_ids.add(rule["id"])
        if rule["type"] not in STANDING_RULE_TYPES:
            raise ReconError(f"Standing rule {rule['id']} has unknown type '{rule['type']}'")
        if rule["type"].startswith("unmatched") and rule.get("side") not in ("exchange", "ledger"):
            raise ReconError(f"Standing rule {rule['id']} needs side: exchange or side: ledger")
    return config


def read_csv_checked(path, required_columns, label):
    """Read a CSV as text, and stop if the file or any required column is missing."""
    path = Path(path)
    if not path.exists():
        raise ReconError(f"{label} file not found: {path}")
    # Read everything as text first; numbers and dates are converted (and checked) below.
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in required_columns if c not in df.columns]
    if missing:
        raise ReconError(f"{label} file is missing column(s): {', '.join(missing)}")
    if df.empty:
        raise ReconError(f"{label} file has no rows: {path}")
    for column in required_columns:
        df[column] = df[column].str.strip()
    return df


def bad_lines(mask):
    """Turn a True/False column into CSV line numbers (line 1 is the header)."""
    lines = [str(i + 2) for i in mask[mask].index[:5]]
    more = " ..." if mask.sum() > 5 else ""
    return ", ".join(lines) + more


def require_not_blank(df, column, label):
    blank = df[column] == ""
    if blank.any():
        raise ReconError(f"{label}: '{column}' is blank on CSV line(s) {bad_lines(blank)}")


def to_numbers(df, column, label):
    numbers = pd.to_numeric(df[column], errors="coerce")
    if numbers.isna().any():
        raise ReconError(f"{label}: '{column}' is not a number on CSV line(s) {bad_lines(numbers.isna())}")
    return numbers


def load_exchange(path, timezone_name):
    df = read_csv_checked(path, EXCHANGE_COLUMNS, "Exchange")
    for column in ["exchange_txn_id", "txn_type", "asset"]:
        require_not_blank(df, column, "Exchange")
    df["quantity"] = to_numbers(df, "quantity", "Exchange")
    df["usd_value"] = to_numbers(df, "usd_value", "Exchange")

    timestamps = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce", format="ISO8601")
    if timestamps.isna().any():
        raise ReconError(f"Exchange: 'timestamp_utc' is not a valid timestamp on CSV line(s) {bad_lines(timestamps.isna())}")
    # The key timezone step: a trade at 9 PM in San Juan is already "tomorrow" in UTC.
    df["local_date"] = timestamps.dt.tz_convert(timezone_name).dt.date
    return df


def load_ledger(path):
    df = read_csv_checked(path, LEDGER_COLUMNS, "Ledger")
    for column in ["ledger_entry_id", "external_ref", "txn_type", "asset"]:
        require_not_blank(df, column, "Ledger")
    df["quantity"] = to_numbers(df, "quantity", "Ledger")
    df["usd_value"] = to_numbers(df, "usd_value", "Ledger")

    dates = pd.to_datetime(df["booking_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise ReconError(f"Ledger: 'booking_date' is not a YYYY-MM-DD date on CSV line(s) {bad_lines(dates.isna())}")
    df["booking_date"] = dates.dt.date
    return df


# ---------------------------------------------------------------------------
# 2. Describing one transaction: the "facts" of a pair
# ---------------------------------------------------------------------------

def build_facts(txn_ref, ex, lg):
    """
    Put everything we know about one transaction into a single dict.
    ex is the exchange row (a dict) or None; lg is the ledger row or None.
    """
    if ex is not None and lg is not None:
        presence = "both"
    elif ex is not None:
        presence = "exchange_only"
    else:
        presence = "ledger_only"

    facts = {
        "txn_ref": txn_ref,
        "presence": presence,
        "txn_type": ex["txn_type"] if ex is not None else lg["txn_type"],
        "exchange_date": ex["local_date"] if ex is not None else None,
        "ledger_date": lg["booking_date"] if lg is not None else None,
        "day_gap": None,
        "exchange_asset": ex["asset"] if ex is not None else None,
        "ledger_asset": lg["asset"] if lg is not None else None,
        "exchange_quantity": ex["quantity"] if ex is not None else None,
        "ledger_quantity": lg["quantity"] if lg is not None else None,
        "quantity_diff": None,
        "exchange_usd": ex["usd_value"] if ex is not None else None,
        "ledger_usd": lg["usd_value"] if lg is not None else None,
        "ledger_entry_id": lg["ledger_entry_id"] if lg is not None else None,
    }
    if presence == "both":
        # Positive day_gap means the ledger booked it later than the exchange.
        facts["day_gap"] = (facts["ledger_date"] - facts["exchange_date"]).days
        # Rounded to 8 decimals so tiny floating-point noise never looks like a difference.
        facts["quantity_diff"] = round(facts["exchange_quantity"] - facts["ledger_quantity"], 8)
    return facts


def find_differences(facts, tolerances):
    """List every way a paired exchange row and ledger row disagree."""
    differences = []
    if facts["exchange_asset"] != facts["ledger_asset"]:
        differences.append("ASSET_MISMATCH")
    if abs(facts["quantity_diff"]) > tolerances["quantity"]:
        differences.append("QTY_MISMATCH")
    gap = abs(facts["day_gap"])
    if gap > tolerances["date_window_days"]:
        differences.append("DATE_OUTSIDE_WINDOW")
    elif gap > 0:
        differences.append("DATE_WITHIN_WINDOW")
    return differences


# ---------------------------------------------------------------------------
# 3. Standing rules
# ---------------------------------------------------------------------------

def rule_matches(rule, facts, differences):
    """Does this standing rule from config.yaml describe this break?"""
    if rule["type"] == "unmatched_below_usd":
        side = rule["side"]
        return facts["presence"] == f"{side}_only" and abs(facts[f"{side}_usd"]) < rule["max_usd"]

    if rule["type"] == "unmatched_txn_type":
        side = rule["side"]
        return facts["presence"] == f"{side}_only" and facts["txn_type"] in rule["txn_types"]

    if rule["type"] == "paired_quantity_rounding":
        return (
            facts["presence"] == "both"
            and differences == ["QTY_MISMATCH"]  # the quantity gap must be the ONLY difference
            and facts["txn_type"] in rule["txn_types"]
            and abs(facts["quantity_diff"]) <= rule["max_quantity_diff"]
        )

    raise ReconError(f"Unknown standing rule type: {rule['type']}")


# ---------------------------------------------------------------------------
# 4. Classification
# ---------------------------------------------------------------------------

def result(category, reason_code, rule_id, detail, owner=""):
    return {"category": category, "reason_code": reason_code, "rule_id": rule_id,
            "rule_owner": owner, "detail": detail}


def classify(facts, config):
    """
    Decide what one transaction is. Returns None if it is clean (no break),
    otherwise a dict with category, reason_code, rule_id, rule_owner and detail.
    """
    tolerances = config["tolerances"]
    differences = []

    if facts["presence"] == "both":
        differences = find_differences(facts, tolerances)
        if not differences:
            return None  # clean: everything ties
        if differences == ["DATE_WITHIN_WINDOW"]:
            return result("TIMING", "DATE_WITHIN_WINDOW", "CORE-TIMING",
                          f"Ledger date is {facts['day_gap']:+d} day(s) from exchange date; amounts agree")
        if len(differences) == 1:
            core_reason = differences[0]
        else:
            core_reason = "MULTIPLE_DIFFERENCES"
    elif facts["presence"] == "exchange_only":
        core_reason = "MISSING_IN_LEDGER"
    else:
        core_reason = "MISSING_ON_EXCHANGE"

    # Before sending it to review, check whether it is a known standing item.
    matching_rules = [rule for rule in config["standing_rules"] if rule_matches(rule, facts, differences)]

    if len(matching_rules) == 1:
        rule = matching_rules[0]
        return result("STANDING", "STANDING_RULE_MATCH", rule["id"],
                      f"Known item: {rule['name']}", owner=rule["owner"])

    if len(matching_rules) > 1:
        ids = ", ".join(rule["id"] for rule in matching_rules)
        return result("NEEDS_REVIEW", "AMBIGUOUS_STANDING_RULE", "CORE-AMBIGUOUS",
                      f"Matches more than one standing rule: {ids}")

    detail = {
        "MISSING_IN_LEDGER": "On the exchange, no ledger entry references it",
        "MISSING_ON_EXCHANGE": "In the ledger, but the exchange export has no such transaction",
        "QTY_MISMATCH": "Quantity differs beyond tolerance",
        "ASSET_MISMATCH": "Asset differs between exchange and ledger",
        "DATE_OUTSIDE_WINDOW": f"Dates are {abs(facts['day_gap'] or 0)} days apart, beyond the "
                               f"{tolerances['date_window_days']}-day window",
        "MULTIPLE_DIFFERENCES": "Differs on: " + ", ".join(differences),
    }[core_reason]
    return result("NEEDS_REVIEW", core_reason, CORE_RULE_FOR_REASON[core_reason], detail)


def duplicate_breaks(exchange, ledger):
    """
    Find IDs that appear more than once on either side. Every row involved becomes
    its own NEEDS_REVIEW break (one line per duplicated row). We never pick which
    copy is "the real one".
    Returns (list of breaks, set of IDs that were handled here).
    """
    dup_exchange_ids = set(exchange.loc[exchange["exchange_txn_id"].duplicated(keep=False), "exchange_txn_id"])
    dup_ledger_refs = set(ledger.loc[ledger["external_ref"].duplicated(keep=False), "external_ref"])

    breaks = []
    for ref in sorted(dup_exchange_ids | dup_ledger_refs):
        ex_rows = exchange[exchange["exchange_txn_id"] == ref].to_dict("records")
        lg_rows = ledger[ledger["external_ref"] == ref].to_dict("records")

        if ref in dup_exchange_ids:
            # Show the ledger side alongside, but only if there is exactly one ledger row.
            counterpart = lg_rows[0] if len(lg_rows) == 1 else None
            for ex in ex_rows:
                facts = build_facts(ref, ex, counterpart)
                facts.update(result("NEEDS_REVIEW", "DUPLICATE_EXCHANGE_ID", "CORE-DUPLICATE",
                                    f"{len(ex_rows)} exchange rows share this ID"))
                breaks.append(facts)

        if ref in dup_ledger_refs:
            counterpart = ex_rows[0] if len(ex_rows) == 1 else None
            entry_ids = ", ".join(lg["ledger_entry_id"] for lg in lg_rows)
            for lg in lg_rows:
                facts = build_facts(ref, counterpart, lg)
                facts.update(result("NEEDS_REVIEW", "DUPLICATE_LEDGER_REF", "CORE-DUPLICATE",
                                    f"{len(lg_rows)} ledger entries reference this ID: {entry_ids}"))
                breaks.append(facts)

    return breaks, dup_exchange_ids | dup_ledger_refs


def reconcile(exchange, ledger, config):
    """
    The core of the tool. Takes the loaded exchange and ledger tables and the
    config; returns (breaks table, stats dict). Clean pairs are counted, not listed.
    """
    breaks, duplicate_ids = duplicate_breaks(exchange, ledger)

    # Index each side by the shared reference (duplicates already removed).
    exchange_by_ref = {row["exchange_txn_id"]: row for row in exchange.to_dict("records")
                       if row["exchange_txn_id"] not in duplicate_ids}
    ledger_by_ref = {row["external_ref"]: row for row in ledger.to_dict("records")
                     if row["external_ref"] not in duplicate_ids}

    # Every reference seen on either side (a "full outer join" in SQL terms).
    all_refs = sorted(set(exchange_by_ref) | set(ledger_by_ref))
    clean_pairs = 0
    for ref in all_refs:
        facts = build_facts(ref, exchange_by_ref.get(ref), ledger_by_ref.get(ref))
        outcome = classify(facts, config)
        if outcome is None:
            clean_pairs += 1
        else:
            facts.update(outcome)
            breaks.append(facts)

    table = pd.DataFrame(breaks, columns=[c for c in BREAK_COLUMNS if c != "break_id"])
    table["category_rank"] = table["category"].map(CATEGORY_ORDER.index)
    table = table.sort_values(["category_rank", "reason_code", "txn_ref"], kind="stable")
    table = table.drop(columns="category_rank").reset_index(drop=True)
    table.insert(0, "break_id", [f"BRK-{n:03d}" for n in range(1, len(table) + 1)])

    stats = {
        "exchange_rows": len(exchange),
        "ledger_rows": len(ledger),
        "references_compared": len(all_refs) + len(duplicate_ids),
        "clean_pairs": clean_pairs,
    }
    return table, stats


# ---------------------------------------------------------------------------
# 5. Output
# ---------------------------------------------------------------------------

def fmt_qty(value):
    """1234.5 -> '1,234.5'; drops trailing zeros. Blank if there is no value."""
    if value is None or pd.isna(value):
        return ""
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def fmt_usd(value):
    if value is None or pd.isna(value):
        return ""
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def write_breaks_csv(breaks, path):
    out = breaks.copy()
    for column in ["exchange_quantity", "ledger_quantity", "quantity_diff"]:
        out[column] = out[column].map(lambda v: "" if v is None or pd.isna(v) else f"{v:.8f}")
    for column in ["exchange_usd", "ledger_usd"]:
        out[column] = out[column].map(lambda v: "" if v is None or pd.isna(v) else f"{v:.2f}")
    out["day_gap"] = out["day_gap"].map(lambda v: "" if v is None or pd.isna(v) else str(int(v)))
    out.to_csv(path, index=False)


def write_up(brk, group, config):
    """
    Three sentences for one NEEDS_REVIEW item: what is different, the likely
    cause, and the decision needed. Fixed templates, so the wording is
    repeatable and auditable. `group` holds every break line for the same
    item (more than one only for duplicates).
    """
    ref = brk["txn_ref"]
    txn = brk["txn_type"].replace("_", " ").lower()
    window = config["tolerances"]["date_window_days"]
    reason = brk["reason_code"]

    if reason == "MISSING_IN_LEDGER":
        return (
            f"The exchange shows a {txn} of {fmt_qty(brk['exchange_quantity'])} {brk['exchange_asset']} "
            f"({fmt_usd(brk['exchange_usd'])}) on {brk['exchange_date']}, but no ledger entry references {ref}.",
            "Likely cause: the transaction was not booked, or it was booked without the exchange reference.",
            "Decision needed: confirm the transaction with the exchange, then book it or document why it should not be booked.",
        )
    if reason == "MISSING_ON_EXCHANGE":
        return (
            f"Ledger entry {brk['ledger_entry_id']} books a {txn} of {fmt_qty(brk['ledger_quantity'])} "
            f"{brk['ledger_asset']} ({fmt_usd(brk['ledger_usd'])}) on {brk['ledger_date']} against {ref}, "
            "but the exchange export has no such transaction.",
            "Likely cause: the entry references the wrong exchange ID, belongs to another account or venue, or was booked in error.",
            "Decision needed: find the source document, then correct the reference or reverse the entry.",
        )
    if reason == "QTY_MISMATCH":
        ex_q, lg_q = brk["exchange_quantity"], brk["ledger_quantity"]
        if ex_q == -lg_q:
            cause = "Likely cause: the entry was booked in the wrong direction (sign flipped)."
        else:
            cause = "Likely cause: a keying error, a partial fill, or a fee netted into the amount."
        return (
            f"{ref} ({txn}, {brk['exchange_asset']}) is {fmt_qty(ex_q)} on the exchange but {fmt_qty(lg_q)} "
            f"in the ledger, a difference of {fmt_qty(brk['quantity_diff'])}.",
            cause,
            "Decision needed: confirm the correct quantity from the exchange record and decide whether to correct the ledger entry.",
        )
    if reason == "ASSET_MISMATCH":
        return (
            f"{ref} is {fmt_qty(brk['exchange_quantity'])} {brk['exchange_asset']} on the exchange but is booked "
            f"as {fmt_qty(brk['ledger_quantity'])} {brk['ledger_asset']} in ledger entry {brk['ledger_entry_id']}.",
            "Likely cause: the wrong asset code was selected at booking (for example, a similar stablecoin).",
            "Decision needed: confirm which asset was actually received and reclass the ledger entry if it is wrong.",
        )
    if reason == "DATE_OUTSIDE_WINDOW":
        return (
            f"{ref} ({txn}, {fmt_qty(brk['exchange_quantity'])} {brk['exchange_asset']}) is dated "
            f"{brk['exchange_date']} on the exchange and {brk['ledger_date']} in the ledger, "
            f"{abs(int(brk['day_gap']))} days apart, beyond the {window}-day timing window.",
            "Likely cause: a late booking or an incorrectly dated entry.",
            "Decision needed: confirm the correct date and decide whether to re-date the entry, which may affect period-end balances.",
        )
    if reason == "MULTIPLE_DIFFERENCES":
        return (
            f"{ref} ({txn}) differs on more than one field: exchange {fmt_qty(brk['exchange_quantity'])} "
            f"{brk['exchange_asset']} on {brk['exchange_date']}, ledger {fmt_qty(brk['ledger_quantity'])} "
            f"{brk['ledger_asset']} on {brk['ledger_date']}.",
            "Likely cause: either a booking with several errors or a ledger entry tied to the wrong exchange ID; the tool does not guess which.",
            "Decision needed: compare against the exchange record and correct each field, or correct the reference.",
        )
    if reason == "DUPLICATE_LEDGER_REF":
        entry_ids = ", ".join(group["ledger_entry_id"])
        return (
            f"Exchange transaction {ref} ({txn}, {fmt_qty(brk['ledger_quantity'])} {brk['ledger_asset']}) "
            f"is referenced by {len(group)} ledger entries: {entry_ids}.",
            "Likely cause: the transaction was booked twice.",
            "Decision needed: confirm which entry is valid and reverse the duplicate(s).",
        )
    if reason == "DUPLICATE_EXCHANGE_ID":
        return (
            f"The exchange export contains {len(group)} rows with ID {ref} ({txn}, "
            f"{fmt_qty(brk['exchange_quantity'])} {brk['exchange_asset']}).",
            "Likely cause: an export glitch; less likely, two real transactions sharing an ID.",
            "Decision needed: re-pull the export or confirm with the exchange whether there were one or two transactions before booking anything.",
        )
    if reason == "AMBIGUOUS_STANDING_RULE":
        if brk["presence"] == "ledger_only":
            quantity, asset = brk["ledger_quantity"], brk["ledger_asset"]
        else:
            quantity, asset = brk["exchange_quantity"], brk["exchange_asset"]
        return (
            f"{ref} ({txn}, {fmt_qty(quantity)} {asset}) has no counterpart and fits more than one "
            "standing rule (see the detail line), so it was not auto-cleared.",
            "Likely cause: the standing rules overlap for this kind of item.",
            "Decision needed: the rule owners should agree which rule applies and tighten config.yaml so only one matches.",
        )
    raise ReconError(f"No write-up template for reason code {reason}")


def build_summary(breaks, stats, config, inputs):
    lines = []
    run_time = datetime.now(ZoneInfo(config["reporting_timezone"])).strftime("%Y-%m-%d %H:%M %Z")
    counts = breaks["category"].value_counts()

    lines += [
        "# Reconciliation summary",
        "",
        f"- Run at: {run_time}",
        f"- Exchange file: `{inputs['exchange']}` ({stats['exchange_rows']} rows)",
        f"- Ledger file: `{inputs['ledger']}` ({stats['ledger_rows']} rows)",
        f"- Config: `{inputs['config']}` (quantity tolerance {config['tolerances']['quantity']:.8f}, "
        f"date window {config['tolerances']['date_window_days']} days, timezone {config['reporting_timezone']})",
        f"- Transaction references compared: {stats['references_compared']}; clean (fully agree): {stats['clean_pairs']}",
        "",
        "This report classifies differences only. Nothing has been matched, cleared or booked.",
        "",
        "## Breaks by category",
        "",
        "| Category | Lines | What to do |",
        "|---|---|---|",
        f"| NEEDS_REVIEW | {counts.get('NEEDS_REVIEW', 0)} | Investigate and escalate today (write-ups below) |",
        f"| TIMING | {counts.get('TIMING', 0)} | Should clear on its own; confirm tomorrow |",
        f"| STANDING | {counts.get('STANDING', 0)} | Known items; rule owner monitors |",
        f"| **Total** | **{len(breaks)}** | |",
        "",
    ]

    standing = breaks[breaks["category"] == "STANDING"]
    lines += ["## Standing items by rule", ""]
    if standing.empty:
        lines += ["None.", ""]
    else:
        lines += ["| Rule | Lines | Total USD (exchange side) | Owner |", "|---|---|---|---|"]
        for rule in config["standing_rules"]:
            rows = standing[standing["rule_id"] == rule["id"]]
            if not rows.empty:
                lines.append(f"| {rule['id']} | {len(rows)} | {fmt_usd(rows['exchange_usd'].sum())} | {rule['owner']} |")
        lines.append("")

    timing = breaks[breaks["category"] == "TIMING"]
    lines += ["## Timing items", ""]
    if timing.empty:
        lines += ["None.", ""]
    else:
        lines += ["| Break | Reference | Type | Quantity | Exchange date | Ledger date |", "|---|---|---|---|---|---|"]
        for brk in timing.to_dict("records"):
            lines.append(
                f"| {brk['break_id']} | {brk['txn_ref']} | {brk['txn_type']} | "
                f"{fmt_qty(brk['exchange_quantity'])} {brk['exchange_asset']} | {brk['exchange_date']} | {brk['ledger_date']} |"
            )
        lines.append("")

    review = breaks[breaks["category"] == "NEEDS_REVIEW"]
    lines += ["## Needs review", ""]
    if review.empty:
        lines += ["None.", ""]
    else:
        lines += [f"{len(review)} line(s). Each item below needs a decision; none has been auto-cleared.", ""]
        done = set()
        for brk in review.to_dict("records"):
            key = (brk["reason_code"], brk["txn_ref"])
            if key in done:
                continue  # duplicates: one write-up covers every line of the group
            done.add(key)
            group = review[(review["reason_code"] == brk["reason_code"]) & (review["txn_ref"] == brk["txn_ref"])]
            what, cause, decision = write_up(brk, group, config)
            lines += [
                f"### {', '.join(group['break_id'])}: {brk['reason_code']} ({brk['txn_ref']})",
                "",
                f"{what} {cause} {decision}",
                "",
                f"*Rule: {brk['rule_id']}. Detail: {brk['detail']}*",
                "",
            ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. Command line
# ---------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(description="Reconcile an exchange export against the internal ledger.")
    parser.add_argument("--exchange", required=True, help="Exchange export CSV")
    parser.add_argument("--ledger", required=True, help="Internal ledger CSV")
    parser.add_argument("--config", required=True, help="config.yaml with tolerances and standing rules")
    parser.add_argument("--output", default="output", help="Folder for breaks.csv and summary.md (default: output)")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        exchange = load_exchange(args.exchange, config["reporting_timezone"])
        ledger = load_ledger(args.ledger)
        breaks, stats = reconcile(exchange, ledger, config)
    except ReconError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        print("The run stopped. No output was written; anything already in the output folder is from an "
              "earlier run and must not be used.", file=sys.stderr)
        return 1

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_breaks_csv(breaks, output_dir / "breaks.csv")
    inputs = {"exchange": args.exchange, "ledger": args.ledger, "config": args.config}
    (output_dir / "summary.md").write_text(build_summary(breaks, stats, config, inputs), encoding="utf-8")

    counts = breaks["category"].value_counts()
    print(f"Compared {stats['references_compared']} references: {stats['clean_pairs']} clean, {len(breaks)} break line(s).")
    for category in CATEGORY_ORDER:
        print(f"  {category:<13} {counts.get(category, 0)}")
    print(f"Wrote {output_dir / 'breaks.csv'} and {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
