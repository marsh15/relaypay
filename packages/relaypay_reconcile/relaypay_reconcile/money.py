"""Exact money: amounts are integer paise. Rupee text is parsed with Decimal, never with floats."""

import re
from decimal import Decimal

from relaypay_reconcile.errors import ReconcileInputError

AMOUNT_UNITS = frozenset({"paise", "rupees"})
# The largest value SQLite stores as INTEGER. Every amount and tolerance must stay within it.
MAX_PAISE = 2**63 - 1

_PAISE_TEXT = re.compile(r"[0-9]{1,17}")
_RUPEE_TEXT = re.compile(r"[0-9]{1,15}(?:\.[0-9]{1,2})?")


def parse_paise(text: str, unit: str) -> int:
    """Return an amount as integer paise.

    `unit` is "paise" (whole numbers only) or "rupees" (at most two decimal places). Negative
    amounts are rejected: a refund is a row of its own, not a negative payment.
    """
    if unit == "paise":
        if _PAISE_TEXT.fullmatch(text) is None:
            raise ReconcileInputError(f"amount {text!r} is not a whole number of paise")
        return int(text)
    if unit == "rupees":
        if _RUPEE_TEXT.fullmatch(text) is None:
            raise ReconcileInputError(f"amount {text!r} is not rupees with at most 2 decimals")
        return int(Decimal(text) * 100)
    raise ReconcileInputError(f"unknown amount unit {unit!r}; use paise or rupees")


def format_rupees(paise: int) -> str:
    """Render integer paise as a rupee string with two decimals, for human-readable reports."""
    sign = "-" if paise < 0 else ""
    whole, fraction = divmod(abs(paise), 100)
    return f"{sign}{whole}.{fraction:02d}"
