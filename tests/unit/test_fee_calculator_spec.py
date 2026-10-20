"""Red specification tests for the hand-written fee calculator (see PROVENANCE.md).

Written from the docstring of ``relaypay.fees.calculator.compute_capture_fee`` only; they fail
with NotImplementedError until the maintainer implements it. Run with ``make handwritten``.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st
from relaypay.fees.calculator import compute_capture_fee
from relaypay.fees.types import FeeBreakdown, FeeScheduleTerms

pytestmark = pytest.mark.handwritten


def _terms(
    *,
    mdr_bps: int = 190,
    fixed_paise: int = 0,
    gst_bps: int = 1800,
    min_fee_paise: int | None = None,
    max_fee_paise: int | None = None,
) -> FeeScheduleTerms:
    return FeeScheduleTerms(
        mdr_bps=mdr_bps,
        fixed_paise=fixed_paise,
        gst_bps=gst_bps,
        min_fee_paise=min_fee_paise,
        max_fee_paise=max_fee_paise,
        refund_fee_policy="RETAIN",
    )


@pytest.mark.parametrize(
    ("amount", "terms", "expected"),
    [
        # 1.90 % of 1000.00 INR is 19.00; GST 18 % of that is 3.42.
        (100_000, _terms(), FeeBreakdown(fee=1_900, gst=342, net=97_758)),
        # 12345 * 1.99 % = 245.6655 -> 246; GST 44.28 -> 44.
        (12_345, _terms(mdr_bps=199), FeeBreakdown(fee=246, gst=44, net=12_055)),
        # An exact half rounds up: 50 * 1 % = 0.5 -> 1; GST 0.18 -> 0.
        (50, _terms(mdr_bps=100), FeeBreakdown(fee=1, gst=0, net=49)),
        # GST exact half: fee 25 * 18 % = 4.5 -> 5.
        (10_000, _terms(mdr_bps=0, fixed_paise=25), FeeBreakdown(fee=25, gst=5, net=9_970)),
        # Fixed fee only.
        (1_000, _terms(mdr_bps=0, fixed_paise=300), FeeBreakdown(fee=300, gst=54, net=646)),
        # Clamped up to the minimum fee before GST.
        (1_000, _terms(mdr_bps=100, min_fee_paise=500), FeeBreakdown(fee=500, gst=90, net=410)),
        # Clamped down to the maximum fee before GST.
        (
            10_000_000,
            _terms(mdr_bps=200, max_fee_paise=50_000),
            FeeBreakdown(fee=50_000, gst=9_000, net=9_941_000),
        ),
        # Zero rates: no fee.
        (777, _terms(mdr_bps=0, gst_bps=0), FeeBreakdown(fee=0, gst=0, net=777)),
    ],
)
def test_fixed_vectors(amount: int, terms: FeeScheduleTerms, expected: FeeBreakdown) -> None:
    assert compute_capture_fee(amount, terms) == expected


@pytest.mark.parametrize("amount", [0, -1])
def test_amount_must_be_positive(amount: int) -> None:
    with pytest.raises(ValueError):
        compute_capture_fee(amount, _terms())


def test_negative_net_is_rejected() -> None:
    with pytest.raises(ValueError):
        compute_capture_fee(100, _terms(mdr_bps=0, fixed_paise=200))


@given(
    amount=st.integers(min_value=1, max_value=10**12),
    mdr_bps=st.integers(min_value=0, max_value=500),
    fixed_paise=st.integers(min_value=0, max_value=1_000),
    gst_bps=st.integers(min_value=0, max_value=2_800),
)
def test_split_always_sums_to_gross(
    amount: int, mdr_bps: int, fixed_paise: int, gst_bps: int
) -> None:
    terms = _terms(mdr_bps=mdr_bps, fixed_paise=fixed_paise, gst_bps=gst_bps)
    try:
        breakdown = compute_capture_fee(amount, terms)
    except ValueError:
        return  # net would be negative (fixed fee larger than a tiny amount): allowed refusal
    assert breakdown.fee >= 0 and breakdown.gst >= 0 and breakdown.net >= 0
    assert breakdown.fee + breakdown.gst + breakdown.net == amount
    assert all(isinstance(value, int) for value in (breakdown.fee, breakdown.gst, breakdown.net))


@given(
    amount=st.integers(min_value=1, max_value=10**9),
    mdr_bps=st.integers(min_value=0, max_value=500),
)
def test_fee_never_decreases_as_the_amount_grows(amount: int, mdr_bps: int) -> None:
    terms = _terms(mdr_bps=mdr_bps, gst_bps=1800)
    assert compute_capture_fee(amount + 1, terms).fee >= compute_capture_fee(amount, terms).fee
