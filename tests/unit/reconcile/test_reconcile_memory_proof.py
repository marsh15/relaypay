"""Memory proof: a 2,000,000-row settlement and order pair reconciles within a fixed peak RSS.

The pair is written to a scratch directory that is deleted when the test ends, even on failure.
The CLI runs in a child process, and that child reports its own peak resident set size, so other
tests in the same pytest process cannot raise the figure.
"""

import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

ROWS = 2_000_000
MISSING_EVERY = 5_000  # every 5,000th order has no settlement row: 400 MISSING_IN_SETTLEMENT
MISMATCH_EVERY = 1_000  # every 1,000th settlement amount is one paisa high: 2,000 AMOUNT_MISMATCH
EXPECTED = {"MISSING_IN_SETTLEMENT": 400, "AMOUNT_MISMATCH": 2_000}
HEADER = ["reference", "type", "amount", "fee", "tax", "date", "status"]
PROJECT_DIR = Path(__file__).resolve().parents[3] / "packages" / "relaypay_reconcile"

# Measured with this test's workload on macOS 27 (arm64), Python 3.12.12, before the test was
# added: 41,730,048 bytes maximum RSS (ru_maxrss is bytes on macOS, kilobytes on Linux). The bound
# is about 3x that figure, to allow for allocator and platform differences. It is not a target.
PEAK_RSS_BOUND_BYTES = 128 * 1024 * 1024

# Runs the CLI in this process, then reports its own peak RSS in bytes on stderr.
_WRAPPER = (
    "import resource, sys\n"
    "from relaypay_reconcile.cli import main\n"
    "status = main(sys.argv[1:])\n"
    "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
    "print(peak if sys.platform == 'darwin' else peak * 1024, file=sys.stderr)\n"
    "raise SystemExit(status)\n"
)


def _rupees(paise: int) -> str:
    whole, fraction = divmod(paise, 100)
    return f"{whole}.{fraction:02d}"


def _row(reference: str, amount: int, fee: int, tax: int) -> list[str]:
    return [
        reference,
        "payment",
        _rupees(amount),
        _rupees(fee),
        _rupees(tax),
        "2026-10-01",
        "captured",
    ]


def _write_pair(root: Path) -> tuple[Path, Path]:
    """Stream both files row by row. Nothing about the pair is kept in memory."""
    settlement, orders = root / "settlement.csv", root / "orders.csv"
    with (
        settlement.open("w", newline="", encoding="utf-8") as settlement_handle,
        orders.open("w", newline="", encoding="utf-8") as orders_handle,
    ):
        settlement_writer, orders_writer = csv.writer(settlement_handle), csv.writer(orders_handle)
        settlement_writer.writerow(HEADER)
        orders_writer.writerow(HEADER)
        for index in range(ROWS):
            reference = f"pay_{index:08d}"
            amount = 10_000 + (index * 7) % 900_000
            fee = amount * 236 // 10_000
            tax = fee * 18 // 100
            if index % MISSING_EVERY != 1:
                settled = amount + (1 if index % MISMATCH_EVERY == 0 else 0)
                settlement_writer.writerow(_row(reference, settled, fee, tax))
            orders_writer.writerow(_row(reference, amount, fee, tax))
    return settlement, orders


@pytest.mark.proof
@pytest.mark.skipif(
    sys.platform == "win32", reason="peak RSS is read with the POSIX resource module"
)
def test_two_million_row_pair_reconciles_within_the_peak_rss_bound() -> None:
    started = time.perf_counter()
    with TemporaryDirectory(prefix="relaypay-reconcile-proof-") as scratch:
        root = Path(scratch)
        settlement, orders = _write_pair(root)
        out = root / "out"
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and arguments, no shell
            [
                sys.executable,
                "-c",
                _WRAPPER,
                "--settlement",
                str(settlement),
                "--orders",
                str(orders),
                "--out-dir",
                str(out),
                "--report",
                "csv",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={"PYTHONPATH": str(PROJECT_DIR)},
            timeout=1_800,
        )
        peak_bytes = int(completed.stderr.strip().splitlines()[-1])
        with (out / "findings.csv").open(encoding="utf-8", newline="") as handle:
            finding_rows = sum(1 for _ in handle) - 1

    assert completed.returncode == 1, completed.stderr
    assert f"findings: {sum(EXPECTED.values())}" in completed.stdout
    for kind, count in EXPECTED.items():
        assert f"  {kind}: {count}" in completed.stdout
    assert finding_rows == sum(EXPECTED.values())
    assert peak_bytes <= PEAK_RSS_BOUND_BYTES, (
        f"peak RSS {peak_bytes} bytes exceeds the bound of {PEAK_RSS_BOUND_BYTES} bytes"
    )
    print(
        json.dumps(
            {
                "orders": ROWS,
                "settlementRows": ROWS - ROWS // MISSING_EVERY,
                "findings": EXPECTED,
                "peakRssBytes": peak_bytes,
                "elapsedSeconds": round(time.perf_counter() - started, 3),
            },
            sort_keys=True,
        )
    )
