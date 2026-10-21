"""Interface types for the PSP webhook ordering decision (agent-owned; ``decide`` is hand-written).

RelayPay initiates every provider operation itself and sequences them locally (a capture exists
only after its authorization succeeded), so cross-operation ordering is guaranteed by
construction. What a webhook needs is a decision *within one operation*: apply its outcome,
ignore it as a duplicate or stale report, or send a contradiction to review.
"""

from dataclasses import dataclass
from typing import Literal

OperationStatus = Literal["PROCESSING", "REQUIRES_REVIEW", "SUCCEEDED", "FAILED"]
EventOutcome = Literal["PENDING", "SUCCEEDED", "DECLINED"]


@dataclass(frozen=True, slots=True)
class OperationView:
    """RelayPay's current state of the operation the event names (read under its row lock)."""

    status: OperationStatus
    kind: str
    amount: int
    applied_event_ids: frozenset[str]  # PSP event ids already applied to this operation


@dataclass(frozen=True, slots=True)
class NormalizedPspEvent:
    """A signature-verified PSP event, reduced to what the decision needs.

    Identity (account, stable key, kind, reference, amount, currency) has already been checked
    against the operation; a mismatch never reaches ``decide``.
    """

    provider: str
    event_id: str
    outcome: EventOutcome
    decline_code: str | None


@dataclass(frozen=True, slots=True)
class Apply:
    """Record the event as WEBHOOK evidence and finalize the operation with this outcome."""

    outcome: Literal["SUCCEEDED", "DECLINED"]


@dataclass(frozen=True, slots=True)
class Ignore:
    """Leave the operation unchanged. DUPLICATE: already reflected. STALE: superseded, or a
    non-terminal report that adds nothing (polling continues)."""

    reason: Literal["DUPLICATE", "STALE"]


@dataclass(frozen=True, slots=True)
class Review:
    """The event contradicts RelayPay's terminal state; a human must decide."""

    reason: str


Decision = Apply | Ignore | Review
