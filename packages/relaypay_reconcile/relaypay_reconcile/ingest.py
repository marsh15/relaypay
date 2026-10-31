"""Streaming ingest: reads one CSV row at a time and writes it to SQLite in fixed-size batches."""

import csv
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, TextIO

from relaypay_reconcile.errors import ReconcileInputError
from relaypay_reconcile.mapping import KindRule, Mapping, SideMapping
from relaypay_reconcile.money import parse_paise

BATCH_ROWS = 5000

_TABLE_COLUMNS = (
    "kind TEXT NOT NULL, ref TEXT NOT NULL, amount INTEGER NOT NULL, "
    "fee INTEGER, tax INTEGER, day INTEGER, failed INTEGER NOT NULL"
)
CREATE_SETTLEMENT = f"CREATE TABLE settlement ({_TABLE_COLUMNS})"
CREATE_ORDERS = f"CREATE TABLE orders ({_TABLE_COLUMNS})"
INSERT_SETTLEMENT = "INSERT INTO settlement VALUES (?, ?, ?, ?, ?, ?, ?)"
INSERT_ORDERS = "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)"

# kind, ref, amount, fee, tax, day (ordinal date or None), failed (1 when the status is a failure)
type _Row = tuple[str, str, int, int | None, int | None, int | None, int]


class LoadCounts(NamedTuple):
    """Rows written to the table, and rows skipped because their type is in the ignore list."""

    rows: int
    skipped: int


class _Columns(NamedTuple):
    """Header positions for one file. Optional columns are None when the mapping leaves them out."""

    ref: int
    amount: int
    fee: int | None
    tax: int | None
    date: int | None
    status: int | None
    kind: tuple[int, KindRule] | None


def create_tables(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_SETTLEMENT)
    conn.execute(CREATE_ORDERS)


def load_side(
    conn: sqlite3.Connection,
    path: Path,
    side: SideMapping,
    mapping: Mapping,
    insert_sql: str,
) -> LoadCounts:
    """Stream one CSV file into a table. Any malformed row stops the load with its line number."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return _load_rows(handle, conn, path, side, mapping, insert_sql)


def _load_rows(
    handle: TextIO,
    conn: sqlite3.Connection,
    path: Path,
    side: SideMapping,
    mapping: Mapping,
    insert_sql: str,
) -> LoadCounts:
    reader = csv.reader(handle, strict=True)
    batch: list[_Row] = []
    rows = skipped = 0
    try:
        header = [name.strip() for name in next(reader, [])]
        if not header:
            raise ReconcileInputError(f"{path}: the file is empty")
        columns = _resolve_columns(header, side, path)
        for cells in reader:
            where = f"{path}:{reader.line_num}"
            if len(cells) != len(header):
                raise ReconcileInputError(
                    f"{where}: expected {len(header)} fields, found {len(cells)}"
                )
            row = _parse_row([cell.strip() for cell in cells], columns, mapping, where, side)
            if row is None:
                skipped += 1
                continue
            batch.append(row)
            rows += 1
            if len(batch) == BATCH_ROWS:
                conn.executemany(insert_sql, batch)
                batch.clear()
    except (csv.Error, UnicodeDecodeError) as error:
        raise ReconcileInputError(f"{path}:{reader.line_num}: {error}") from error
    conn.executemany(insert_sql, batch)
    return LoadCounts(rows=rows, skipped=skipped)


def _resolve_columns(header: list[str], side: SideMapping, path: Path) -> _Columns:
    duplicates = sorted({name for name in header if header.count(name) > 1})
    if duplicates:
        raise ReconcileInputError(f"{path}: duplicate header columns: {', '.join(duplicates)}")
    kind: tuple[int, KindRule] | None = None
    if side.kind is not None:
        kind = (_index(header, side.kind.column, path), side.kind)
    return _Columns(
        ref=_index(header, side.ref, path),
        amount=_index(header, side.amount, path),
        fee=_optional_index(header, side.fee, path),
        tax=_optional_index(header, side.tax, path),
        date=_optional_index(header, side.date, path),
        status=_optional_index(header, side.status, path),
        kind=kind,
    )


def _index(header: list[str], column: str, path: Path) -> int:
    if column not in header:
        raise ReconcileInputError(
            f"{path}: no column named {column!r}; the columns are: {', '.join(header)}"
        )
    return header.index(column)


def _optional_index(header: list[str], column: str | None, path: Path) -> int | None:
    return None if column is None else _index(header, column, path)


def _parse_row(
    cells: list[str],
    columns: _Columns,
    mapping: Mapping,
    where: str,
    side: SideMapping,
) -> _Row | None:
    ref = cells[columns.ref]
    if not ref:
        raise ReconcileInputError(f"{where}: the reference is empty")
    if columns.kind is None:
        kind = "payment"
    else:
        position, rule = columns.kind
        mapped = _kind_of(cells[position], rule, where)
        if mapped is None:
            return None
        kind = mapped
    amount = _amount(cells[columns.amount], side.unit, where)
    fee = _optional_amount(cells, columns.fee, side.unit, where)
    tax = _optional_amount(cells, columns.tax, side.unit, where)
    day = _optional_day(cells, columns.date, where)
    failed = 0 if columns.status is None else _status_flag(cells[columns.status], mapping, where)
    return (kind, ref, amount, fee, tax, day, failed)


def _kind_of(text: str, rule: KindRule, where: str) -> str | None:
    value = text.lower()
    if value in rule.payment:
        return "payment"
    if value in rule.refund:
        return "refund"
    if value in rule.ignore:
        return None
    raise ReconcileInputError(f"{where}: type {text!r} is not listed as payment, refund or ignore")


def _status_flag(text: str, mapping: Mapping, where: str) -> int:
    """Return 1 for a failed status and 0 for a settled one. Anything else is an input error."""
    value = text.lower()
    if value in mapping.failed_values:
        return 1
    if value in mapping.settled_values:
        return 0
    raise ReconcileInputError(
        f"{where}: status {text!r} is neither settled nor failed; export only completed rows, "
        "or list the status in settled_values or failed_values"
    )


def _amount(text: str, unit: str, where: str) -> int:
    try:
        return parse_paise(text, unit)
    except ReconcileInputError as error:
        raise ReconcileInputError(f"{where}: {error}") from error


def _optional_amount(cells: list[str], position: int | None, unit: str, where: str) -> int | None:
    if position is None or not cells[position]:
        return None
    return _amount(cells[position], unit, where)


def _optional_day(cells: list[str], position: int | None, where: str) -> int | None:
    if position is None or not cells[position]:
        return None
    try:
        return datetime.fromisoformat(cells[position]).date().toordinal()
    except ValueError as error:
        raise ReconcileInputError(f"{where}: date {cells[position]!r} is not ISO 8601") from error
