"""Reports are streamed row by row from the findings table as CSV, JSON or Markdown.

Ordering is fixed by the query (exception, kind, reference, side), so the same inputs always give
byte-identical reports. Money is written as integer paise.
"""

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from relaypay_reconcile.ingest import LoadCounts
from relaypay_reconcile.kinds import ExceptionKind
from relaypay_reconcile.mapping import Mapping

REPORT_FILES = {"csv": "findings.csv", "json": "findings.json", "md": "report.md"}
REPORT_FORMATS = tuple(REPORT_FILES)
_COLUMNS = (
    "exception",
    "kind",
    "reference",
    "side",
    "expected_paise",
    "actual_paise",
    "difference_paise",
    "detail",
)
_FINDINGS_QUERY = """
SELECT exception, kind, ref, side, expected, actual,
       CASE WHEN expected IS NOT NULL AND actual IS NOT NULL THEN actual - expected END,
       detail
FROM findings
ORDER BY exception, kind, ref, side
"""


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Row counts, finding counts for all nine kinds, and the checks this mapping cannot run."""

    settlement: LoadCounts
    orders: LoadCounts
    counts: dict[ExceptionKind, int]
    coverage: tuple[str, ...]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def summarize(
    conn: sqlite3.Connection,
    settlement: LoadCounts,
    orders: LoadCounts,
    mapping: Mapping,
) -> RunSummary:
    counts = dict.fromkeys(ExceptionKind, 0)
    for name, total in conn.execute("SELECT exception, COUNT(*) FROM findings GROUP BY exception"):
        counts[ExceptionKind[name]] = total
    return RunSummary(settlement, orders, counts, _coverage(mapping))


def _coverage(mapping: Mapping) -> tuple[str, ...]:
    notes: list[str] = []
    if mapping.settlement.fee is None or mapping.orders.fee is None:
        notes.append("FEE_DIFFERENCE not evaluated: fee is not mapped on both sides")
    if mapping.settlement.tax is None or mapping.orders.tax is None:
        notes.append("GST_DIFFERENCE not evaluated: tax is not mapped on both sides")
    if mapping.settlement.date is None or mapping.orders.date is None:
        notes.append("TIMING not evaluated: date is not mapped on both sides")
    if mapping.orders.status is None:
        notes.append("FAILED_BUT_DEBITED not evaluated: the orders have no status column")
    if mapping.settlement.status is None:
        notes.append(
            "the settlement has no status column, so every settlement row counts as landed"
        )
    if mapping.settlement.kind is None or mapping.orders.kind is None:
        notes.append("refund checks not evaluated: a kind column is needed on both sides")
    return tuple(notes)


def write_reports(
    conn: sqlite3.Connection, out_dir: Path, formats: tuple[str, ...], summary: RunSummary
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in formats:
        path = out_dir / REPORT_FILES[name]
        if name == "csv":
            _write_csv(conn, path)
        elif name == "json":
            _write_json(conn, path, summary)
        else:
            _write_markdown(conn, path, summary)
        written.append(path)
    return written


def _write_csv(conn: sqlite3.Connection, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(_COLUMNS)
        writer.writerows(conn.execute(_FINDINGS_QUERY))


def _write_json(conn: sqlite3.Connection, path: Path, summary: RunSummary) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f'{{\n"summary": {json.dumps(_summary_document(summary), sort_keys=True)},\n')
        handle.write('"findings": [')
        for index, row in enumerate(conn.execute(_FINDINGS_QUERY)):
            separator = "" if index == 0 else ","
            record = json.dumps(dict(zip(_COLUMNS, row, strict=True)), sort_keys=True)
            handle.write(f"{separator}\n{record}")
        handle.write("\n]\n}\n")


def _summary_document(summary: RunSummary) -> dict[str, int | list[str] | dict[str, int]]:
    return {
        "settlement_rows": summary.settlement.rows,
        "settlement_skipped": summary.settlement.skipped,
        "order_rows": summary.orders.rows,
        "order_skipped": summary.orders.skipped,
        "findings": {kind.name: count for kind, count in summary.counts.items()},
        "total": summary.total,
        "not_evaluated": list(summary.coverage),
    }


def _write_markdown(conn: sqlite3.Connection, path: Path, summary: RunSummary) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write("# Reconciliation report\n\n")
        handle.write(f"- Settlement rows: {summary.settlement.rows} ")
        handle.write(f"({summary.settlement.skipped} skipped by type)\n")
        handle.write(f"- Order rows: {summary.orders.rows} ({summary.orders.skipped} skipped)\n")
        handle.write(f"- Findings: {summary.total}\n\n")
        handle.write("## Exceptions\n\n| Exception | Count | Meaning |\n|---|---:|---|\n")
        for kind, count in summary.counts.items():
            handle.write(f"| {kind.name} | {count} | {_cell(kind.value)} |\n")
        if summary.coverage:
            handle.write("\n## Not evaluated\n\n")
            handle.write("".join(f"- {note}\n" for note in summary.coverage))
        handle.write("\n## Findings\n\nMoney is in paise; 100 paise is one rupee.\n\n")
        handle.write("| " + " | ".join(_COLUMNS) + " |\n|" + "---|" * len(_COLUMNS) + "\n")
        for row in conn.execute(_FINDINGS_QUERY):
            handle.write("| " + " | ".join(_cell(value) for value in row) + " |\n")


def _cell(value: str | int | None) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")
