# Runbook: daily exchange-to-ledger reconciliation

**Who this is for:** an operations analyst running the reconciliation for the first time, with
no help available.
**When:** every business morning, before **[cut-off time, set by team]**.
**Time needed:** about 10 minutes to run, plus investigation time for NEEDS_REVIEW items.

> Items in **[square brackets]** are team-specific (names, times, thresholds). Fill them in
> before using this runbook for real work. The sample data in this repository is synthetic.

---

## 1. One-time setup

Do this once per computer.

1. Install Python 3.11 or newer from python.org.
2. Open a terminal (PowerShell on Windows) in the project folder.
3. Create the environment and install the libraries:
   ```
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   On macOS or Linux, the second line is `source .venv/bin/activate`.
4. Check it works: run `python -m pytest`. Every test must pass. **If any test fails, do not
   use the tool.** Go to section 6.

---

## 2. Daily procedure

### Step 1: Get the two input files

| File | Source | Save as |
|---|---|---|
| Exchange export | **[exchange portal / API job]**, covering **[period, e.g. the previous business day plus the 2 days before]** | `data/exchange.csv` |
| Ledger extract | **[ledger system saved search]**, same period | `data/ledger.csv` |

Before running, open each file and check:

- [ ] Both files cover the **same date range**. A mismatched range creates false "missing" breaks.
- [ ] Each has a header row and data rows, and the columns match the README ("Input files").
- [ ] Neither file has been edited by hand. **Never edit an input file to make a break go away.**

### Step 2: Activate the environment and run the tool

Run these in the project folder. Write today's date into the output folder name, for example
`output/2026-10-15`:

```
.venv\Scripts\Activate.ps1
python recon.py --exchange data/exchange.csv --ledger data/ledger.csv --config config.yaml --output output/2026-10-15
```

Use a dated folder every day. Yesterday's results are how you spot breaks that are not clearing
(see Step 4).

### Step 3: Confirm the run succeeded

A successful run prints:

```
Compared 200 references: 166 clean, 36 break line(s).
  NEEDS_REVIEW  14
  TIMING        3
  STANDING      19
Wrote output\2026-10-15\breaks.csv and output\2026-10-15\summary.md
```

If you see a line starting with `ERROR:`, stop and go to section 5. **Do not use any files left
over from an earlier run.**

Then open `summary.md` and check the header:

- [ ] **Run at** shows today's date and time.
- [ ] The exchange and ledger files and row counts are the ones you just saved.

### Step 4: Read the summary, in this order

1. **Breaks by category**: get a feel for the day. A sudden jump in any category compared with
   yesterday is itself worth a note.
2. **Needs review**: your main work. See section 3.
3. **Timing items**: compare with yesterday's `summary.md`.
4. **Standing items by rule**: a quick sanity check.

`breaks.csv` has the same breaks, one per line, with every field. Open it in Excel when you need
to filter or sort.

---

## 3. Handling each category

### NEEDS_REVIEW: investigate and escalate the same day

Every NEEDS_REVIEW item needs a person to decide. The tool never clears these.

For each item in the "Needs review" section:

1. Read the three-sentence write-up: what is different, the likely cause, and the decision needed.
2. Investigate using the table below.
3. Record what you found and the decision you are recommending in **[the break log / ticket system]**.
4. Escalate (section 4).

| Reason code | Where to look first | Typical outcome |
|---|---|---|
| `MISSING_IN_LEDGER` | The exchange record for that ID; the ledger, searched by amount and date (it may have been booked without the reference) | Book the entry, or add the missing reference to an existing one |
| `MISSING_ON_EXCHANGE` | The ledger entry's source document; other exchange accounts | Correct the reference, or reverse an entry booked in error |
| `QTY_MISMATCH` | The exchange trade/transfer detail vs. the ledger entry. Check the sign: a deposit booked as a withdrawal shows as double the amount | Correct the ledger quantity or sign |
| `ASSET_MISMATCH` | What actually arrived on the exchange | Reclass the ledger entry to the right asset |
| `DATE_OUTSIDE_WINDOW` | Booking date vs. trade date; whether a period end falls in between | Re-date the entry; flag it if it crosses a month end |
| `MULTIPLE_DIFFERENCES` | Whether the ledger entry is tied to the wrong exchange ID altogether | Correct the reference, or each field |
| `DUPLICATE_LEDGER_REF` | All ledger entries listed in the write-up | Reverse the duplicate entry |
| `DUPLICATE_EXCHANGE_ID` | Re-pull the export; if it repeats, ask the exchange | Confirm whether there was one transaction or two before booking anything |
| `AMBIGUOUS_STANDING_RULE` | The rules named in the detail line | Ask the rule owners to tighten `config.yaml` (section 7) |

Corrections are made **in the ledger system, by someone authorized to post**, never in the CSV
files. After a correction, re-extract the ledger and re-run the tool to confirm the break has
cleared.

### TIMING: no action today, but check it clears

A timing break is the same transaction with matching amounts, booked up to 2 days apart. It
should disappear on its own.

- Compare today's timing list with yesterday's. The tool has no memory between runs, so this
  check is yours.
- If the same reference shows up as TIMING on **[2]** consecutive days, treat it as NEEDS_REVIEW
  and escalate it.
- Once the gap is longer than the window, the tool reports it as `DATE_OUTSIDE_WINDOW` itself.

### STANDING: known items, check that the totals make sense

Standing items match a documented rule in `config.yaml`. Each has an owner.

- **STND-001 Dust:** the total should stay small (cents to a few dollars). A jump means
  something larger is being caught. Tell the owner.
- **STND-002 Staking rewards:** expect roughly one reward per staked asset per day. After the
  month-end accrual is booked, last month's rewards should stop appearing. If they keep
  appearing after **[day 3]** of the new month, escalate.
- **STND-003 Fee rounding:** each difference is tiny by definition. A change in the number of
  lines is normal; a change in the rule threshold is not, unless it was approved.

You do not escalate standing items individually. The owners review them **[weekly / at month-end]**.

---

## 4. Escalation

| What | To whom | By when |
|---|---|---|
| All NEEDS_REVIEW items | **[Ops lead]** | **[same day, by 12:00 PM]** |
| Any missing withdrawal, or any item over **[$ threshold]** | **[Ops lead + Fund Accounting]** | **Immediately** |
| A TIMING item not cleared after **[2]** days | **[Ops lead]** | Same day |
| The tool could not be run | **[Ops lead]** | Before **[cut-off time]** |

Message template (copy the write-up straight from `summary.md`):

```
Subject: Recon break [BRK-xxx] [REASON_CODE] [reference], [date]

Break:            [BRK-xxx], [reason code], rule [rule_id]
Write-up:         [paste the three sentences from summary.md]
What I checked:   [systems/records reviewed]
Recommendation:   [book / correct / reverse / wait, and why]
Decision needed from: [name], by [time]
```

---

## 5. When the tool errors

The tool stops on any input problem and **writes nothing**, so a broken run can never look like
a clean one. Read the line starting with `ERROR:`.

| Error message contains | Meaning | What to do |
|---|---|---|
| `file not found` | The path is wrong, or the file wasn't saved | Check the file name and folder, then re-run |
| `missing column(s)` | The export format changed, or the wrong file was used | Re-pull the export. If the source format really changed, escalate. Do not rename columns by hand |
| `has no rows` | An empty export | Re-pull. If the day truly had no activity, record that and tell **[Ops lead]** |
| `is not a number on CSV line(s) N` | Bad value (text, or a thousands comma such as `1,000.00`) on that line | Open the file and look at that line. Re-export with plain numbers; do not hand-edit the value |
| `is blank on CSV line(s) N` | A missing ID or reference | A blank `external_ref` usually means an entry booked without the exchange reference. Raise it as a break with **[Ops lead]** |
| `not a valid timestamp` / `not a YYYY-MM-DD date` | Date format changed | Re-export in the expected format |
| `Config ...` / `Standing rule ...` | `config.yaml` was edited incorrectly | Undo the last config change (section 7) and re-run |
| Any other error (a Python "Traceback") | A bug | Do not work around it. Save the full message and escalate to **[tool owner]** |

If the tool can't be run before **[cut-off time]**, tell **[Ops lead]** that today's
reconciliation is **not done**. Do not send yesterday's results.

---

## 6. If the tests fail

`python -m pytest` must pass after setup and after any change to `recon.py` or `config.yaml`.
A failing test means the tool might classify breaks wrongly. Do not use it; send the test output
to **[tool owner]**.

---

## 7. Changing the configuration

Changing `config.yaml` changes what gets flagged, so treat it like a control change:

1. Agree the change with the rule owner and **[approver]**.
2. Edit `config.yaml`. Every standing rule needs an `id`, `name`, `description`, `owner` and `type`.
3. Run `python -m pytest`. If a test now fails because the change was intended, the test must be
   updated in the same reviewed change.
4. Commit the change to Git with a message that says why, for example
   `config: raise dust threshold to $2 (approved by [name], [date])`.

---

## 8. Daily sign-off checklist

- [ ] Both input files cover the same period and were not edited
- [ ] The tool ran without errors, and "Run at" shows today
- [ ] Every NEEDS_REVIEW item is investigated, logged and escalated
- [ ] Timing items compared with yesterday; anything stuck is escalated
- [ ] Standing totals look normal
- [ ] Output folder for today is saved: `output/YYYY-MM-DD/`
