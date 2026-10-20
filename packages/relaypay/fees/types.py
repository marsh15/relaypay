"""Interface types for the fee calculator (agent-owned; the calculator itself is hand-written)."""

from dataclasses import dataclass
from typing import Literal

RefundFeePolicy = Literal["RETAIN"]


@dataclass(frozen=True, slots=True)
class FeeScheduleTerms:
    """One immutable fee schedule version, in integer paise and basis points.

    ``mdr_bps`` is the merchant discount rate in basis points of the captured amount (190 is
    1.90 %). ``fixed_paise`` is added per capture. ``min_fee_paise`` and ``max_fee_paise`` clamp
    the fee before GST; ``None`` means no bound. ``gst_bps`` is GST on the fee (1800 is 18 %).
    """

    mdr_bps: int
    fixed_paise: int
    gst_bps: int
    min_fee_paise: int | None
    max_fee_paise: int | None
    refund_fee_policy: RefundFeePolicy


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """Split of one captured gross amount: ``fee + gst + net == gross``."""

    fee: int
    gst: int
    net: int
