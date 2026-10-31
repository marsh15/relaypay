"""The nine reconciliation exception kinds a seller report can contain."""

from enum import Enum


class ExceptionKind(Enum):
    """One way the seller's orders and the PSP settlement can disagree.

    The member name is the label used in every report, and the value is the one-line meaning.
    """

    MISSING_IN_SETTLEMENT = "An order that should have settled has no landed settlement row."
    MISSING_IN_ORDERS = "A settlement row has no order with the same reference."
    AMOUNT_MISMATCH = "The order and settlement amounts differ by more than the tolerance."
    DUPLICATE = "A reference appears more than once on one side. Pair checks are skipped."
    FEE_DIFFERENCE = "The settled fee differs from the fee expected for the order."
    GST_DIFFERENCE = "The settled tax (GST on the fee) differs from the tax expected for the order."
    TIMING = "The settlement date falls outside the configured window after the order date."
    REFUND_NOT_LANDED = "A refund in the orders has no landed refund row in the settlement."
    FAILED_BUT_DEBITED = "The order failed, yet the settlement shows a landed debit for it."
