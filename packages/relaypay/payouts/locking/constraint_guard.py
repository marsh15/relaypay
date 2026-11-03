"""Constraint-guard payout reservation (implemented by Claude at the maintainer's request).

No application-level lock and no application balance check. The reservation is inserted and the
deferred coverage trigger (migration 0020) enforces ``sum(reserved) <= available`` at commit,
raising SQLSTATE 23514 (check_violation), which is reported as INSUFFICIENT. A repeated
idempotency key returns REPLAYED. See PROVENANCE.md.
"""

from collections.abc import Callable
from typing import ClassVar

from sqlalchemy.exc import IntegrityError

from relaypay.errors import RelayPayError
from relaypay.payouts.locking.base import GuardedReserver
from relaypay.payouts.service import CommandResult, ReservationGuard

CHECK_VIOLATION = "23514"


class ConstraintGuardReserver(GuardedReserver):
    name: ClassVar[str] = "constraint_guard"
    guard: ClassVar[ReservationGuard] = ReservationGuard.CONSTRAINT

    def _execute(self, create: Callable[[], CommandResult]) -> tuple[CommandResult | None, int]:
        try:
            return create(), 1
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == CHECK_VIOLATION:
                return None, 1
            raise
        except RelayPayError as error:
            # Only a non-positive amount reaches here; the trigger owns the overdraw decision.
            if error.code == "INSUFFICIENT_PAYOUT_BALANCE":
                return None, 1
            raise
