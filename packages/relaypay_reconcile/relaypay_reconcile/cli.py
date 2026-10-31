"""relaypay-reconcile: match a PSP settlement export against a seller's orders export.

Exit status: 0 when nothing is found, 1 when there are findings, 2 for an input or usage error.
"""

import argparse
import re
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from relaypay_reconcile import ingest, matching, report
from relaypay_reconcile.errors import ReconcileInputError
from relaypay_reconcile.mapping import FORMATS, load_mapping

_WINDOW = re.compile(r"(-?[0-9]+):(-?[0-9]+)")
_WHOLE = re.compile(r"[0-9]+")
_CACHE_KIB = 16_384


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="relaypay-reconcile",
        description="Match a PSP settlement export against a seller's orders export.",
    )
    parser.add_argument("--settlement", required=True, type=Path, help="settlement CSV")
    parser.add_argument("--orders", required=True, type=Path, help="seller orders CSV")
    parser.add_argument("--format", choices=FORMATS, help="settlement preset (default: generic)")
    parser.add_argument("--mapping", type=Path, help="mapping JSON; see README for the schema")
    parser.add_argument("--out-dir", type=Path, default=Path("reconcile-out"))
    parser.add_argument(
        "--report",
        type=_report_formats,
        default="csv,json,md",
        help="comma-separated subset of csv,json,md (default: all three)",
    )
    parser.add_argument(
        "--timing-window",
        type=_window,
        help="allowed days from order date to settlement, MIN:MAX (default from the mapping)",
    )
    parser.add_argument(
        "--tolerance-paise",
        type=_whole_paise,
        help="largest allowed difference in paise for amount, fee and tax (default 0)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _reconcile(args)
    except (ReconcileInputError, OSError) as error:
        print(f"relaypay-reconcile: {error}", file=sys.stderr)
        return 2
    except sqlite3.Error as error:
        print(
            f"relaypay-reconcile: storage error, nothing was reconciled: {error}", file=sys.stderr
        )
        return 2
    except OverflowError:
        print("relaypay-reconcile: a value is outside the supported range", file=sys.stderr)
        return 2


def _reconcile(args: argparse.Namespace) -> int:
    mapping = load_mapping(args.mapping, args.format, args.timing_window, args.tolerance_paise)
    conn = sqlite3.connect("", isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute(f"PRAGMA cache_size = -{_CACHE_KIB}")
        ingest.create_tables(conn)
        conn.execute("BEGIN")
        settlement = ingest.load_side(
            conn, args.settlement, mapping.settlement, mapping, ingest.INSERT_SETTLEMENT
        )
        orders = ingest.load_side(conn, args.orders, mapping.orders, mapping, ingest.INSERT_ORDERS)
        conn.execute("COMMIT")
        matching.reconcile(conn, mapping)
        summary = report.summarize(conn, settlement, orders, mapping)
        written = report.write_reports(conn, args.out_dir, args.report, summary)
    finally:
        conn.close()
    _print_summary(summary, written)
    return 1 if summary.total else 0


def _print_summary(summary: report.RunSummary, written: list[Path]) -> None:
    print(f"settlement rows: {summary.settlement.rows} (skipped {summary.settlement.skipped})")
    print(f"order rows: {summary.orders.rows} (skipped {summary.orders.skipped})")
    print(f"findings: {summary.total}")
    for kind, count in summary.counts.items():
        if count:
            print(f"  {kind.name}: {count}")
    for note in summary.coverage:
        print(f"not evaluated: {note}")
    print("reports: " + ", ".join(str(path) for path in written))


def _report_formats(text: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in text.split(","))
    unknown = [name for name in names if name not in report.REPORT_FORMATS]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown report {unknown[0]!r}; use csv, json or md")
    return names


def _window(text: str) -> tuple[int, int]:
    match = _WINDOW.fullmatch(text)
    if match is None:
        raise argparse.ArgumentTypeError("use MIN:MAX whole days, for example 0:2")
    low, high = int(match.group(1)), int(match.group(2))
    if low > high:
        raise argparse.ArgumentTypeError(f"minimum {low} is above maximum {high}")
    return low, high


def _whole_paise(text: str) -> int:
    if _WHOLE.fullmatch(text) is None:
        raise argparse.ArgumentTypeError("use a whole number of paise, zero or more")
    return int(text)
