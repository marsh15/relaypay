"""Column mappings: built-in settlement presets and strictly validated mapping JSON files.

Every preset and mapping describes a completed export. A status column, when mapped, must hold
either a settled value or a failed value. Any other status stops the load, because an unknown
status such as "pending" cannot be reconciled exactly.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relaypay_reconcile.errors import ReconcileInputError
from relaypay_reconcile.money import AMOUNT_UNITS, MAX_PAISE

FORMATS = ("generic", "razorpay", "cashfree")
DEFAULT_TIMING_WINDOW = (0, 2)
DEFAULT_SETTLED_VALUES = ("captured", "processed", "settled")
DEFAULT_FAILED_VALUES = ("failed",)
MAX_DAY_WINDOW = 1_000_000_000
# A mapping is a small JSON object. Larger files are refused before they are parsed. CSV inputs
# have no such limit; they stream.
MAX_MAPPING_BYTES = 256 * 1024
_SIDE_KEYS = frozenset({"ref", "amount", "unit", "fee", "tax", "date", "status", "kind"})
_KIND_KEYS = frozenset({"column", "payment", "refund", "ignore"})
_TOP_KEYS = frozenset(
    {
        "format",
        "settlement",
        "orders",
        "settled_values",
        "failed_values",
        "timing_window_days",
        "tolerance_paise",
    }
)

_GENERIC_SIDE: dict[str, Any] = {
    "ref": "reference",
    "amount": "amount",
    "unit": "rupees",
    "fee": "fee",
    "tax": "tax",
    "date": "date",
    "status": "status",
    "kind": {"column": "type", "payment": ["payment"], "refund": ["refund"]},
}

# Settlement-side presets. Every key is set so a preset never inherits a column it did not choose.
#
# Razorpay: the field names come from its settlement API documentation (entity_id, type, fee, tax,
# settled_at; amounts in paise). They were NOT checked against a real CSV export, and the preset
# assumes every row in the file is settled.
#
# Cashfree: NOT verified, and not a Cashfree export format. The docs name the concepts (payment ID,
# settlement amount, service charge, GST) but not the CSV headers or units. The names below are
# placeholders. Use a mapping file that matches the export you actually receive.
_PRESET_SETTLEMENTS: dict[str, dict[str, Any]] = {
    "generic": _GENERIC_SIDE,
    "razorpay": {
        "ref": "entity_id",
        "amount": "amount",
        "unit": "paise",
        "fee": "fee",
        "tax": "tax",
        "date": "settled_at",
        "status": None,
        "kind": {
            "column": "type",
            "payment": ["payment"],
            "refund": ["refund"],
            "ignore": ["adjustment"],
        },
    },
    "cashfree": {
        "ref": "cashfree_payment_id",
        "amount": "amount",
        "unit": "rupees",
        "fee": "service_charge",
        "tax": "gst",
        "date": None,
        "status": None,
        "kind": None,
    },
}


@dataclass(frozen=True, slots=True)
class KindRule:
    """Which values of a type column mean a payment, a refund, or a row to skip."""

    column: str
    payment: frozenset[str]
    refund: frozenset[str]
    ignore: frozenset[str]


@dataclass(frozen=True, slots=True)
class SideMapping:
    """Column names for one file. Optional fields are None when the file does not carry them."""

    ref: str
    amount: str
    unit: str
    fee: str | None
    tax: str | None
    date: str | None
    status: str | None
    kind: KindRule | None


@dataclass(frozen=True, slots=True)
class Mapping:
    """Everything the reconciliation needs to read two exports and compare them."""

    settlement: SideMapping
    orders: SideMapping
    settled_values: frozenset[str]
    failed_values: frozenset[str]
    timing_window_days: tuple[int, int]
    tolerance_paise: int


def load_mapping(
    path: Path | None,
    format_name: str | None,
    timing_window_days: tuple[int, int] | None = None,
    tolerance_paise: int | None = None,
) -> Mapping:
    """Build the mapping from a preset, a mapping JSON file, or both, then apply CLI overrides.

    The overrides are validated with the same rules as the file, so an out-of-range value is an
    input error (exit 2) and never reaches SQLite.
    """
    if path is not None and format_name is not None:
        raise ReconcileInputError(
            'pass either --format or --mapping; a mapping can name a base with its "format" key'
        )
    document = _read_document(path) if path is not None else {}
    unknown = sorted(set(document) - _TOP_KEYS)
    if unknown:
        raise ReconcileInputError(f"mapping has unknown keys: {', '.join(unknown)}")
    base = _string(document, "format", format_name or "generic")
    if base not in FORMATS:
        raise ReconcileInputError(f"unknown format {base!r}; use one of {', '.join(FORMATS)}")

    settlement_raw = {**_PRESET_SETTLEMENTS[base], **_side_document(document, "settlement")}
    orders_raw = {**_GENERIC_SIDE, **_side_document(document, "orders")}
    settled = _status_values(
        document.get("settled_values", list(DEFAULT_SETTLED_VALUES)), "settled_values"
    )
    failed = _status_values(
        document.get("failed_values", list(DEFAULT_FAILED_VALUES)), "failed_values"
    )
    if settled & failed:
        raise ReconcileInputError(
            f"{sorted(settled & failed)} is listed in both settled_values and failed_values"
        )
    window = _timing_window(
        timing_window_days
        if timing_window_days is not None
        else document.get("timing_window_days", list(DEFAULT_TIMING_WINDOW))
    )
    tolerance = _tolerance(
        tolerance_paise if tolerance_paise is not None else document.get("tolerance_paise", 0)
    )
    return Mapping(
        settlement=_side(settlement_raw, "settlement"),
        orders=_side(orders_raw, "orders"),
        settled_values=settled,
        failed_values=failed,
        timing_window_days=window,
        tolerance_paise=tolerance,
    )


def _read_document(path: Path) -> dict[str, Any]:
    # Read at most one byte past the limit, so an oversized file is detected without loading it.
    with path.open("rb") as handle:
        data = handle.read(MAX_MAPPING_BYTES + 1)
    if len(data) > MAX_MAPPING_BYTES:
        raise ReconcileInputError(
            f"{path}: larger than {MAX_MAPPING_BYTES} bytes; a mapping is a small JSON object, "
            "so check that this is the mapping file and not a data export"
        )
    try:
        document = json.loads(data.decode("utf-8"))
    except RecursionError as error:
        raise ReconcileInputError(
            f"{path}: nested too deeply to be a mapping; a mapping needs two levels of objects"
        ) from error
    except ValueError as error:
        # UnicodeDecodeError, JSONDecodeError, and an integer over Python's digit limit.
        raise ReconcileInputError(
            f"{path}: not valid JSON (the file must be UTF-8): {error}"
        ) from error
    if not isinstance(document, dict):
        raise ReconcileInputError(f"{path}: the mapping must be a JSON object")
    return document


def _side_document(document: dict[str, Any], name: str) -> dict[str, Any]:
    side = document.get(name, {})
    if not isinstance(side, dict):
        raise ReconcileInputError(f"mapping {name!r} must be an object")
    unknown = sorted(set(side) - _SIDE_KEYS)
    if unknown:
        raise ReconcileInputError(f"mapping {name!r} has unknown keys: {', '.join(unknown)}")
    return side


def _side(raw: dict[str, Any], name: str) -> SideMapping:
    unit = _string(raw, "unit", "rupees", label=f"{name}.unit")
    if unit not in AMOUNT_UNITS:
        raise ReconcileInputError(f"{name}.unit must be paise or rupees, not {unit!r}")
    return SideMapping(
        ref=_required_column(raw, "ref", name),
        amount=_required_column(raw, "amount", name),
        unit=unit,
        fee=_column(raw, "fee", name),
        tax=_column(raw, "tax", name),
        date=_column(raw, "date", name),
        status=_column(raw, "status", name),
        kind=_kind(raw.get("kind"), name),
    )


def _column(raw: dict[str, Any], key: str, name: str) -> str | None:
    if raw.get(key) is None:
        return None
    return _required_column(raw, key, name)


def _required_column(raw: dict[str, Any], key: str, name: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReconcileInputError(f"{name}.{key} must be a non-empty column name")
    return value.strip()


def _kind(raw: Any, name: str) -> KindRule | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ReconcileInputError(f"{name}.kind must be an object or null")
    unknown = sorted(set(raw) - _KIND_KEYS)
    if unknown:
        raise ReconcileInputError(f"{name}.kind has unknown keys: {', '.join(unknown)}")
    rule = KindRule(
        column=_required_column(raw, "column", f"{name}.kind"),
        payment=_values(raw, "payment", f"{name}.kind", required=True),
        refund=_values(raw, "refund", f"{name}.kind", required=True),
        ignore=_values(raw, "ignore", f"{name}.kind"),
    )
    overlap = (rule.payment & rule.refund) | (rule.payment & rule.ignore)
    overlap |= rule.refund & rule.ignore
    if overlap:
        raise ReconcileInputError(f"{name}.kind lists {sorted(overlap)} more than once")
    return rule


def _values(raw: dict[str, Any], key: str, label: str, required: bool = False) -> frozenset[str]:
    values = raw.get(key, [])
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ReconcileInputError(f"{label}.{key} must be a list of non-empty strings")
    if required and not values:
        raise ReconcileInputError(f"{label}.{key} must list at least one value")
    return frozenset(v.strip().lower() for v in values)


def _string(raw: dict[str, Any], key: str, default: str, label: str | None = None) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise ReconcileInputError(f"{label or key} must be a string")
    return value


def _status_values(raw: Any, key: str) -> frozenset[str]:
    if not isinstance(raw, list) or not all(isinstance(v, str) and v.strip() for v in raw):
        raise ReconcileInputError(f"{key} must be a list of non-empty strings")
    return frozenset(v.strip().lower() for v in raw)


def _timing_window(raw: Any) -> tuple[int, int]:
    if not isinstance(raw, list | tuple) or len(raw) != 2 or not all(type(v) is int for v in raw):
        raise ReconcileInputError("timing_window_days must be [min, max] whole numbers")
    low, high = raw
    if not (-MAX_DAY_WINDOW <= low <= high <= MAX_DAY_WINDOW):
        raise ReconcileInputError(
            f"timing_window_days must be within +/-{MAX_DAY_WINDOW} days, with min not above max"
        )
    return low, high


def _tolerance(raw: Any) -> int:
    if type(raw) is not int or not 0 <= raw <= MAX_PAISE:
        raise ReconcileInputError(
            f"tolerance_paise must be a whole number of paise from 0 to {MAX_PAISE}"
        )
    return raw
