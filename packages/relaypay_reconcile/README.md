# relaypay-reconcile

A standalone seller reconciliation CLI. It matches a payment provider's settlement export against
the seller's own order export and reports every disagreement as one of nine exception kinds.

- **Stdlib only.** Its only runtime dependency is the Python standard library. It opens no sockets
  and imports nothing from `relaypay`. The tests enforce both rules.
- **Exact money.** Amounts are integer paise. Rupee text is parsed with `Decimal`, never floats.
- **Streamed.** Both CSVs are read one row at a time into SQLite, in batches of 5,000 rows. The
  matching is SQL, and the reports are written row by row from a cursor. No CSV and no report is
  held in a Python list.

## Invocation

From the repository root, after `uv sync --frozen`:

```bash
uv run relaypay-reconcile \
  --settlement examples/reconcile/settlement.csv \
  --orders examples/reconcile/orders.csv \
  --out-dir reconcile-out
```

Or, without `uv`: `python -m relaypay_reconcile --settlement ... --orders ...`.

| Option | Meaning |
|---|---|
| `--settlement PATH` | Settlement export (CSV). Required. |
| `--orders PATH` | Seller order export (CSV). Required. |
| `--format {generic,razorpay,cashfree}` | Settlement preset. Default `generic`. Cannot be combined with `--mapping`. |
| `--mapping PATH` | Mapping JSON that sets the columns, units and rules. See below. |
| `--out-dir DIR` | Where reports are written. Default `reconcile-out`. A report name that is a link is refused (see Reports). |
| `--report csv,json,md` | Which reports to write. Default is all three. |
| `--timing-window MIN:MAX` | Allowed days from the order date to the settlement date. Default `0:2`. Use `--timing-window=-1:2` for a negative minimum, because argparse reads a leading `-` as a flag. |
| `--tolerance-paise N` | Largest allowed difference, in paise, for amount, fee and tax. Default `0`. |

**Exit status:** `0` nothing found. `1` at least one finding. `2` input, option, output or storage
error. The message goes to stderr. For an input error nothing is reconciled, and for a refused
output name no report is written.

**Reports** are written to `--out-dir` under fixed names: `findings.csv`, `findings.json` and
`report.md`. The output is deterministic. The same inputs give byte-identical reports, whatever
the order of the input rows.

## Input files

Both files are UTF-8 CSV, with a header row. A byte-order mark is allowed. Column names are
matched after trimming whitespace, and duplicate header names are rejected. Each row must have
the same number of fields as the header. Blank lines are rejected. Row parsing errors include
the file and physical line number; header and missing-column errors identify the file.

Amounts are non-negative. A refund is its own row with a refund type, never a negative payment.

| Field | Rule |
|---|---|
| reference | Non-empty. Compared exactly, case-sensitively, after trimming. |
| amount | `rupees`: digits with at most two decimals, for example `1234.5`. `paise`: whole digits only. Anything else is an error. |
| fee, tax | Optional. A blank cell means "not known", and that row is not compared. |
| date | Optional. Any ISO 8601 date or datetime. Only the calendar date is used (see TIMING). |
| status | Optional. When mapped, every value must be a settled value or a failed value (see below). |
| kind | Optional. Maps the type column to `payment`, `refund`, or `ignore`. |

### Status values

When a status column is mapped, its value must be in `settled_values` or `failed_values`. The
default `settled_values` are `captured`, `processed` and `settled`. The default `failed_values`
is `failed`. Matching ignores case. Any other value, including `pending` and a blank cell, stops
the load with its line number.

This is deliberate. A row that has not completed is not reconciled as if it had. Export only
completed rows, or list your statuses in the mapping. A settlement row counts as landed only when
its status is settled. If the settlement has no status column, every row counts as landed, and
the report says so under "Not evaluated".

### Presets

Presets describe the settlement file only. The seller's orders file always uses the generic
layout, unless a mapping overrides it.

| Preset | Basis | Verification status |
|---|---|---|
| `generic` | This document: `reference,type,amount,fee,tax,date,status`, amounts in rupees. | Defined here. It is the layout the examples use. |
| `razorpay` | Field names from Razorpay's settlement API docs: `entity_id`, `type` (`adjustment` is ignored), `amount`, `fee`, `tax`, `settled_at`, amounts in paise. | **Not verified** against a real CSV export. The export headers were not found in the public docs. The preset assumes every row is settled. |
| `cashfree` | Placeholder column names (`cashfree_payment_id`, `service_charge`, `gst`). | **Not verified, and not a Cashfree export format.** The public docs name the concepts but not the CSV headers or units. Use a mapping file that matches the export you actually receive. |

Verify any preset against one real export before you rely on it. Start with a copy of your
export's header row, and set the mapping to match it.

### Mapping JSON

```json
{
  "format": "razorpay",
  "settlement": {"amount": "gross_paise"},
  "orders": {"ref": "order_reference", "unit": "rupees", "fee": null, "tax": null},
  "settled_values": ["captured", "settled"],
  "failed_values": ["failed"],
  "timing_window_days": [0, 2],
  "tolerance_paise": 0
}
```

- `format` (optional) names a preset that the `settlement` object overrides key by key.
- `settlement` and `orders` each accept: `ref`, `amount`, `unit` (`paise` or `rupees`), `fee`,
  `tax`, `date`, `status`, `kind`. Set a column to `null` to say it is not mapped. A mapped
  column must exist in the header.
- `kind` is `{"column": "type", "payment": [...], "refund": [...], "ignore": [...]}`. Every
  listed value is compared case-insensitively. A type not listed in any of the three is an error,
  so an unexpected row type never passes silently. Refund checks need a kind column on both sides.
- Unknown keys are rejected.
- The mapping file is read as UTF-8 and may be at most **262,144 bytes (256 KiB)**. A larger file,
  invalid UTF-8, or JSON nested too deeply for the parser exits with status 2 and a message that
  names the file. The limit applies to the mapping only. CSV inputs have no size limit.

## The nine exception kinds

Each finding has an exception name, a kind (`payment` or `refund`), the reference, a side
(`orders`, `settlement`, or `both`), expected and actual paise, the difference, and a detail.

| Kind | Found when |
|---|---|
| `MISSING_IN_SETTLEMENT` | A payment order that is not failed has no landed settlement row for its reference. |
| `MISSING_IN_ORDERS` | A settlement row has no order with the same reference. |
| `AMOUNT_MISMATCH` | Both sides have one row, and the amounts differ by more than the tolerance. |
| `DUPLICATE` | A reference appears more than once on one side. The pair checks (amount, fee, tax, timing, refund and failed-debit) are skipped for that reference. A missing-side finding can still apply. |
| `FEE_DIFFERENCE` | Both sides carry a fee, the order is not failed, and the fees differ by more than the tolerance. |
| `GST_DIFFERENCE` | As above, for tax. |
| `TIMING` | A payment settles outside the window. The window is measured in calendar days between the two dates as written, so 23:59 on day 1 and 00:01 on day 3 are two days apart. |
| `REFUND_NOT_LANDED` | A refund in the orders has no landed refund row in the settlement. |
| `FAILED_BUT_DEBITED` | An order is failed, but the settlement has a landed payment for it. |

A failed order with no settlement row is expected, and it is not reported. A settlement row that
is failed does not count as landed.

## Reports

- `findings.csv`: one row per finding, with a fixed header.
- `findings.json`: `{"summary": {...}, "findings": [...]}`. The summary has row counts, counts for
  all nine kinds, and the list of checks that were not evaluated.
- `report.md`: the summary and the findings table. Money is in paise. The findings table is
  streamed row by row, so a large run does not hold it in memory. In the table, each cell escapes
  `|`, `&`, `<` and `>`, so a reference cannot split a column or start raw HTML. The CSV and JSON
  reports keep each reference exactly as read.

Ordering is by exception name, then kind, reference and side. Output is UTF-8, with `\n` line
endings.

Each report is written to an exclusively created private temporary file in `--out-dir`, synced,
and then replaced over its name. If rendering or replacement fails, the previous report stays in
place and the temporary file is removed. Reports already replaced earlier in a run remain replaced.
A process
killed outright (SIGKILL) cannot clean up, so it can leave a hidden `.<name>.*.tmp` file behind.
Before anything is written, every
requested name is checked. If one is a symbolic link (even a dangling one), a hard link to another
file, or not a regular file, the run exits with status 2 and writes no report. Where supported,
names are resolved against the output directory's open descriptor. Elsewhere, path-based checks
and replacement are used; the operator must control and trust the output directory and its parent
paths, since concurrent changes can race those checks. The `--out-dir` path itself may be a
symlink, and the directory it names is used. A regular file from an earlier run is replaced.
On POSIX the final report inherits the temporary file's `0600` mode, including on reruns; on
other platforms access follows the platform's temporary-file permissions and ACLs.

## Limits

- No network access, no authentication and no real PSP API call. The input is files only.
- The presets are not verified against real exports (see above).
- The tool does not convert timezones. Dates are compared as written, so mixing offsets in one
  file can shift a TIMING result by a day.
- CSV cells are written as they were read. A reference that starts with `=` can be treated as a
  formula when the CSV is opened in a spreadsheet. Treat the reports as data, not as a sheet.
  The Markdown table escapes raw HTML, but it does not escape `[`, `]` or `!`, so a reference that
  holds Markdown link or image syntax can render as one in some viewers.
- Each report is synced to disk before its rename, so a large run pays for that sync.
- A crash that is not one of the handled errors still produces a traceback. Its exit status is
  Python's default, 1, which is also the findings status. Read stderr before trusting the code.
- Refunds and fee reversals are matched by reference only, one row per reference and side.
- The matching is set-based and runs in SQLite. Peak memory was measured on one 2,000,000-row pair
  (see Measurements). The figure is for that workload on one machine, not a general guarantee.

## Tests

- `tests/unit/reconcile/` covers money, ingest, mapping, matching for all nine kinds, the CLI,
  the sample dataset, and the stdlib-only constraint.
- `tests/unit/reconcile/test_reconcile_memory_proof.py` (marker `proof`) generates a 2,000,000-row
  pair in a scratch directory that is deleted afterwards, runs the CLI in a child process, and
  asserts the child's peak RSS is at most 128 MiB. It takes about 3 minutes. It runs in the proof
  gate. CI's release gate deselects it, but the default `make test` and `make unit` do not, so use
  `-m 'not proof'` for a fast local run.
- The sample in `examples/reconcile/` seeds one case of every kind. `expected_findings.json`
  lists exactly what must be found, and the test asserts it.

## Measurements

Measured on macOS 27 (Darwin 27.0, arm64), Python 3.12.12, SQLite through the stdlib `sqlite3`
module. The machine was shared with other CI work while these runs happened, so wall-clock times
are inflated and will vary.

| Run | Rows (settlement / orders) | Result | Wall | User / sys | Peak RSS |
|---|---|---|---|---|---|
| CLI, `/usr/bin/time -l` | 1,999,600 / 2,000,000 | exit 1, 2,400 findings (400 MISSING_IN_SETTLEMENT, 2,000 AMOUNT_MISMATCH) | 430.4 s | 57.7 s / 17.4 s | 41,730,048 bytes (about 40 MiB) |
| Proof test (`-m proof`) | same workload | passed; peak asserted at most 134,217,728 bytes | 189.9 s (test call) | not recorded | not printed by the test |

The `ru_maxrss` value is bytes on macOS and kilobytes on Linux. The proof test converts it. The
bound of 128 MiB is about three times the measured figure, to allow for other platforms and
allocator behaviour.

The wall time is much larger than the user CPU time. That points to disk waits on a nearly full
volume, and to contention with other work. Re-measure on an idle machine before quoting a speed.
