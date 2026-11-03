"""Streaming ingest: exact rows go in, and malformed input stops the load with a line number."""

import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from relaypay_reconcile import ingest
from relaypay_reconcile.errors import ReconcileInputError
from relaypay_reconcile.mapping import Mapping, load_mapping

HEADER = "reference,type,amount,fee,tax,date,status"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect("", isolation_level=None)
    ingest.create_tables(connection)
    yield connection
    connection.close()


@pytest.fixture
def generic() -> Mapping:
    return load_mapping(None, None)


def _write(tmp_path: Path, text: str, name: str = "settlement.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))
    return path


def _load(conn: sqlite3.Connection, path: Path, mapping: Mapping) -> ingest.LoadCounts:
    return ingest.load_side(conn, path, mapping.settlement, mapping, ingest.INSERT_SETTLEMENT)


def _rows(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return conn.execute("SELECT * FROM settlement ORDER BY ref").fetchall()


def test_rows_load_with_paise_fees_tax_and_ordinal_dates(
    conn: sqlite3.Connection, tmp_path: Path, generic: Mapping
) -> None:
    path = _write(
        tmp_path,
        f"{HEADER}\npay_1,payment,199.99,2.36,0.42,2026-10-01T10:00:00+05:30,captured\n"
        "pay_2,payment,10,,,,captured\n",
    )
    counts = _load(conn, path, generic)
    assert counts == ingest.LoadCounts(rows=2, skipped=0)
    assert _rows(conn) == [
        ("payment", "pay_1", 19999, 236, 42, date(2026, 10, 1).toordinal(), 0),
        ("payment", "pay_2", 1000, None, None, None, 0),
    ]


def test_failed_status_is_flagged_case_insensitively(
    conn: sqlite3.Connection, tmp_path: Path, generic: Mapping
) -> None:
    path = _write(
        tmp_path, f"{HEADER}\npay_1,payment,1.00,,,,FAILED\npay_2,payment,1.00,,,,captured\n"
    )
    _load(conn, path, generic)
    assert [row[-1] for row in _rows(conn)] == [1, 0]


def test_rows_with_an_ignored_type_are_skipped_and_counted(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    mapping = load_mapping(None, "razorpay")
    path = _write(
        tmp_path,
        "entity_id,type,amount,fee,tax,settled_at\n"
        "pay_1,payment,100,,,\nadj_1,adjustment,500,,,\nrfnd_1,refund,40,,,\n",
    )
    counts = _load(conn, path, mapping)
    assert counts == ingest.LoadCounts(rows=2, skipped=1)
    assert [row[1] for row in _rows(conn)] == ["pay_1", "rfnd_1"]
    assert [row[0] for row in _rows(conn)] == ["payment", "refund"]


def test_more_rows_than_one_batch_all_load(
    conn: sqlite3.Connection, tmp_path: Path, generic: Mapping
) -> None:
    lines = [f"{HEADER}"] + [f"pay_{n:06d},payment,1.00,,,,captured" for n in range(12_000)]
    counts = _load(conn, _write(tmp_path, "\n".join(lines) + "\n"), generic)
    assert counts.rows == 12_000
    assert conn.execute("SELECT COUNT(*) FROM settlement").fetchone() == (12_000,)


def test_utf8_byte_order_mark_is_accepted(
    conn: sqlite3.Connection, tmp_path: Path, generic: Mapping
) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes(("\ufeff" + f"{HEADER}\npay_1,payment,1.00,,,,captured\n").encode("utf-8"))
    assert _load(conn, path, generic).rows == 1


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "the file is empty"),
        ("reference,type,fee\npay_1,payment,1\n", "no column named 'amount'"),
        (f"{HEADER},reference\npay_1,payment,1.00,,,,,pay_1\n", "duplicate header"),
        (f"{HEADER}\n,payment,1.00,,,,\n", "reference is empty"),
        (f"{HEADER}\npay_1,payment,1.005,,,,\n", "not rupees with at most 2 decimals"),
        (f"{HEADER}\npay_1,payment,1.00,,,\n", "expected 7 fields, found 6"),
        (f"{HEADER}\npay_1,capture,1.00,,,,\n", "type 'capture' is not listed"),
        (f"{HEADER}\npay_1,payment,1.00,,,not-a-date,\n", "is not ISO 8601"),
        (
            f"{HEADER}\npay_1,payment,1.00,,,,captured\n\npay_2,payment,1.00,,,,captured\n",
            "expected 7 fields",
        ),
        (f'{HEADER}\n"pay_1,payment,1.00,,,,\n', "unexpected end of data"),
    ],
)
def test_malformed_input_is_rejected_with_its_line(
    conn: sqlite3.Connection, tmp_path: Path, message: str, body: str
) -> None:
    mapping = load_mapping(None, None)
    path = _write(tmp_path, body)
    with pytest.raises(ReconcileInputError, match=message.replace("(", r"\(")):
        _load(conn, path, mapping)


def test_bad_line_number_points_at_the_offending_row(
    conn: sqlite3.Connection, tmp_path: Path, generic: Mapping
) -> None:
    path = _write(
        tmp_path, f"{HEADER}\npay_1,payment,1.00,,,,captured\npay_2,payment,x,,,,captured\n"
    )
    with pytest.raises(ReconcileInputError, match=r"settlement\.csv:3: amount 'x'"):
        _load(conn, path, generic)


def test_bytes_that_are_not_utf8_are_rejected(
    conn: sqlite3.Connection, tmp_path: Path, generic: Mapping
) -> None:
    path = tmp_path / "latin.csv"
    path.write_bytes(f"{HEADER}\npay_\xff,payment,1.00,,,,\n".encode("latin-1"))
    with pytest.raises(ReconcileInputError, match="utf-8"):
        _load(conn, path, generic)


@pytest.mark.parametrize("status", ["pending", "", "partially_refunded"])
def test_a_status_that_is_neither_settled_nor_failed_is_rejected_with_its_line(
    conn: sqlite3.Connection, tmp_path: Path, generic: Mapping, status: str
) -> None:
    path = _write(
        tmp_path, f"{HEADER}\npay_1,payment,1.00,,,,captured\npay_2,payment,1.00,,,,{status}\n"
    )
    with pytest.raises(ReconcileInputError, match=r"settlement\.csv:3: status .* neither settled"):
        _load(conn, path, generic)


def test_mapping_settled_values_decide_which_statuses_land(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps({"settled_values": ["paid"], "failed_values": ["void"]}), encoding="utf-8"
    )
    mapping = load_mapping(mapping_path, None)
    path = _write(tmp_path, f"{HEADER}\npay_1,payment,1.00,,,,PAID\npay_2,payment,1.00,,,,void\n")
    assert _load(conn, path, mapping).rows == 2
    assert [row[-1] for row in _rows(conn)] == [0, 1]


def test_every_duplicate_header_is_named_in_one_message(
    conn: sqlite3.Connection, tmp_path: Path, generic: Mapping
) -> None:
    # A wide header with several repeated names: all of them are reported, sorted, in one pass.
    filler = [f"extra_{n}" for n in range(5_000)]
    header = ",".join(["reference", "type", "amount", "reference", "amount", *filler])
    path = _write(tmp_path, header + "\n")
    with pytest.raises(ReconcileInputError, match=r"duplicate header columns: amount, reference$"):
        _load(conn, path, generic)
