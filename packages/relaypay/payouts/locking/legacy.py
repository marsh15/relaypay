"""Control adapter: the existing production payout creation path behind ``PayoutReserver``.

No locking logic lives here. The production path takes ``SELECT ... FOR UPDATE`` on the merchant
row inside ``_create_payout_transaction``; this adapter is the row-lock guard under the name the
race tests and the M19 benchmark use for the control.
"""

from typing import ClassVar

from relaypay.payouts.locking.base import GuardedReserver
from relaypay.payouts.service import ReservationGuard


class LegacyPessimisticReserver(GuardedReserver):
    name: ClassVar[str] = "legacy_pessimistic"
    guard: ClassVar[ReservationGuard] = ReservationGuard.ROW_LOCK
