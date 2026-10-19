"""Pessimistic payout reservation. HAND-WRITTEN BY THE MAINTAINER; this file is a stub.

Spec: inside one transaction, take ``SELECT ... FOR UPDATE`` on the ``merchant_accounts`` row,
recompute balances with ``derive_balances`` and reserve only if ``amount <= payout_eligible``.
The row lock serialises all reservations for one merchant, so concurrent callers can never
overdraw. Idempotency: a repeated key returns REPLAYED with the original payout id.
"""

from typing import ClassVar

from sqlalchemy.orm import Session, sessionmaker

from relaypay.payouts.locking.types import ReserveOutcome, ReserveRequest


class PessimisticReserver:
    name: ClassVar[str] = "pessimistic"

    def reserve(self, factory: sessionmaker[Session], request: ReserveRequest) -> ReserveOutcome:
        """Lock the merchant row FOR UPDATE, recompute balances, reserve if within eligible."""
        raise NotImplementedError("hand-written by the maintainer; see PROVENANCE")
