"""Interface for payout reservation strategies (agent-owned; see docs/plans M15/M19).

A *reserver* decides whether a payout of ``amount`` paise may be reserved against a
merchant's payout-eligible balance and, if so, records the reservation. Three strategies
are hand-written by the maintainer (pessimistic, optimistic, constraint guard); the
``LegacyPessimisticReserver`` in ``legacy.py`` is the control that wraps today's production
path. All of them must satisfy the same contract so the 500-way race test and the M19
benchmark can run them interchangeably.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Protocol

from sqlalchemy.orm import Session, sessionmaker

from relaypay.identity.security import Principal


@dataclass(frozen=True, slots=True)
class ReserveRequest:
    organisation_id: uuid.UUID
    environment_id: uuid.UUID
    merchant_account_id: uuid.UUID
    beneficiary_id: uuid.UUID
    amount: int  # paise
    idempotency_key: str
    principal: Principal


class ReserveStatus(StrEnum):
    RESERVED = "RESERVED"  # a new reservation was recorded
    INSUFFICIENT = "INSUFFICIENT"  # amount exceeds the payout-eligible balance
    REPLAYED = "REPLAYED"  # the idempotency key was seen before; the original payout is returned


@dataclass(frozen=True, slots=True)
class ReserveOutcome:
    status: ReserveStatus
    payout_id: uuid.UUID | None  # set for RESERVED and REPLAYED, None for INSUFFICIENT
    attempts: int  # transactions tried (1 for lock-based strategies, >1 after CAS retries)


class PayoutReserver(Protocol):
    """Strategy contract. ``reserve`` runs its own transaction(s) on ``factory``.

    Contract:
    - ``amount <= payout_eligible`` -> RESERVED with a new payout id; never overdraw: the sum
      of reservations never exceeds the available balance, however many callers race.
    - ``amount > payout_eligible`` -> INSUFFICIENT, no payout row, no reservation.
    - Same idempotency key again -> REPLAYED with the original payout id, no second reservation.
    - Any other failure propagates as an exception; it is never reported as INSUFFICIENT.
    """

    name: ClassVar[str]

    def reserve(
        self, factory: sessionmaker[Session], request: ReserveRequest
    ) -> ReserveOutcome: ...
