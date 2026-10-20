"""Fee and GST calculation: HAND-WRITTEN BY THE MAINTAINER (see PROVENANCE.md).

AI tooling wrote only this interface, its specification and the red tests in
tests/unit/test_fee_calculator_spec.py. The implementation is the maintainer's.
"""

from relaypay.fees.types import FeeBreakdown, FeeScheduleTerms


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
    raise NotImplementedError("hand-written by the maintainer; see PROVENANCE")
