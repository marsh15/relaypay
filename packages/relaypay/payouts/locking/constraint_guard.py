"""Constraint-guard payout reservation. HAND-WRITTEN BY THE MAINTAINER; this file is a stub.

Spec: no application-level lock. Insert the reservation and rely on the M16 deferred trigger
(migration 0022) that enforces sum(reserved) <= available at commit. The trigger raises SQLSTATE
23514 (check_violation); map that to INSUFFICIENT. Idempotency: a repeated key returns REPLAYED.
"""

from typing import ClassVar

from sqlalchemy.orm import Session, sessionmaker

from relaypay.payouts.locking.types import ReserveOutcome, ReserveRequest


class ConstraintGuardReserver:
    name: ClassVar[str] = "constraint_guard"

    def reserve(self, factory: sessionmaker[Session], request: ReserveRequest) -> ReserveOutcome:
        """Insert the reservation without a lock; the deferred trigger rejects an overdraw."""
        raise NotImplementedError("hand-written by the maintainer; see PROVENANCE")
