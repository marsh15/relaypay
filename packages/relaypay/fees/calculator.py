"""Fee and GST calculation (implemented by Claude at the maintainer's request; see PROVENANCE.md).

Written against the specification and the pre-existing tests in
tests/unit/test_fee_calculator_spec.py.
"""

from relaypay.fees.types import FeeBreakdown, FeeScheduleTerms

BPS_DENOMINATOR = 10_000


def _round_half_up(numerator: int, denominator: int) -> int:
    """Round a non-negative fraction to the nearest integer, exact halves upward."""
    return (2 * numerator + denominator) // (2 * denominator)


def compute_capture_fee(amount_paise: int, terms: FeeScheduleTerms) -> FeeBreakdown:
    """Split a captured gross amount into platform fee, GST on the fee, and the merchant's net.

    Specification (all integer paise; no floating point anywhere):

    * ``amount_paise`` must be positive, else ``ValueError``.
    * fee = round_half_up(amount_paise * mdr_bps / 10_000) + fixed_paise, then clamped to
      ``[min_fee_paise, max_fee_paise]`` (a ``None`` bound is not applied).
    * gst = round_half_up(fee * gst_bps / 10_000).
    * net = amount_paise - fee - gst; ``ValueError`` if net would be negative.
    * round_half_up rounds exact halves away from zero (all values here are non-negative).
    * Deterministic and pure: same inputs, same output; no I/O.

    Returns ``FeeBreakdown(fee, gst, net)`` with ``fee + gst + net == amount_paise``.
    """
    if amount_paise <= 0:
        raise ValueError("amount_paise must be positive")
    fee = _round_half_up(amount_paise * terms.mdr_bps, BPS_DENOMINATOR) + terms.fixed_paise
    if terms.min_fee_paise is not None:
        fee = max(fee, terms.min_fee_paise)
    if terms.max_fee_paise is not None:
        fee = min(fee, terms.max_fee_paise)
    gst = _round_half_up(fee * terms.gst_bps, BPS_DENOMINATOR)
    net = amount_paise - fee - gst
    if net < 0:
        raise ValueError("fee and GST exceed the captured amount")
    return FeeBreakdown(fee=fee, gst=gst, net=net)
