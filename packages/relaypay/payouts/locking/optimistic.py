"""Optimistic payout reservation. HAND-WRITTEN BY THE MAINTAINER; this file is a stub.

Spec: read ``merchant_accounts.balance_version`` (column added by M19 migration 0026), compute
balances, insert the reservation and ``UPDATE merchant_accounts SET balance_version = v + 1
WHERE id = :id AND balance_version = v`` in the same transaction. If the UPDATE touches 0 rows
another writer won; roll back and retry at most N times (``ReserveOutcome.attempts`` reports
how many transactions were tried). Idempotency: a repeated key returns REPLAYED.
"""

from typing import ClassVar

from sqlalchemy.orm import Session, sessionmaker

from relaypay.payouts.locking.types import ReserveOutcome, ReserveRequest


class OptimisticReserver:
    name: ClassVar[str] = "optimistic"

    def reserve(self, factory: sessionmaker[Session], request: ReserveRequest) -> ReserveOutcome:
        """Compare-and-swap on ``balance_version`` with bounded retry."""
        raise NotImplementedError("hand-written by the maintainer; see PROVENANCE")
