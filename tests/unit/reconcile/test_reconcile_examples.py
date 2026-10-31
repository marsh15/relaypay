"""The sample seller export pair: every seeded exception is found exactly once, and nothing else."""

import csv
import json
from collections import Counter
from pathlib import Path

from relaypay_reconcile.cli import main
from relaypay_reconcile.kinds import ExceptionKind

EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "reconcile"


def _findings(tmp_path: Path, *extra: str) -> list[tuple[str, str, str]]:
    out = tmp_path / "out"
    status = main(
        [
            "--settlement",
            str(EXAMPLES / "settlement.csv"),
            "--orders",
            str(EXAMPLES / "orders.csv"),
            "--out-dir",
            str(out),
            "--report",
            "csv",
            *extra,
        ]
    )
    assert status == 1
    with (out / "findings.csv").open(encoding="utf-8", newline="") as handle:
        return [(row["exception"], row["reference"], row["side"]) for row in csv.DictReader(handle)]


def _expected() -> list[tuple[str, str, str]]:
    raw = json.loads((EXAMPLES / "expected_findings.json").read_text(encoding="utf-8"))
    return [(exception, reference, side) for exception, reference, side in raw]


def test_each_seeded_exception_is_found_exactly_once(tmp_path: Path) -> None:
    found = Counter(_findings(tmp_path))
    assert found == Counter(_expected())
    assert max(found.values()) == 1


def test_the_sample_covers_all_nine_exception_kinds(tmp_path: Path) -> None:
    kinds = {exception for exception, _, _ in _findings(tmp_path)}
    assert kinds == {kind.name for kind in ExceptionKind}
    assert len(_findings(tmp_path)) == len(ExceptionKind)


def test_clean_references_produce_no_findings(tmp_path: Path) -> None:
    references = {reference for _, reference, _ in _findings(tmp_path)}
    assert not references & {"pay_clean_01", "pay_clean_02", "pay_clean_03", "rfnd_clean_01"}


def test_the_example_mapping_file_gives_the_same_findings(tmp_path: Path) -> None:
    mapped = _findings(tmp_path, "--mapping", str(EXAMPLES / "mapping.example.json"))
    assert Counter(mapped) == Counter(_expected())
