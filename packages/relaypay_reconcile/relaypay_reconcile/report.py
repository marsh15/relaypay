"""Reports are streamed row by row from the findings table as CSV, JSON or Markdown.

Ordering is fixed by the query (exception, kind, reference, side), so the same inputs always give
byte-identical reports. Money is written as integer paise.

Each report is written to a private temporary file in the output directory and then renamed over
its name, so an interrupted run leaves the previous report in place. Names are resolved against the
output directory's open descriptor where supported. A report name that is a symbolic link, a hard
link, or not a regular file is refused rather than written through. The operator must control the
output directory, especially when descriptor-relative operations are unavailable.
"""

import csv
import json
import os
import secrets
import sqlite3
import stat
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Self, TextIO

from relaypay_reconcile.errors import ReconcileInputError
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
    with _OutputDirectory(out_dir) as target:
        # Refuse every requested name before the first write, so a refusal leaves no partial set.
        for name in formats:
            target.check_free(REPORT_FILES[name])
        written: list[Path] = []
        for name in formats:
            file_name = REPORT_FILES[name]
            if name == "csv":
                written.append(target.write(file_name, lambda h: _write_csv(conn, h), newline=""))
            elif name == "json":
                written.append(target.write(file_name, lambda h: _write_json(conn, h, summary)))
            else:
                written.append(target.write(file_name, lambda h: _write_markdown(conn, h, summary)))
    return written


def _supports_dir_fd() -> bool:
    return (
        os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.unlink in os.supports_dir_fd
        and os.rename in os.supports_dir_fd
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    )


class _OutputDirectory:
    """Write reports within an operator-controlled directory."""

    def __init__(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._descriptor = (
            os.open(path, os.O_RDONLY | os.O_DIRECTORY) if _supports_dir_fd() else None
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if self._descriptor is not None:
            os.close(self._descriptor)

    def check_free(self, name: str) -> None:
        """Refuse a report name that is a link, is not a regular file, or shares its file."""
        try:
            status = (
                os.stat(name, dir_fd=self._descriptor, follow_symlinks=False)
                if self._descriptor is not None
                else (self.path / name).lstat()
            )
        except FileNotFoundError:
            return
        where = self.path / name
        if stat.S_ISLNK(status.st_mode):
            raise ReconcileInputError(
                f"{where} is a symbolic link; remove it or choose another --out-dir"
            )
        if not stat.S_ISREG(status.st_mode):
            raise ReconcileInputError(
                f"{where} is not a regular file; remove it or choose another --out-dir"
            )
        if status.st_nlink > 1:
            raise ReconcileInputError(
                f"{where} is hard-linked to another file; remove the extra link or choose "
                "another --out-dir"
            )

    def write(
        self, name: str, render: Callable[[TextIO], None], newline: str | None = None
    ) -> Path:
        """Render a private temporary file beside the report, then rename it over its name."""
        self.check_free(name)
        if self._descriptor is None:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".tmp", dir=self.path
            )
        else:
            temporary = f".{name}.{secrets.token_hex(8)}.tmp"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=self._descriptor,
            )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline=newline) as handle:
                render(handle)
                handle.flush()
                os.fsync(handle.fileno())
            self.check_free(name)
            if self._descriptor is None:
                os.replace(temporary, self.path / name)
            else:
                os.replace(
                    temporary, name, src_dir_fd=self._descriptor, dst_dir_fd=self._descriptor
                )
        except BaseException:
            with suppress(OSError):
                if self._descriptor is None:
                    os.unlink(temporary)
                else:
                    os.unlink(temporary, dir_fd=self._descriptor)
            raise
        return self.path / name


def _write_csv(conn: sqlite3.Connection, handle: TextIO) -> None:
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(_COLUMNS)
    writer.writerows(conn.execute(_FINDINGS_QUERY))


def _write_json(conn: sqlite3.Connection, handle: TextIO, summary: RunSummary) -> None:
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


def _write_markdown(conn: sqlite3.Connection, handle: TextIO, summary: RunSummary) -> None:
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
    """Escape one value for a Markdown table cell: no column split, no raw HTML, no line break.

    The CSV and JSON reports keep the reference exactly as read. Only this Markdown view escapes.
    """
    if value is None:
        return ""
    text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
