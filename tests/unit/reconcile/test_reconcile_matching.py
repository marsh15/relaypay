"""Each reconciliation exception kind is found, and the cases that must stay quiet stay quiet."""

import csv
from pathlib import Path
from uuid import uuid4

import pytest
from relaypay_reconcile.cli import main

HEADER = "reference,type,amount,fee,tax,date,status"


def _run(
    tmp_path: Path, settlement: list[str], orders: list[str], *args: str
) -> tuple[int, list[dict[str, str]]]:
    settlement_path = tmp_path / "settlement.csv"
    orders_path = tmp_path / "orders.csv"
    settlement_path.write_text("\n".join([HEADER, *settlement]) + "\n", encoding="utf-8")
    orders_path.write_text("\n".join([HEADER, *orders]) + "\n", encoding="utf-8")
    out_dir = tmp_path / uuid4().hex
    status = main(
        [
            "--settlement",
            str(settlement_path),
            "--orders",
            str(orders_path),
            "--out-dir",
            str(out_dir),
            "--report",
            "csv",
            *args,
        ]
    )
    with (out_dir / "findings.csv").open(encoding="utf-8", newline="") as handle:
        return status, list(csv.DictReader(handle))


def _kinds(findings: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    return [(row["exception"], row["reference"], row["side"]) for row in findings]


CLEAN_SETTLEMENT = ["pay_1,payment,100.00,2.36,0.42,2026-10-01,captured"]
CLEAN_ORDERS = ["pay_1,payment,100.00,2.36,0.42,2026-10-01,captured"]


def test_matching_rows_produce_no_findings_and_exit_zero(tmp_path: Path) -> None:
    status, findings = _run(tmp_path, CLEAN_SETTLEMENT, CLEAN_ORDERS)
    assert status == 0
    assert findings == []


def test_order_without_a_settlement_row_is_missing_in_settlement(tmp_path: Path) -> None:
    status, findings = _run(
        tmp_path,
        [],
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
    )
    assert status == 1
    assert _kinds(findings) == [("MISSING_IN_SETTLEMENT", "pay_1", "orders")]
    assert findings[0]["expected_paise"] == "10000"
    assert findings[0]["actual_paise"] == ""


def test_failed_order_without_settlement_is_expected_and_not_reported(tmp_path: Path) -> None:
    status, findings = _run(tmp_path, [], ["pay_1,payment,100.00,,,2026-10-01,failed"])
    assert status == 0
    assert findings == []


def test_settlement_row_without_an_order_is_missing_in_orders(tmp_path: Path) -> None:
    status, findings = _run(tmp_path, ["pay_9,payment,5.00,,,2026-10-01,captured"], [])
    assert status == 1
    assert _kinds(findings) == [("MISSING_IN_ORDERS", "pay_9", "settlement")]
    assert findings[0]["actual_paise"] == "500"


def test_amount_difference_is_reported_in_paise(tmp_path: Path) -> None:
    status, findings = _run(
        tmp_path,
        ["pay_1,payment,99.99,,,2026-10-01,captured"],
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
    )
    assert status == 1
    assert _kinds(findings) == [("AMOUNT_MISMATCH", "pay_1", "both")]
    assert findings[0]["expected_paise"] == "10000"
    assert findings[0]["actual_paise"] == "9999"
    assert findings[0]["difference_paise"] == "-1"


def test_amount_difference_within_tolerance_is_not_reported(tmp_path: Path) -> None:
    status, findings = _run(
        tmp_path,
        ["pay_1,payment,99.99,,,2026-10-01,captured"],
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
        "--tolerance-paise",
        "1",
    )
    assert status == 0
    assert findings == []


def test_duplicate_settlement_row_is_reported_once_and_skips_pair_checks(tmp_path: Path) -> None:
    status, findings = _run(
        tmp_path,
        [
            "pay_1,payment,100.00,,,2026-10-01,captured",
            "pay_1,payment,40.00,,,2026-10-01,captured",
        ],
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
    )
    assert status == 1
    assert _kinds(findings) == [("DUPLICATE", "pay_1", "settlement")]
    assert findings[0]["detail"] == "2 settlement rows share this reference"


def test_duplicate_order_row_is_reported_on_the_orders_side(tmp_path: Path) -> None:
    status, findings = _run(
        tmp_path,
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
        [
            "pay_1,payment,100.00,,,2026-10-01,captured",
            "pay_1,payment,100.00,,,2026-10-01,captured",
        ],
    )
    assert status == 1
    assert _kinds(findings) == [("DUPLICATE", "pay_1", "orders")]


def test_fee_difference_is_found_only_when_both_sides_carry_a_fee(tmp_path: Path) -> None:
    settlement = ["pay_1,payment,100.00,2.36,0.42,2026-10-01,captured"]
    orders = ["pay_1,payment,100.00,2.00,0.42,2026-10-01,captured"]
    _, findings = _run(tmp_path, settlement, orders)
    assert _kinds(findings) == [("FEE_DIFFERENCE", "pay_1", "both")]
    _, unmapped = _run(tmp_path, settlement, ["pay_1,payment,100.00,,0.42,2026-10-01,captured"])
    assert unmapped == []


def test_gst_difference_is_reported_separately_from_the_fee(tmp_path: Path) -> None:
    _, findings = _run(
        tmp_path,
        ["pay_1,payment,100.00,2.36,0.43,2026-10-01,captured"],
        ["pay_1,payment,100.00,2.36,0.42,2026-10-01,captured"],
    )
    assert _kinds(findings) == [("GST_DIFFERENCE", "pay_1", "both")]


@pytest.mark.parametrize(
    ("settled", "window", "expected"),
    [
        ("2026-10-03", "0:2", False),
        ("2026-10-03", "0:1", True),
        ("2026-10-04", "0:2", True),
        ("2026-10-01", "0:2", False),
        ("2026-10-04", "0:3", False),
        ("2026-09-30", "0:2", True),
    ],
)
def test_settlement_outside_the_window_is_timing(
    tmp_path: Path, settled: str, window: str, expected: bool
) -> None:
    _, findings = _run(
        tmp_path,
        [f"pay_1,payment,100.00,,,{settled},captured"],
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
        "--timing-window",
        window,
    )
    assert _kinds(findings) == ([("TIMING", "pay_1", "both")] if expected else [])


def test_timing_counts_calendar_days_not_elapsed_hours(tmp_path: Path) -> None:
    # 23:59 on the 1st to 00:01 on the 3rd is about one elapsed day, but two calendar days.
    settlement = ["pay_1,payment,100.00,,,2026-10-03T00:01:00+05:30,captured"]
    orders = ["pay_1,payment,100.00,,,2026-10-01T23:59:00,captured"]
    _, inside = _run(tmp_path, settlement, orders, "--timing-window", "0:2")
    _, outside = _run(tmp_path, settlement, orders, "--timing-window", "0:1")
    assert inside == []
    assert _kinds(outside) == [("TIMING", "pay_1", "both")]


def test_refund_missing_from_the_settlement_is_refund_not_landed(tmp_path: Path) -> None:
    _, findings = _run(
        tmp_path,
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
        [
            "pay_1,payment,100.00,,,2026-10-01,captured",
            "rfnd_1,refund,40.00,,,2026-10-02,processed",
        ],
    )
    assert _kinds(findings) == [("REFUND_NOT_LANDED", "rfnd_1", "orders")]


def test_refund_that_only_failed_in_the_settlement_has_not_landed(tmp_path: Path) -> None:
    _, findings = _run(
        tmp_path,
        ["rfnd_1,refund,40.00,,,2026-10-02,failed"],
        ["rfnd_1,refund,40.00,,,2026-10-02,processed"],
    )
    assert _kinds(findings) == [("REFUND_NOT_LANDED", "rfnd_1", "orders")]


def test_refund_amount_difference_is_an_amount_mismatch(tmp_path: Path) -> None:
    _, findings = _run(
        tmp_path,
        ["rfnd_1,refund,30.00,,,2026-10-02,processed"],
        ["rfnd_1,refund,40.00,,,2026-10-02,processed"],
    )
    assert _kinds(findings) == [("AMOUNT_MISMATCH", "rfnd_1", "both")]


def test_failed_order_with_a_landed_debit_is_failed_but_debited(tmp_path: Path) -> None:
    status, findings = _run(
        tmp_path,
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
        ["pay_1,payment,100.00,,,2026-10-01,failed"],
    )
    assert status == 1
    assert _kinds(findings) == [("FAILED_BUT_DEBITED", "pay_1", "both")]
    assert findings[0]["expected_paise"] == "0"
    assert findings[0]["actual_paise"] == "10000"


def test_failed_settlement_row_for_a_captured_order_is_missing_in_settlement(
    tmp_path: Path,
) -> None:
    _, findings = _run(
        tmp_path,
        ["pay_1,payment,100.00,,,2026-10-01,failed"],
        ["pay_1,payment,100.00,,,2026-10-01,captured"],
    )
    assert _kinds(findings) == [("MISSING_IN_SETTLEMENT", "pay_1", "orders")]


def test_findings_do_not_depend_on_input_row_order(tmp_path: Path) -> None:
    settlement = [f"pay_{n:03d},payment,{n}.00,,,2026-10-01,captured" for n in range(1, 60)] + [
        "pay_099,payment,5.00,,,2026-10-01,captured"
    ]
    orders = [f"pay_{n:03d},payment,{n}.00,,,2026-10-01,captured" for n in range(1, 61)]
    first_status, first = _run(tmp_path, settlement, orders)
    reordered_settlement = settlement[::-1]
    reordered_orders = orders[17:] + orders[:17]
    second_status, second = _run(tmp_path, reordered_settlement, reordered_orders)
    assert first_status == second_status == 1
    assert first == second
