"""Seeded operation schedules for the chaos harness.

``generate_schedule(seed, n_ops)`` is a pure function of its arguments (``random.Random(seed)``
only; no clock, no database, no globals), so a failing seed reproduces exactly and a schedule
serialises to JSON for the failure dump. The generator keeps a rough model of every payment it
has created (not yet authorised / authorised / captured / refunded) to aim most commands at
payments in a state where they make sense, while a few percent are deliberately aimed at the
wrong state, a missing payment or an over-large refund so rejections are exercised too. The
model assumes commands succeed; faults make that untrue, which is the point.

Operations refer to payments by the index of the CREATE operation that makes them and to
earlier commands (REPLAY) by operation index, so a schedule is self-contained.
"""

import json
import random
from collections import Counter
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any, Final

from scripts.chaos.faults import (
    BANK_FAULTS,
    COMMAND_CRASH_POINTS,
    CRASH_RATE,
    FAULT_RATE,
    PAYOUT_CRASH_POINTS,
    PROVIDER_FAULTS,
    TICK_CRASH_POINTS,
    weighted_choice,
)


class OpKind(StrEnum):
    CREATE = "CREATE"
    AUTHORIZE = "AUTHORIZE"
    CAPTURE = "CAPTURE"
    REFUND = "REFUND"
    REPLAY = "REPLAY"  # same key and body as an earlier command (a client retry)
    CONCURRENT_DUPLICATE = "CONCURRENT_DUPLICATE"  # one key fired from 2-4 threads at once
    RECOVER_TICK = "RECOVER_TICK"  # one poller pass over non-terminal operations
    RESOLVE_PENDING = "RESOLVE_PENDING"  # the provider settles PENDING effects
    SETTLE = "SETTLE"
    PAYOUT = "PAYOUT"  # only against payout-eligible balance (plus the odd over-ask)


KIND_WEIGHTS: Final[tuple[tuple[OpKind, int], ...]] = (
    (OpKind.CREATE, 14),
    (OpKind.AUTHORIZE, 17),
    (OpKind.CAPTURE, 15),
    (OpKind.REFUND, 13),
    (OpKind.REPLAY, 8),
    (OpKind.CONCURRENT_DUPLICATE, 3),
    (OpKind.RECOVER_TICK, 6),
    (OpKind.RESOLVE_PENDING, 10),
    (OpKind.SETTLE, 2),
    (OpKind.PAYOUT, 3),
)
# Commands that carry a payment target, a provider fault and a crash point.
COMMAND_KINDS: Final = frozenset({OpKind.AUTHORIZE, OpKind.CAPTURE, OpKind.REFUND})
REPLAYABLE_KINDS: Final = frozenset(
    {OpKind.CREATE, OpKind.AUTHORIZE, OpKind.CAPTURE, OpKind.REFUND, OpKind.PAYOUT}
)
DUPLICATE_COMMANDS: Final = (("AUTHORIZE", 4), ("CAPTURE", 4), ("REFUND", 3))

INVALID_RATE: Final = 0.08  # share of targeted commands aimed at the wrong state
NO_TARGET_RATE: Final = 0.01  # share aimed at a payment that does not exist
OVER_REFUND_RATE: Final = 0.12  # share of refunds asking for more than is refundable
OVER_PAYOUT_RATE: Final = 0.06
PAYOUT_FAULT_RATE: Final = 0.2
TICK_CRASH_RATE: Final = 0.08


@dataclass(frozen=True, slots=True)
class Op:
    """One scheduled operation. Only the fields that matter for ``kind`` are set."""

    index: int
    kind: str
    target: int | None = None  # CREATE op index of the payment addressed
    amount: int | None = None  # CREATE: payment amount; REFUND: requested amount
    ref: int | None = None  # REPLAY: index of the command replayed
    command: str | None = None  # CONCURRENT_DUPLICATE: AUTHORIZE | CAPTURE | REFUND
    threads: int | None = None  # CONCURRENT_DUPLICATE: 2-4
    outcome: str | None = None  # provider (or bank, for PAYOUT) fault for the first send
    crash: str | None = None  # crash point armed for this operation
    pick: int | None = None  # RECOVER_TICK / RESOLVE_PENDING: selector
    batch: int | None = None  # RECOVER_TICK / RESOLVE_PENDING: how many items
    resolve_to: str | None = None  # RESOLVE_PENDING: SUCCEED | DECLINE
    expire_leases: bool = False  # RECOVER_TICK: leases of a dead poller have lapsed
    pct: int | None = None  # PAYOUT: percent of the payout-eligible balance
    invalid: bool = False  # deliberately aimed at the wrong state / too large

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None and value is not False
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Op":
        return cls(**data)


@dataclass(slots=True)
class _Slot:
    create_index: int
    amount: int
    stage: int = 0  # 0 created, 1 authorising, 2 captured, 3 fully refunded
    refunded: int = 0

    @property
    def refundable(self) -> int:
        return self.amount - self.refunded


def _pick_slot(
    rng: random.Random, slots: list[_Slot], *, stage: int
) -> tuple[_Slot | None, bool, bool]:
    """Choose a payment for a command: ``(slot, invalid, missing)``.

    ``slot`` is None with ``missing`` set for a command aimed at a payment that does not exist.
    """
    if rng.random() < NO_TARGET_RATE:
        return None, True, True
    eligible = [slot for slot in slots if slot.stage == stage]
    if eligible and rng.random() >= INVALID_RATE:
        return eligible[rng.randrange(len(eligible))], False, False
    return slots[rng.randrange(len(slots))], True, False


def _fault_and_crash(
    rng: random.Random, *, crash_table: tuple[tuple[str, int], ...], faults_ok: bool = True
) -> tuple[str | None, str | None]:
    outcome = weighted_choice(rng, PROVIDER_FAULTS) if rng.random() < FAULT_RATE else None
    crash = weighted_choice(rng, crash_table) if rng.random() < CRASH_RATE else None
    return (outcome if faults_ok else None), crash


def _command_op(rng: random.Random, index: int, command: str, slots: list[_Slot], kind: str) -> Op:
    stage = {"AUTHORIZE": 0, "CAPTURE": 1, "REFUND": 2}[command]
    slot, invalid, missing = _pick_slot(rng, slots, stage=stage)
    outcome, crash = _fault_and_crash(rng, crash_table=COMMAND_CRASH_POINTS)
    amount: int | None = None
    if command == "REFUND":
        if slot is None:
            amount = 1_000
        elif rng.random() < OVER_REFUND_RATE:
            amount = max(slot.refundable, 0) + rng.randint(1, 5_000)
            invalid = True
        else:
            amount = max(1, min(slot.refundable, slot.amount * rng.randint(10, 60) // 100))
            if slot.refundable <= 0:
                invalid = True
    if slot is not None and not invalid:
        if command == "AUTHORIZE":
            slot.stage = 1
        elif command == "CAPTURE":
            slot.stage = 2
        elif amount is not None:
            slot.refunded += amount
            if slot.refundable <= 0:
                slot.stage = 3
    return Op(
        index=index,
        kind=kind,
        target=None if missing or slot is None else slot.create_index,
        amount=amount,
        command=command if kind == OpKind.CONCURRENT_DUPLICATE else None,
        outcome=outcome,
        crash=crash,
        invalid=invalid,
    )


def generate_schedule(seed: int, n_ops: int) -> list[Op]:
    """The deterministic schedule of ``n_ops`` operations for ``seed``."""
    rng = random.Random(seed)  # noqa: S311 - a simulation, not a secret
    kinds = [kind for kind, _ in KIND_WEIGHTS]
    weights = [weight for _, weight in KIND_WEIGHTS]
    slots: list[_Slot] = []
    replayable: list[int] = []
    ops: list[Op] = []

    def create(index: int) -> Op:
        amount = rng.randrange(100, 5_001) * 100
        slots.append(_Slot(index, amount))
        return Op(index=index, kind=OpKind.CREATE, amount=amount)

    for index in range(n_ops):
        kind = rng.choices(kinds, weights)[0] if slots else OpKind.CREATE
        if kind == OpKind.CREATE:
            op = create(index)
        elif kind in COMMAND_KINDS:
            op = _command_op(rng, index, kind.value, slots, kind.value)
        elif kind == OpKind.CONCURRENT_DUPLICATE:
            command = weighted_choice(rng, DUPLICATE_COMMANDS)
            op = _command_op(rng, index, command, slots, kind.value)
            op = replace(op, threads=rng.randint(2, 4))
        elif kind == OpKind.REPLAY:
            if not replayable:
                op = create(index)
            else:
                ref = replayable[
                    max(0, len(replayable) - 30) + rng.randrange(min(30, len(replayable)))
                ]
                crash = (
                    weighted_choice(rng, COMMAND_CRASH_POINTS)
                    if rng.random() < CRASH_RATE
                    else None
                )
                op = Op(index=index, kind=kind.value, ref=ref, crash=crash)
        elif kind == OpKind.RECOVER_TICK:
            crash = (
                weighted_choice(rng, TICK_CRASH_POINTS) if rng.random() < TICK_CRASH_RATE else None
            )
            op = Op(
                index=index,
                kind=kind.value,
                batch=rng.randint(1, 6),
                pick=rng.randrange(1 << 16),
                expire_leases=rng.random() < 0.3,
                crash=crash,
            )
        elif kind == OpKind.RESOLVE_PENDING:
            op = Op(
                index=index,
                kind=kind.value,
                batch=rng.randint(1, 3),
                pick=rng.randrange(1 << 16),
                resolve_to="SUCCEED" if rng.random() < 0.7 else "DECLINE",
            )
        elif kind == OpKind.SETTLE:
            op = Op(index=index, kind=kind.value)
        else:  # PAYOUT
            over = rng.random() < OVER_PAYOUT_RATE
            outcome = (
                weighted_choice(rng, BANK_FAULTS) if rng.random() < PAYOUT_FAULT_RATE else None
            )
            crash = weighted_choice(rng, PAYOUT_CRASH_POINTS) if rng.random() < CRASH_RATE else None
            op = Op(
                index=index,
                kind=kind.value,
                pct=rng.randint(10, 100),
                outcome=outcome,
                crash=crash,
                invalid=over,
            )
        if op.kind in {k.value for k in REPLAYABLE_KINDS}:
            replayable.append(index)
        ops.append(op)
    return ops


def schedule_to_json(ops: list[Op]) -> str:
    """Canonical JSON for a schedule (byte-identical for identical schedules)."""
    return json.dumps([op.to_dict() for op in ops], sort_keys=True, separators=(",", ":"))


def schedule_from_json(text: str) -> list[Op]:
    return [Op.from_dict(item) for item in json.loads(text)]


def schedule_stats(ops: list[Op]) -> dict[str, Any]:
    """Counts by kind, planned provider faults and crash points, and invalid commands."""
    return {
        "ops": len(ops),
        "by_kind": dict(sorted(Counter(op.kind for op in ops).items())),
        "faults_planned": dict(sorted(Counter(op.outcome for op in ops if op.outcome).items())),
        "crashes_planned": dict(sorted(Counter(op.crash for op in ops if op.crash).items())),
        "invalid": sum(1 for op in ops if op.invalid),
    }
