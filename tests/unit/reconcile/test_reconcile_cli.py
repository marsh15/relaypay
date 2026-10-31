"""The command line: exit codes, report selection, machine-readable output and input errors."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from relaypay_reconcile import matching
from relaypay_reconcile.cli import main


def test_invalid_utf8_mapping_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mapping = tmp_path / "mapping.json"
    mapping.write_bytes(b"\xff")
    status = main(
        ["--settlement", "unused.csv", "--orders", "unused.csv", "--mapping", str(mapping)]
    )
    assert status == 2
    assert "mapping.json: not valid JSON" in capsys.readouterr().err


HEADER = "reference,type,amount,fee,tax,date,status"
PROJECT_DIR = Path(__file__).resolve().parents[3] / "packages" / "relaypay_reconcile"


def _files(tmp_path: Path, settlement: str, orders: str) -> tuple[Path, Path]:
    settlement_path = tmp_path / "settlement.csv"
    orders_path = tmp_path / "orders.csv"
    settlement_path.write_text(f"{HEADER}\n{settlement}", encoding="utf-8")
    orders_path.write_text(f"{HEADER}\n{orders}", encoding="utf-8")
    return settlement_path, orders_path


def test_clean_run_exits_zero_and_writes_all_three_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settlement, orders = _files(
        tmp_path,
        "pay_1,payment,1.00,,,2026-10-01,captured\n",
        "pay_1,payment,1.00,,,2026-10-01,captured\n",
    )
    out = tmp_path / "out"
    status = main(["--settlement", str(settlement), "--orders", str(orders), "--out-dir", str(out)])
    assert status == 0
    assert sorted(path.name for path in out.iterdir()) == [
        "findings.csv",
        "findings.json",
        "report.md",
    ]
    assert "findings: 0" in capsys.readouterr().out


def test_json_report_is_one_valid_document_with_a_matching_total(tmp_path: Path) -> None:
    settlement, orders = _files(
        tmp_path,
        "pay_1,payment,1.00,,,2026-10-01,captured\npay_9,payment,2.00,,,2026-10-01,captured\n",
        "pay_1,payment,1.50,,,2026-10-01,captured\n",
    )
    out = tmp_path / "out"
    assert (
        main(["--settlement", str(settlement), "--orders", str(orders), "--out-dir", str(out)]) == 1
    )
    document = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    assert document["summary"]["total"] == len(document["findings"]) == 2
    assert document["summary"]["settlement_rows"] == 2
    assert document["summary"]["findings"]["AMOUNT_MISMATCH"] == 1
    assert len(document["summary"]["findings"]) == 9


def test_report_option_selects_formats(tmp_path: Path) -> None:
    settlement, orders = _files(tmp_path, "", "")
    out = tmp_path / "out"
    main(
        [
            "--settlement",
            str(settlement),
            "--orders",
            str(orders),
            "--out-dir",
            str(out),
            "--report",
            "md",
        ]
    )
    assert [path.name for path in out.iterdir()] == ["report.md"]


def test_repeated_runs_write_byte_identical_reports(tmp_path: Path) -> None:
    settlement, orders = _files(
        tmp_path,
        "pay_2,payment,2.00,,,2026-10-01,captured\npay_1,payment,1.00,,,2026-10-01,captured\n",
        "pay_1,payment,1.10,,,2026-10-01,captured\npay_3,payment,3.00,,,2026-10-01,captured\n",
    )
    first, second = tmp_path / "first", tmp_path / "second"
    main(["--settlement", str(settlement), "--orders", str(orders), "--out-dir", str(first)])
    main(["--settlement", str(settlement), "--orders", str(orders), "--out-dir", str(second)])
    for name in ("findings.csv", "findings.json", "report.md"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_missing_input_file_exits_two_with_a_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, orders = _files(tmp_path, "", "")
    status = main(["--settlement", str(tmp_path / "absent.csv"), "--orders", str(orders)])
    assert status == 2
    assert "absent.csv" in capsys.readouterr().err


def test_malformed_input_exits_two_and_names_the_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settlement, orders = _files(tmp_path, "pay_1,payment,oops,,,,\n", "")
    status = main(["--settlement", str(settlement), "--orders", str(orders)])
    assert status == 2
    assert "settlement.csv:2" in capsys.readouterr().err


def test_format_and_mapping_together_exit_two(tmp_path: Path) -> None:
    settlement, orders = _files(tmp_path, "", "")
    mapping = tmp_path / "mapping.json"
    mapping.write_text("{}", encoding="utf-8")
    status = main(
        [
            "--settlement",
            str(settlement),
            "--orders",
            str(orders),
            "--format",
            "razorpay",
            "--mapping",
            str(mapping),
        ]
    )
    assert status == 2


def test_unknown_report_name_is_a_usage_error(tmp_path: Path) -> None:
    settlement, orders = _files(tmp_path, "", "")
    with pytest.raises(SystemExit) as exit_info:
        main(["--settlement", str(settlement), "--orders", str(orders), "--report", "pdf"])
    assert exit_info.value.code == 2


def test_timing_window_must_be_min_colon_max(tmp_path: Path) -> None:
    settlement, orders = _files(tmp_path, "", "")
    with pytest.raises(SystemExit) as exit_info:
        main(["--settlement", str(settlement), "--orders", str(orders), "--timing-window", "3:1"])
    assert exit_info.value.code == 2


def test_the_console_script_runs_as_a_module_with_its_exit_status(tmp_path: Path) -> None:
    settlement, orders = _files(
        tmp_path,
        "pay_9,payment,2.00,,,2026-10-01,captured\n",
        "",
    )
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and arguments, no shell
        [
            sys.executable,
            "-m",
            "relaypay_reconcile",
            "--settlement",
            str(settlement),
            "--orders",
            str(orders),
            "--out-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(PROJECT_DIR)},
    )
    assert completed.returncode == 1, completed.stderr
    assert "MISSING_IN_ORDERS: 1" in completed.stdout


def test_a_value_outside_the_signed_64_bit_range_is_an_input_error(tmp_path: Path) -> None:
    settlement, orders = _files(tmp_path, "", "")
    status = main(
        [
            "--settlement",
            str(settlement),
            "--orders",
            str(orders),
            "--tolerance-paise",
            str(2**63),
        ]
    )
    assert status == 2


def test_a_timing_window_beyond_the_supported_range_is_an_input_error(tmp_path: Path) -> None:
    settlement, orders = _files(tmp_path, "", "")
    status = main(
        [
            "--settlement",
            str(settlement),
            "--orders",
            str(orders),
            "--timing-window=-2000000000:0",
        ]
    )
    assert status == 2


def test_a_storage_error_during_matching_exits_two_with_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settlement, orders = _files(tmp_path, "pay_1,payment,1.00,,,,captured\n", "")

    def disk_full(*_args: object) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(matching, "reconcile", disk_full)
    status = main(["--settlement", str(settlement), "--orders", str(orders)])
    assert status == 2
    assert "storage error" in capsys.readouterr().err


def test_an_overflow_during_processing_exits_two_with_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settlement, orders = _files(tmp_path, "pay_1,payment,1.00,,,,captured\n", "")

    def overflow(*_args: object) -> None:
        raise OverflowError("Python int too large to convert to SQLite INTEGER")

    monkeypatch.setattr(matching, "reconcile", overflow)
    status = main(["--settlement", str(settlement), "--orders", str(orders)])
    assert status == 2
    assert "outside the supported range" in capsys.readouterr().err


def test_an_unmapped_settlement_status_is_reported_as_not_evaluated(tmp_path: Path) -> None:
    settlement_path = tmp_path / "settlement.csv"
    settlement_path.write_text("reference,type,amount\npay_1,payment,1.00\n", encoding="utf-8")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(
        json.dumps({"settlement": {"fee": None, "tax": None, "date": None, "status": None}}),
        encoding="utf-8",
    )
    orders, out = tmp_path / "orders.csv", tmp_path / "out"
    orders.write_text(f"{HEADER}\npay_1,payment,1.00,,,,captured\n", encoding="utf-8")
    status = main(
        [
            "--settlement",
            str(settlement_path),
            "--orders",
            str(orders),
            "--mapping",
            str(mapping),
            "--out-dir",
            str(out),
        ]
    )
    assert status == 0
    document = json.loads((out / "findings.json").read_text(encoding="utf-8"))
    notes = document["summary"]["not_evaluated"]
    assert any("every settlement row counts as landed" in note for note in notes)
