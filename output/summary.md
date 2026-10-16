# Reconciliation summary

- Run at: 2026-10-15 19:56 AST
- Exchange file: `data/exchange.csv` (200 rows)
- Ledger file: `data/ledger.csv` (182 rows)
- Config: `config.yaml` (quantity tolerance 0.00000001, date window 2 days, timezone America/Puerto_Rico)
- Transaction references compared: 200; clean (fully agree): 166

This report classifies differences only. Nothing has been matched, cleared or booked.

## Breaks by category

| Category | Lines | What to do |
|---|---|---|
| NEEDS_REVIEW | 14 | Investigate and escalate today (write-ups below) |
| TIMING | 3 | Should clear on its own; confirm tomorrow |
| STANDING | 19 | Known items; rule owner monitors |
| **Total** | **36** | |

## Standing items by rule

| Rule | Lines | Total USD (exchange side) | Owner |
|---|---|---|---|
| STND-001 | 2 | $0.87 | Crypto Ops - Fund Accounting |
| STND-002 | 14 | $220.80 | Crypto Ops - Staking |
| STND-003 | 3 | -$57.35 | Crypto Ops - Fund Accounting |

## Timing items

| Break | Reference | Type | Quantity | Exchange date | Ledger date |
|---|---|---|---|---|---|
| BRK-015 | EX-196126 | TRADE_SELL | -340 SOL | 2026-10-13 | 2026-10-14 |
| BRK-016 | EX-389282 | DEPOSIT | 1.25 BTC | 2026-10-03 | 2026-10-04 |
| BRK-017 | EX-989227 | WITHDRAWAL | -12.5 ETH | 2026-10-09 | 2026-10-11 |

## Needs review

14 line(s). Each item below needs a decision; none has been auto-cleared.

### BRK-001: AMBIGUOUS_STANDING_RULE (EX-865054)

EX-865054 (staking reward, 0.00015 ETH) has no counterpart and fits more than one standing rule (see the detail line), so it was not auto-cleared. Likely cause: the standing rules overlap for this kind of item. Decision needed: the rule owners should agree which rule applies and tighten config.yaml so only one matches.

*Rule: CORE-AMBIGUOUS. Detail: Matches more than one standing rule: STND-001, STND-002*

### BRK-002: ASSET_MISMATCH (EX-786516)

EX-786516 is 50,000 USDC on the exchange but is booked as 50,000 USDT in ledger entry LG-000042. Likely cause: the wrong asset code was selected at booking (for example, a similar stablecoin). Decision needed: confirm which asset was actually received and reclass the ledger entry if it is wrong.

*Rule: CORE-ASSET. Detail: Asset differs between exchange and ledger*

### BRK-003: DATE_OUTSIDE_WINDOW (EX-533914)

EX-533914 (trade sell, -0.75 BTC) is dated 2026-10-02 on the exchange and 2026-10-08 in the ledger, 6 days apart, beyond the 2-day timing window. Likely cause: a late booking or an incorrectly dated entry. Decision needed: confirm the correct date and decide whether to re-date the entry, which may affect period-end balances.

*Rule: CORE-DATE. Detail: Dates are 6 days apart, beyond the 2-day window*

### BRK-004, BRK-005: DUPLICATE_EXCHANGE_ID (EX-550961)

The exchange export contains 2 rows with ID EX-550961 (trade buy, 0.4 BTC). Likely cause: an export glitch; less likely, two real transactions sharing an ID. Decision needed: re-pull the export or confirm with the exchange whether there were one or two transactions before booking anything.

*Rule: CORE-DUPLICATE. Detail: 2 exchange rows share this ID*

### BRK-006, BRK-007: DUPLICATE_LEDGER_REF (EX-678116)

Exchange transaction EX-678116 (trade sell, -8 ETH) is referenced by 2 ledger entries: LG-000104, LG-000105. Likely cause: the transaction was booked twice. Decision needed: confirm which entry is valid and reverse the duplicate(s).

*Rule: CORE-DUPLICATE. Detail: 2 ledger entries reference this ID: LG-000104, LG-000105*

### BRK-008: MISSING_IN_LEDGER (EX-499407)

The exchange shows a trade buy of 3.5 ETH ($7,752.54) on 2026-10-10, but no ledger entry references EX-499407. Likely cause: the transaction was not booked, or it was booked without the exchange reference. Decision needed: confirm the transaction with the exchange, then book it or document why it should not be booked.

*Rule: CORE-MISSING. Detail: On the exchange, no ledger entry references it*

### BRK-009: MISSING_IN_LEDGER (EX-713799)

The exchange shows a withdrawal of -25,000 USDC (-$25,000.00) on 2026-10-06, but no ledger entry references EX-713799. Likely cause: the transaction was not booked, or it was booked without the exchange reference. Decision needed: confirm the transaction with the exchange, then book it or document why it should not be booked.

*Rule: CORE-MISSING. Detail: On the exchange, no ledger entry references it*

### BRK-010: MISSING_ON_EXCHANGE (EX-575755)

Ledger entry LG-000075 books a deposit of 0.8 BTC ($48,909.64) on 2026-10-07 against EX-575755, but the exchange export has no such transaction. Likely cause: the entry references the wrong exchange ID, belongs to another account or venue, or was booked in error. Decision needed: find the source document, then correct the reference or reverse the entry.

*Rule: CORE-MISSING. Detail: In the ledger, but the exchange export has no such transaction*

### BRK-011: MULTIPLE_DIFFERENCES (EX-575528)

EX-575528 (deposit) differs on more than one field: exchange 20 ETH on 2026-10-05, ledger 2 ETH on 2026-10-06. Likely cause: either a booking with several errors or a ledger entry tied to the wrong exchange ID; the tool does not guess which. Decision needed: compare against the exchange record and correct each field, or correct the reference.

*Rule: CORE-MULTI. Detail: Differs on: QTY_MISMATCH, DATE_WITHIN_WINDOW*

### BRK-012: QTY_MISMATCH (EX-427704)

EX-427704 (fee, BTC) is -0.00051234 on the exchange but -0.00041234 in the ledger, a difference of -0.0001. Likely cause: a keying error, a partial fill, or a fee netted into the amount. Decision needed: confirm the correct quantity from the exchange record and decide whether to correct the ledger entry.

*Rule: CORE-QTY. Detail: Quantity differs beyond tolerance*

### BRK-013: QTY_MISMATCH (EX-630108)

EX-630108 (trade buy, SOL) is 120.5 on the exchange but 125 in the ledger, a difference of -4.5. Likely cause: a keying error, a partial fill, or a fee netted into the amount. Decision needed: confirm the correct quantity from the exchange record and decide whether to correct the ledger entry.

*Rule: CORE-QTY. Detail: Quantity differs beyond tolerance*

### BRK-014: QTY_MISMATCH (EX-931621)

EX-931621 (deposit, USDC) is 10,000 on the exchange but -10,000 in the ledger, a difference of 20,000. Likely cause: the entry was booked in the wrong direction (sign flipped). Decision needed: confirm the correct quantity from the exchange record and decide whether to correct the ledger entry.

*Rule: CORE-QTY. Detail: Quantity differs beyond tolerance*
