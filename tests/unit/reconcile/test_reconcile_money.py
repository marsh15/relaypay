"""Exact paise parsing: rupee text is converted with Decimal and rejected when it is not exact."""

import pytest
from relaypay_reconcile.errors import ReconcileInputError
from relaypay_reconcile.money import format_rupees, parse_paise


@pytest.mark.parametrize(
    ("text", "paise"),
    [("0", 0), ("0.5", 50), ("0.10", 10), ("19.99", 1999), ("1234567.89", 123456789)],
)
def test_rupee_text_converts_to_exact_paise(text: str, paise: int) -> None:
    assert parse_paise(text, "rupees") == paise


def test_largest_accepted_rupee_amount_stays_exact() -> None:
    assert parse_paise("999999999999999.99", "rupees") == 99_999_999_999_999_999


@pytest.mark.parametrize(
    "text", ["1.234", "-1", "1,000", ".5", "5.", "1e3", "NaN", "Infinity", " 1", "1.2.3", ""]
)
def test_rupee_text_that_is_not_exact_is_rejected(text: str) -> None:
    with pytest.raises(ReconcileInputError):
        parse_paise(text, "rupees")


@pytest.mark.parametrize(("text", "paise"), [("0", 0), ("1999", 1999), ("100000000", 100000000)])
def test_paise_text_is_read_as_whole_paise(text: str, paise: int) -> None:
    assert parse_paise(text, "paise") == paise


@pytest.mark.parametrize("text", ["19.99", "-5", "1e2", "", "₹5"])
def test_paise_text_with_any_other_shape_is_rejected(text: str) -> None:
    with pytest.raises(ReconcileInputError):
        parse_paise(text, "paise")


def test_unknown_unit_is_rejected() -> None:
    with pytest.raises(ReconcileInputError, match="unknown amount unit"):
        parse_paise("1", "dollars")


@pytest.mark.parametrize(
    ("paise", "text"), [(0, "0.00"), (5, "0.05"), (1999, "19.99"), (-1250, "-12.50")]
)
def test_format_rupees_is_two_decimal_places(paise: int, text: str) -> None:
    assert format_rupees(paise) == text
