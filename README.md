# exchange-ledger-recon

A small Python tool for a daily crypto position reconciliation. It compares an exchange's
transaction export against the internal ledger, finds every difference ("break"), and sorts
each one into one of three buckets:

| Category | Meaning | What happens next |
|---|---|---|
| **TIMING** | Same transaction on both sides, booked a day or two apart | Should clear on its own; watch it |
| **STANDING** | A known, documented recurring difference listed in `config.yaml` | Rule owner monitors; no daily action |
| **NEEDS_REVIEW** | Everything else: missing on one side, quantity or asset mismatch, duplicates, ambiguous cases | A person investigates and makes a decision. Never auto-cleared |

The output is a line-by-line break file (`output/breaks.csv`) and a short report
(`output/summary.md`) with counts per category and a three-sentence write-up for every item
that needs a decision: what is different, the likely cause, and the decision needed.

All data in this repository is **synthetic**. There is no real company data, no real wallet
addresses and no account numbers. Prices are illustrative, not market data.

## Why it matters

In a morning reconciliation, most of the differences between an exchange and the books are
noise: a trade that hit the exchange late in the evening and was booked the next morning, or
daily staking rewards the ledger books once a month. The real risk is the handful of breaks
hiding in that noise, such as a withdrawal that was never booked or a deposit booked with
the wrong sign. This tool clears the noise *with an audit trail* so the analyst's time goes
to the breaks that need a decision.

## Control principles

1. **The tool never forces a match or books anything.** It reads two files and reports. It
   does not change the input files, and it does not write to any system.
2. **Every break gets a reason code and the rule that classified it.** A reviewer can audit
   any decision: `CORE-TIMING` means the built-in timing rule; `STND-002` means standing rule
   STND-002 in `config.yaml`, with its description and owner.
3. **Anything ambiguous goes to NEEDS_REVIEW. Flag rather than guess.** For example:
   - A transaction that fits two standing rules is flagged, not assigned to one of them.
   - A duplicate ID is flagged on every copy; the tool does not pick "the real one".
   - Matching is by reference only. There is no fuzzy "same amount, probably the same trade" matching.
   - A standing rule covers only its exact situation. A fee rounding difference *plus* a
     date gap is not standing; it goes to review.
4. **Bad input stops the run.** A missing column, unparseable number or date, or blank ID
   stops the tool with an error, and no output is written. A partial reconciliation is worse
   than none.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m venv .venv
# Windows (PowerShell):   .venv\Scripts\Activate.ps1
# macOS / Linux:          source .venv/bin/activate
pip install -r requirements.txt

python generate_sample_data.py      # creates data/exchange.csv, data/ledger.csv, data/planted_breaks.csv
python recon.py --exchange data/exchange.csv --ledger data/ledger.csv --config config.yaml
python -m pytest                    # 22 tests
```

Example console output:

```
Compared 200 references: 166 clean, 36 break line(s).
  NEEDS_REVIEW  14
  TIMING        3
  STANDING      19
Wrote output\breaks.csv and output\summary.md
```

An optional `--output <folder>` writes the results somewhere other than `output/`, which is
useful for keeping one folder per day.

To open the step-by-step notebook: `pip install notebook`, then `jupyter notebook notebooks/walkthrough.ipynb`.

## Example write-up from `summary.md`

> **BRK-011: MULTIPLE_DIFFERENCES (EX-575528)**
>
> EX-575528 (deposit) differs on more than one field: exchange 20 ETH on 2026-10-05, ledger 2 ETH
> on 2026-10-06. Likely cause: either a booking with several errors or a ledger entry tied to the
> wrong exchange ID; the tool does not guess which. Decision needed: compare against the exchange
> record and correct each field, or correct the reference.
>
> *Rule: CORE-MULTI. Detail: Differs on: QTY_MISMATCH, DATE_WITHIN_WINDOW*

The write-ups come from fixed templates (one per reason code), not from an AI model, so the
same break always produces the same wording.

## How a break is classified

1. **Load and validate** the config and both files.
2. **Convert exchange timestamps from UTC to local dates** (`reporting_timezone`, default
   `America/Puerto_Rico`). A trade at 9 PM in San Juan is already the next day in UTC. In the
   sample data, 31 clean trades would show up as false timing breaks without this step.
3. **Duplicates first.** An exchange ID that appears twice in the export, or an exchange ID
   referenced by two ledger entries, goes to NEEDS_REVIEW (one line per duplicated row).
4. **Pair** each exchange row with the ledger row whose `external_ref` equals its
   `exchange_txn_id`.
5. **Compare** each pair on asset, quantity (within `tolerances.quantity`) and date.
   - No differences: clean, not reported.
   - Only the date differs, by up to `date_window_days`: **TIMING**.
   - Anything else, including rows with no counterpart: check the standing rules. Exactly one
     match: **STANDING**. Two or more: **NEEDS_REVIEW** (ambiguous). None: **NEEDS_REVIEW**
     with the specific reason.

| Reason code | Category | Rule ID |
|---|---|---|
| `DATE_WITHIN_WINDOW` | TIMING | `CORE-TIMING` |
| `STANDING_RULE_MATCH` | STANDING | the `STND-xxx` rule that matched |
| `MISSING_IN_LEDGER` | NEEDS_REVIEW | `CORE-MISSING` |
| `MISSING_ON_EXCHANGE` | NEEDS_REVIEW | `CORE-MISSING` |
| `QTY_MISMATCH` | NEEDS_REVIEW | `CORE-QTY` |
| `ASSET_MISMATCH` | NEEDS_REVIEW | `CORE-ASSET` |
| `DATE_OUTSIDE_WINDOW` | NEEDS_REVIEW | `CORE-DATE` |
| `MULTIPLE_DIFFERENCES` | NEEDS_REVIEW | `CORE-MULTI` |
| `DUPLICATE_EXCHANGE_ID` | NEEDS_REVIEW | `CORE-DUPLICATE` |
| `DUPLICATE_LEDGER_REF` | NEEDS_REVIEW | `CORE-DUPLICATE` |
| `AMBIGUOUS_STANDING_RULE` | NEEDS_REVIEW | `CORE-AMBIGUOUS` |

## Configuration (`config.yaml`)

- `reporting_timezone`: the timezone the ledger books in.
- `tolerances.quantity`: the largest quantity difference treated as equal (default one satoshi, 0.00000001).
- `tolerances.date_window_days`: the largest date gap that still counts as TIMING (default 2 calendar days).
- `standing_rules`: each rule has an `id`, `name`, `description`, `owner`, a `type` and its parameters.

| Rule | Type | Covers |
|---|---|---|
| STND-001 Dust | `unmatched_below_usd` | Exchange-only rows worth under $1.00 |
| STND-002 Staking rewards | `unmatched_txn_type` | Exchange-only `STAKING_REWARD` rows (ledger accrues monthly) |
| STND-003 Fee rounding | `paired_quantity_rounding` | `FEE` pairs whose only difference is ≤ 0.000005 |

Thresholds can be changed in YAML without touching code. A new *kind* of rule needs a new
`type` in `recon.py`, which is deliberate: logic changes go through code review and tests.

## Input files

**Exchange export**: `exchange_txn_id, timestamp_utc, txn_type, asset, quantity, usd_value`

**Ledger**: `ledger_entry_id, external_ref, booking_date, txn_type, asset, quantity, usd_value, memo`

`quantity` is signed: positive means into the account, negative means out. `txn_type` is one of
`TRADE_BUY, TRADE_SELL, DEPOSIT, WITHDRAWAL, FEE, STAKING_REWARD`. A trade is one row for the
crypto leg, priced in USD.

## Testing

`tests/test_recon.py` has 22 tests:

- One small, hand-built test per break type, plus boundary cases: exactly 2 days apart is
  TIMING and 3 days is not; a fee difference too large to be rounding; a transaction that
  fits two standing rules.
- A test that clean rows, including late-evening trades that cross midnight UTC, produce no breaks.
- Bad-input tests: a missing column or a non-numeric value stops the run, and nothing is written.
- An end-to-end test that runs all 200 sample rows and checks every result against the answer
  key `data/planted_breaks.csv`, written by the data generator.

## Project layout

```
recon.py                  the reconciliation CLI
config.yaml               tolerances and standing rules
generate_sample_data.py   synthetic data with planted breaks and an answer key
data/                     sample inputs and the answer key
output/                   breaks.csv and summary.md from the sample run
tests/test_recon.py       pytest suite
notebooks/walkthrough.ipynb   step-by-step walkthrough
RUNBOOK.md                daily procedure for an analyst
```

## Limitations and next steps

- **No memory between runs.** The tool cannot tell that a TIMING break has been open for five
  days. The runbook covers this by archiving each day's output; a next step would be to compare
  against yesterday's `breaks.csv` automatically and promote aged timing breaks to review.
- **Matching needs the exchange reference in the ledger.** Ledger entries without one (for
  example, a month-end accrual) currently stop the run; they need a reference such as
  `ACCRUAL-2026-10`.
- **`txn_type` is not compared**, only asset, quantity and date.
- **One exchange account per run**, and one row per transaction (no multi-leg trades).
- The staking rule is deliberately simple. A stricter version would apply it only to the
  current open month, so last month's rewards without an accrual go to review.

## How this was built

This project was built with AI coding agents (Claude Code) under my direction and review. I
defined the requirements, the break categories and the control principles, approved the design
at each checkpoint, and checked the results against deliberately planted breaks. The code
itself was written by the agent.

## License

MIT. See [LICENSE](LICENSE).
