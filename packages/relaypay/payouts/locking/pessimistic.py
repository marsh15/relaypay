"""Pessimistic payout reservation (implemented by Claude at the maintainer's request).

Inside one transaction, take ``SELECT ... FOR UPDATE`` on the ``merchant_accounts`` row, recompute
balances with ``derive_balances`` and reserve only if ``amount <= payout_eligible``. The row lock
serialises all reservations for one merchant, so concurrent callers can never overdraw. A repeated
idempotency key returns REPLAYED with the original payout id. See PROVENANCE.md.
"""

from typing import ClassVar

from relaypay.payouts.locking.base import GuardedReserver
from relaypay.payouts.service import ReservationGuard


class PessimisticReserver(GuardedReserver):
    name: ClassVar[str] = "pessimistic"
    guard: ClassVar[ReservationGuard] = ReservationGuard.ROW_LOCK
