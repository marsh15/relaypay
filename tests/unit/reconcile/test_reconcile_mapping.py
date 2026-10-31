"""Mapping configuration: presets, mapping JSON and CLI overrides are validated strictly."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from relaypay_reconcile.errors import ReconcileInputError
from relaypay_reconcile.mapping import DEFAULT_TIMING_WINDOW, load_mapping


def _json(tmp_path: Path, document: Any) -> Path:
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_no_arguments_gives_the_generic_layout() -> None:
    mapping = load_mapping(None, None)
    assert mapping.settlement.ref == "reference"
    assert mapping.settlement.unit == "rupees"
    assert mapping.settlement.kind is not None
    assert mapping.failed_values == frozenset({"failed"})
    assert mapping.timing_window_days == DEFAULT_TIMING_WINDOW
    assert mapping.tolerance_paise == 0


def test_razorpay_preset_reads_paise_and_ignores_adjustments() -> None:
    mapping = load_mapping(None, "razorpay")
    assert mapping.settlement.ref == "entity_id"
    assert mapping.settlement.unit == "paise"
    assert mapping.settlement.date == "settled_at"
    assert mapping.settlement.kind is not None
    assert mapping.settlement.kind.ignore == frozenset({"adjustment"})


def test_cashfree_preset_has_no_kind_column_so_every_row_is_a_payment() -> None:
    mapping = load_mapping(None, "cashfree")
    assert mapping.settlement.kind is None
    assert mapping.settlement.unit == "rupees"


def test_format_and_mapping_together_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ReconcileInputError, match="either --format or --mapping"):
        load_mapping(_json(tmp_path, {}), "razorpay")


def test_mapping_can_name_a_preset_base_and_override_one_column(tmp_path: Path) -> None:
    path = _json(tmp_path, {"format": "razorpay", "settlement": {"amount": "gross_paise"}})
    mapping = load_mapping(path, None)
    assert mapping.settlement.amount == "gross_paise"
    assert mapping.settlement.ref == "entity_id"


def test_mapping_can_set_its_own_windows_and_failure_values(tmp_path: Path) -> None:
    path = _json(
        tmp_path,
        {
            "failed_values": ["Failed", "declined"],
            "timing_window_days": [1, 5],
            "tolerance_paise": 3,
        },
    )
    mapping = load_mapping(path, None)
    assert mapping.failed_values == frozenset({"failed", "declined"})
    assert mapping.timing_window_days == (1, 5)
    assert mapping.tolerance_paise == 3


def test_cli_overrides_win_over_the_mapping(tmp_path: Path) -> None:
    path = _json(tmp_path, {"tolerance_paise": 3})
    mapping = load_mapping(path, None, timing_window_days=(0, 9), tolerance_paise=0)
    assert mapping.timing_window_days == (0, 9)
    assert mapping.tolerance_paise == 0


def test_a_null_column_means_the_field_is_not_mapped(tmp_path: Path) -> None:
    mapping = load_mapping(_json(tmp_path, {"orders": {"fee": None}}), None)
    assert mapping.orders.fee is None
    assert mapping.orders.tax == "tax"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"settings": {}}, "unknown keys: settings"),
        ({"settlement": {"reference": "x"}}, "'settlement' has unknown keys: reference"),
        ({"format": "stripe"}, "unknown format 'stripe'"),
        ({"settlement": {"unit": "dollars"}}, "settlement.unit must be paise or rupees"),
        ({"settlement": {"ref": ""}}, "settlement.ref must be a non-empty column name"),
        ({"settlement": {"ref": None}}, "settlement.ref must be a non-empty column name"),
        ({"settlement": {"kind": "type"}}, "settlement.kind must be an object or null"),
        (
            {"settlement": {"kind": {"column": "type", "payment": ["a"]}}},
            "settlement.kind.refund must list at least one value",
        ),
        (
            {"settlement": {"kind": {"column": "type", "payment": ["a"], "refund": ["A"]}}},
            "more than once",
        ),
        ({"failed_values": "failed"}, "failed_values must be a list"),
        ({"timing_window_days": [5, 1]}, "min not above max"),
        ({"timing_window_days": [0, 2_000_000_000]}, "within +/-"),
        ({"tolerance_paise": 2**63}, "tolerance_paise must be a whole number of paise from 0"),
        ({"settled_values": "captured"}, "settled_values must be a list"),
        ({"settled_values": ["captured"], "failed_values": ["Captured"]}, "both settled_values"),
        ({"timing_window_days": [0, "2"]}, "timing_window_days must be"),
        ({"timing_window_days": [0, True]}, "timing_window_days must be"),
        ({"tolerance_paise": -1}, "tolerance_paise must be"),
        ({"tolerance_paise": 1.5}, "tolerance_paise must be"),
    ],
)
def test_invalid_mapping_documents_are_rejected(
    tmp_path: Path, document: dict[str, Any], message: str
) -> None:
    with pytest.raises(ReconcileInputError, match=re.escape(message)):
        load_mapping(_json(tmp_path, document), None)


def test_mapping_must_be_a_json_object(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ReconcileInputError, match="must be a JSON object"):
        load_mapping(path, None)


def test_mapping_that_is_not_json_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ReconcileInputError, match="not valid JSON"):
        load_mapping(path, None)


def test_settled_values_default_to_completed_statuses() -> None:
    mapping = load_mapping(None, None)
    assert mapping.settled_values == frozenset({"captured", "processed", "settled"})
    assert mapping.failed_values == frozenset({"failed"})


def test_cli_overrides_are_range_checked_like_the_file() -> None:
    with pytest.raises(ReconcileInputError, match="tolerance_paise must be"):
        load_mapping(None, None, tolerance_paise=2**63)
    with pytest.raises(ReconcileInputError, match="within"):
        load_mapping(None, None, timing_window_days=(-2_000_000_000, 0))
