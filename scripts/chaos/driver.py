"""CI-mode chaos driver: execute one seed's schedule in-process against the service layer.

What is real: the service functions (``initiate_*``, ``dispatch_operation``, recovery, finalizer,
settlement, payouts), Postgres, the signature/validation path, and the mock bank's database.
What is simulated: the provider (``FakeProvider``, scripted per stable key) and process death
(``SimulatedCrash`` raised at the production crash points, plus ``api.after_initiate`` which this
driver calls because it plays the API route).

A crash aborts the current unit of work; nothing is rolled back that a real crash would keep,
because every crash point sits between committed transactions. The client then behaves like a
real client: commands that never got an answer are re-sent with the same Idempotency-Key during
quiescence.

Quiescence (no more faults): the provider settles every PENDING effect, unanswered commands are
retried, then recovery passes run (expiring leases, as the 30 s lease would) until no operation
is PROCESSING; operations in REQUIRES_REVIEW get one operator ``retry_lookup``. Then the system is
snapshotted and handed to ``check_invariants``. The webhook/event delivery batches are global
(not tenant-scoped), so they are not run here.
"""

import hashlib
import json
import os
import random
import threading
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any

from relaypay.contracts import EmptyCommand, PaymentIntentCreate, RefundCreate
from relaypay.database import build_session_factory
from relaypay.errors import RelayPayError
from relaypay.idempotency import Fingerprint, build_fingerprint, key_hint
from relaypay.mock_bank.models import BankAccount
from relaypay.mock_bank.service import (
    apply_transfer,
    configure_fault,
    lookup_transfer,
    parse_command,
)
from relaypay.payments.service import (
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
    initiate_refund,
    read_operation,
)
from relaypay.payouts.models import Payout, PayoutAttempt
from relaypay.payouts.service import create_payout, dispatch_payout_attempt
from relaypay.proof import AckedOp, capture_snapshot, check_invariants
from relaypay.proof.crashpoints import SimulatedCrash, crash_plan, maybe_crash
from relaypay.provider_operations.models import ProviderOperation
from relaypay.provider_operations.recovery import claim_specific_operation, recover_claim
from relaypay.provider_operations.service import dispatch_operation
from relaypay.provider_operations.service_types import ProviderObservation
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from scripts.chaos.ops import Op, OpKind, generate_schedule, schedule_stats
from tests.support.db import relaypay_database_url
from tests.support.fake_provider import FakeProvider, Outcome
from tests.support.snapshots import fake_provider_effects
from tests.support.world import PayoutBody, World, balances, build_world, make_beneficiary, settle

BANK_DATABASE_URL = "postgresql+psycopg://bank_app:bank_app_dev@localhost:55432/bank"
BANK_ACCOUNT = "bank_chaos"
BANK_SECRET = "chaos-synthetic-bank-secret"  # noqa: S105
QUIESCE_MAX_PASSES = 12
POOL_SIZE = 4
POOL_OVERFLOW = 1
BANK_POOL_PER_PROCESS = 2
CONNECTIONS_PER_SEED = POOL_SIZE + POOL_OVERFLOW + BANK_POOL_PER_PROCESS

PAYMENT_COMMANDS = ("AUTHORIZE", "CAPTURE", "REFUND")


# Chaos engines skip the per-checkout ping and ask the server not to wait for WAL flush on
# commit: CI mode never crashes Postgres, it only needs the transactions to be atomic, and
# fsync-per-commit is what made a 20 x 1000 run slow on a laptop.
_SESSION_OPTIONS = "-c synchronous_commit=off"


@contextmanager
def seed_factory(application_name: str) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        relaypay_database_url(),
        connect_args={"application_name": application_name, "options": _SESSION_OPTIONS},
        pool_size=POOL_SIZE,
        max_overflow=POOL_OVERFLOW,
    )
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()


# ----- bank ------------------------------------------------------------------------------------


class LocalBankTransport:
    """In-process ``BankTransport``: the real signed bank simulator, no HTTP hop."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        reply = apply_transfer(
            self.factory, command=parse_command(request_bytes), signing_secret=BANK_SECRET
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        reply = lookup_transfer(
            self.factory,
            account_public_id=account_id,
            stable_key=stable_key,
            signing_secret=BANK_SECRET,
        )
        return ProviderObservation(reply.status_code, reply.body, reply.headers)


def bank_database_url() -> str:
    return os.environ.get("BANK_DATABASE_URL", BANK_DATABASE_URL)


@contextmanager
def bank_factory(
    pool_size: int = 8, *, ensure_account: bool = True
) -> Iterator[sessionmaker[Session]]:
    """Session factory on the bank database; optionally creates the chaos bank account."""
    engine = create_engine(
        bank_database_url(),
        pool_size=pool_size,
        max_overflow=0,
        connect_args={"application_name": "relaypay-chaos-bank", "options": _SESSION_OPTIONS},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False, autobegin=False)
    try:
        if ensure_account:
            with factory() as session, session.begin():
                if (
                    session.scalar(select(BankAccount).where(BankAccount.public_id == BANK_ACCOUNT))
                    is None
                ):
                    session.add(
                        BankAccount(
                            public_id=BANK_ACCOUNT,
                            name="Chaos synthetic account",
                            signing_secret_digest=hashlib.sha256(BANK_SECRET.encode()).digest(),
                        )
                    )
        yield factory
    finally:
        engine.dispose()


# ----- seed state ------------------------------------------------------------------------------


@dataclass(slots=True)
class CommandSpec:
    """A client command, reconstructible from its operation (so it can be replayed verbatim)."""

    kind: str  # CREATE | AUTHORIZE | CAPTURE | REFUND | PAYOUT
    key: str
    op_index: int
    payment_id: str | None = None
    amount: int = 0  # requested amount (the ack amount)
    outcome: str | None = None
    payload: PaymentIntentCreate | RefundCreate | None = None
    fingerprint: Fingerprint | None = None


@dataclass(slots=True)
class SeedResult:
    seed: int
    n_ops: int
    elapsed: float = 0.0
    ops_by_kind: dict[str, int] = field(default_factory=dict)
    executed: int = 0
    skipped: int = 0
    crashes: dict[str, int] = field(default_factory=dict)
    faults: dict[str, int] = field(default_factory=dict)
    rejections: dict[str, int] = field(default_factory=dict)
    quiesce_passes: int = 0
    quiesced: bool = False
    acked_ops: int = 0
    unacked_retried: int = 0
    review_remaining: int = 0
    payouts: dict[str, int] = field(default_factory=dict)
    findings: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    invariants: str | dict[str, Any] = "SKIPPED"
    violations: list[dict[str, str]] = field(default_factory=list)
    snapshot_counts: dict[str, int] = field(default_factory=dict)
    schedule: list[dict[str, Any]] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    snapshot: Any = None  # the SystemSnapshot, only kept when asked for (tests)

    @property
    def failed(self) -> bool:
        return bool(self.failures) or bool(self.violations)

    def summary(self) -> dict[str, Any]:
        """Everything except the bulky schedule, log and violations lists."""
        bulky = {"schedule", "log", "violations", "failures", "snapshot"}
        data = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in bulky}
        data["failures"] = len(self.failures)
        return data


class SeedContext:
    def __init__(
        self,
        seed: int,
        factory: sessionmaker[Session],
        bank: sessionmaker[Session],
        world: World,
        beneficiary_id: str,
        provider: FakeProvider,
        result: SeedResult,
    ) -> None:
        self.seed = seed
        self.factory = factory
        self.bank = bank
        self.world = world
        self.beneficiary_id = beneficiary_id
        self.provider = provider
        self.result = result
        self.lock = threading.RLock()
        self.payments: dict[int, str] = {}  # CREATE op index -> payment public id
        self.payment_amounts: dict[str, int] = {}
        self.specs: dict[int, CommandSpec] = {}
        self.scripted: dict[str, str] = {}  # provider stable key -> scripted outcome
        self.bank_faults: set[str] = set()
        self.acked: dict[tuple[str, str, str], AckedOp] = {}
        self.unacked: dict[str, CommandSpec] = {}  # Idempotency-Key -> spec
        self.answered: set[str] = set()  # Idempotency-Keys whose command got an answer
        self.crashes: Counter[str] = Counter()
        self.rejections: Counter[str] = Counter()
        self.payout_counts: Counter[str] = Counter()
        self.findings: Counter[str] = Counter()
        self.duplicate_pool: ThreadPoolExecutor | None = None

    def fail(self, message: str) -> None:
        with self.lock:
            if len(self.result.failures) < 25:
                self.result.failures.append(message)


# ----- commands --------------------------------------------------------------------------------


def _key(seed: int, index: int, tag: str) -> str:
    digest = hashlib.sha256(f"chaos:{seed}:{index}:{tag}".encode()).hexdigest()[:12]
    return f"chaos-{seed}-{index}-{tag}-{digest}"


def _fingerprint(payment_id: str, suffix: str, body: EmptyCommand | RefundCreate) -> Fingerprint:
    return build_fingerprint(
        api_version="v1",
        method="POST",
        route_template=f"/payment_intents/{{payment_intent_id}}/{suffix}",
        path_params={"payment_intent_id": payment_id},
        body=body,
    )


_MISSING_PAYMENT = "pay_" + "0" * 32


def build_spec(ctx: SeedContext, op: Op, command: str) -> CommandSpec | None:
    """The client command for ``op`` (None when its payment was never created)."""
    world = ctx.world
    if command == "CREATE":
        payload = PaymentIntentCreate(
            customer_id=world.customer_public_id,
            merchant_reference=f"chaos-{ctx.seed}-{op.index}",
            amount=op.amount or 1,
            currency="INR",
        )
        return CommandSpec(
            "CREATE",
            _key(ctx.seed, op.index, "create"),
            op.index,
            amount=payload.amount,
            payload=payload,
            fingerprint=build_fingerprint(
                api_version="v1",
                method="POST",
                route_template="/payment_intents",
                path_params={},
                body=payload,
            ),
        )
    if op.target is None:
        payment_id = _MISSING_PAYMENT
    else:
        found = ctx.payments.get(op.target)
        if found is None:
            return None
        payment_id = found
    amount = ctx.payment_amounts.get(payment_id, 1_000)
    key = _key(ctx.seed, op.index, command[:3].lower())
    if command == "REFUND":
        refund_payload = RefundCreate(
            amount=op.amount or 1,
            currency="INR",
            merchant_refund_reference=f"chaos-{ctx.seed}-{op.index}",
        )
        return CommandSpec(
            "REFUND",
            key,
            op.index,
            payment_id,
            amount=refund_payload.amount,
            outcome=op.outcome,
            payload=refund_payload,
            fingerprint=_fingerprint(payment_id, "refunds", refund_payload),
        )
    suffix = command.lower()
    return CommandSpec(
        command,
        key,
        op.index,
        payment_id,
        amount=amount,
        outcome=op.outcome,
        fingerprint=_fingerprint(payment_id, suffix, EmptyCommand()),
    )


def _script_provider(ctx: SeedContext, spec: CommandSpec, envelope: dict[str, Any]) -> None:
    """Choose the provider's behaviour for the first mutate of this command's stable key."""
    if not spec.outcome or spec.payment_id is None:
        return
    stable_key = (
        f"refund:{envelope['resourceId']}"
        if spec.kind == "REFUND"
        else f"{spec.kind.lower()}:{spec.payment_id}"
    )
    with ctx.lock:
        if stable_key in ctx.scripted or stable_key in ctx.provider.effects:
            return
        ctx.provider.script(stable_key, Outcome[spec.outcome])
        ctx.scripted[stable_key] = spec.outcome


def _record_ack(ctx: SeedContext, spec: CommandSpec, envelope: dict[str, Any]) -> None:
    if spec.kind == "CREATE":
        with ctx.lock:
            ctx.payments.setdefault(spec.op_index, str(envelope["id"]))
            ctx.payment_amounts[str(envelope["id"])] = spec.amount
            ctx.answered.add(spec.key)
            ctx.unacked.pop(spec.key, None)
        return
    resource_id = envelope.get("resourceId")
    with ctx.lock:
        ctx.answered.add(spec.key)
        ctx.unacked.pop(spec.key, None)
        if spec.kind in PAYMENT_COMMANDS and isinstance(resource_id, str):
            hint = key_hint(spec.key)
            ctx.acked[(spec.kind, resource_id, hint)] = AckedOp(
                kind=spec.kind,
                resource_id=resource_id,
                idempotency_key_hint=hint,
                amount=spec.amount,
            )


def execute_command(ctx: SeedContext, spec: CommandSpec, *, inject: bool = True) -> None:
    """Run one client command like the API route does: initiate, dispatch, read the answer.

    Raises ``RelayPayError`` for rejections and ``SimulatedCrash`` when a crash point fires.
    """
    world, factory = ctx.world, ctx.factory
    if spec.kind == "PAYOUT":
        _execute_payout(ctx, spec, inject=inject)
        return
    assert spec.fingerprint is not None
    if spec.kind == "CREATE":
        assert isinstance(spec.payload, PaymentIntentCreate)
        created = create_payment_intent(
            factory,
            organisation_id=world.organisation_id,
            environment_id=world.environment_id,
            payload=spec.payload,
            idempotency_key=spec.key,
            fingerprint=spec.fingerprint,
            key_pepper=world.key_pepper,
        )
        _record_ack(ctx, spec, json.loads(created.body))
        return
    assert spec.payment_id is not None
    common: dict[str, Any] = {
        "organisation_id": world.organisation_id,
        "environment_id": world.environment_id,
        "payment_public_id": spec.payment_id,
        "idempotency_key": spec.key,
        "fingerprint": spec.fingerprint,
        "key_pepper": world.key_pepper,
    }
    if spec.kind == "AUTHORIZE":
        result = initiate_authorization(factory, **common)
    elif spec.kind == "CAPTURE":
        result = initiate_capture(factory, **common)
    else:
        assert isinstance(spec.payload, RefundCreate)
        result = initiate_refund(factory, payload=spec.payload, **common)
    envelope: dict[str, Any] = json.loads(result.body)
    if result.status_code == 202:
        if inject:
            _script_provider(ctx, spec, envelope)
        maybe_crash("api.after_initiate")
        operation_id = str(envelope["operationId"])
        dispatch_operation(
            factory,
            organisation_id=world.organisation_id,
            environment_id=world.environment_id,
            operation_public_id=operation_id,
            provider_account_id=world.provider_account_id,
            provider_signing_secret=world.provider_signing_secret,
            transport=ctx.provider,
        )
        answer = read_operation(
            factory,
            organisation_id=world.organisation_id,
            environment_id=world.environment_id,
            operation_public_id=operation_id,
        )
        envelope = json.loads(answer.body)
    _record_ack(ctx, spec, envelope)


def _execute_payout(ctx: SeedContext, spec: CommandSpec, *, inject: bool) -> None:
    world = ctx.world
    assert spec.fingerprint is not None
    created = create_payout(
        ctx.factory,
        principal=world.admin_principal,
        environment_public_id=world.environment_public_id,
        merchant_public_id=world.merchant_public_id,
        beneficiary_public_id=ctx.beneficiary_id,
        amount=spec.amount,
        idempotency_key=spec.key,
        fingerprint=spec.fingerprint,
        key_pepper=world.key_pepper,
    )
    payout_id = str(json.loads(created.body)["id"])
    stable_key = f"payout:{payout_id}:attempt:1"
    with ctx.lock:
        arm = inject and spec.outcome is not None and stable_key not in ctx.bank_faults
        if arm:
            ctx.bank_faults.add(stable_key)
    if arm and spec.outcome:
        configure_fault(
            ctx.bank,
            account_public_id=BANK_ACCOUNT,
            stable_key=stable_key,
            fault_type=spec.outcome,
        )
        ctx.payout_counts[f"fault_{spec.outcome.lower()}"] += 1
    ctx.payout_counts["created"] += 1
    _dispatch_payout(ctx, payout_id)


def _dispatch_payout(ctx: SeedContext, payout_id: str) -> None:
    dispatch_payout_attempt(
        ctx.factory,
        payout_public_id=payout_id,
        bank_account_id=BANK_ACCOUNT,
        bank_signing_secret=BANK_SECRET,
        transport=LocalBankTransport(ctx.bank),
    )


def _payouts_with_attempt_status(ctx: SeedContext, statuses: tuple[str, ...]) -> list[str]:
    with ctx.factory() as session, session.begin():
        return list(
            session.scalars(
                select(Payout.public_id)
                .join(PayoutAttempt, PayoutAttempt.payout_id == Payout.id)
                .where(
                    Payout.organisation_id == ctx.world.organisation_id,
                    PayoutAttempt.status.in_(statuses),
                )
                .order_by(Payout.public_id)
            )
        )


# ----- operations ------------------------------------------------------------------------------


def processing_operations(
    factory: sessionmaker[Session], organisation_id: uuid.UUID
) -> list[tuple[str, bool]]:
    """``(public_id, was_sent)`` of a tenant's PROCESSING operations."""
    with factory() as session, session.begin():
        rows = session.execute(
            select(ProviderOperation.public_id, ProviderOperation.last_sent_at)
            .where(
                ProviderOperation.organisation_id == organisation_id,
                ProviderOperation.status == "PROCESSING",
            )
            .order_by(ProviderOperation.public_id)
        ).all()
    return [(str(row[0]), row[1] is not None) for row in rows]


def review_operations(factory: sessionmaker[Session], organisation_id: uuid.UUID) -> list[str]:
    with factory() as session, session.begin():
        return list(
            session.scalars(
                select(ProviderOperation.public_id)
                .where(
                    ProviderOperation.organisation_id == organisation_id,
                    ProviderOperation.status == "REQUIRES_REVIEW",
                )
                .order_by(ProviderOperation.public_id)
            )
        )


def _processing_operations(ctx: SeedContext) -> list[tuple[str, bool]]:
    return processing_operations(ctx.factory, ctx.world.organisation_id)


def _review_operations(ctx: SeedContext) -> list[str]:
    return review_operations(ctx.factory, ctx.world.organisation_id)


def _expire_leases(ctx: SeedContext) -> None:
    """Lapse every recovery lease, as 30 s of wall clock (or a dead poller) would."""
    past = datetime.now(UTC) - timedelta(seconds=1)
    with ctx.factory() as session, session.begin():
        session.execute(
            update(ProviderOperation)
            .where(
                ProviderOperation.organisation_id == ctx.world.organisation_id,
                ProviderOperation.lookup_lease_expires_at.is_not(None),
            )
            .values(lookup_lease_expires_at=past)
        )


def _recover(ctx: SeedContext, operation_id: str, *, review: bool = False) -> None:
    claim = claim_specific_operation(
        ctx.factory,
        organisation_id=ctx.world.organisation_id,
        operation_public_id=operation_id,
        require_review=review,
    )
    maybe_crash("poller.between_tasks")
    recover_claim(
        ctx.factory,
        claim=claim,
        provider_account_id=ctx.world.provider_account_id,
        provider_signing_secret=ctx.world.provider_signing_secret,
        transport=ctx.provider,
        actor_type="ADMIN_LOOKUP" if review else "RECOVERY_WORKER",
    )


def _op_recover_tick(ctx: SeedContext, op: Op) -> str:
    """One poller pass: recover a few operations, then the payout dispatch batch."""
    if op.expire_leases:
        _expire_leases(ctx)
    sent = [operation_id for operation_id, was_sent in _processing_operations(ctx) if was_sent]
    recovered = 0
    if sent:
        start = (op.pick or 0) % len(sent)
        chosen = (sent[start:] + sent[:start])[: op.batch or 1]
        for operation_id in chosen:
            try:
                _recover(ctx, operation_id)
                recovered += 1
            except RelayPayError as error:  # lease still held, or already terminal
                ctx.rejections[error.code] += 1
    for payout_id in _payouts_with_attempt_status(ctx, ("PREPARED", "AMBIGUOUS"))[:25]:
        _dispatch_payout(ctx, payout_id)
    return f"recovered={recovered}"


def _op_resolve_pending(ctx: SeedContext, op: Op) -> str:
    pending = sorted(
        key for key, effect in ctx.provider.effects.items() if effect.outcome == "PENDING"
    )
    if not pending:
        return "none-pending"
    resolved = 0
    start = (op.pick or 0) % len(pending)
    for key in (pending[start:] + pending[:start])[: op.batch or 1]:
        ctx.provider.resolve(key, Outcome[op.resolve_to or "SUCCEED"])
        resolved += 1
    return f"resolved={resolved}"


def _op_payout(ctx: SeedContext, op: Op) -> str:
    eligible = balances(ctx.factory, ctx.world).payout_eligible
    if eligible <= 0:
        return "no-eligible-balance"
    amount = eligible + 1 if op.invalid else max(1, eligible * (op.pct or 100) // 100)
    spec = CommandSpec(
        "PAYOUT",
        _key(ctx.seed, op.index, "pay"),
        op.index,
        amount=amount,
        outcome=op.outcome,
    )
    body = PayoutBody(
        merchant_account_id=ctx.world.merchant_public_id,
        beneficiary_id=ctx.beneficiary_id,
        amount=amount,
    )
    spec.fingerprint = build_fingerprint(
        api_version="admin-v1",
        method="POST",
        route_template="/environments/{environment_id}/payouts",
        path_params={"environment_id": ctx.world.environment_public_id},
        body=body,
    )
    ctx.specs[op.index] = spec
    execute_command(ctx, spec)
    return f"payout={amount}"


def _run_duplicates(ctx: SeedContext, op: Op, spec: CommandSpec) -> str:
    """Fire the same command (same Idempotency-Key) from several threads at once."""
    threads = op.threads or 2
    barrier = threading.Barrier(threads)

    def fire(position: int) -> tuple[str, str]:
        rng = random.Random(f"{ctx.seed}:{op.index}:{position}")  # noqa: S311
        points = {op.crash: 0.5} if op.crash else {}
        try:
            with crash_plan(points, rng):
                barrier.wait(timeout=30)
                execute_command(ctx, spec)
            return "ok", ""
        except SimulatedCrash as crash:
            return "crash", crash.point
        except RelayPayError as error:
            return "rejected", error.code
        except Exception as error:
            return "error", f"{type(error).__name__}: {error}"

    assert ctx.duplicate_pool is not None
    outcomes = list(ctx.duplicate_pool.map(fire, range(threads)))
    for state, detail in outcomes:
        if state == "crash":
            ctx.crashes[detail] += 1
            with ctx.lock:
                if spec.key not in ctx.answered:
                    ctx.unacked[spec.key] = spec
        elif state == "rejected":
            ctx.rejections[detail] += 1
        elif state == "error":
            ctx.fail(f"op {op.index} CONCURRENT_DUPLICATE: unexpected {detail}")
    return ",".join(state for state, _ in outcomes)


def run_op(ctx: SeedContext, op: Op) -> None:
    entry: dict[str, Any] = {"i": op.index, "k": op.kind}
    rng = random.Random(f"{ctx.seed}:{op.index}")  # noqa: S311 - simulation
    points = {op.crash: 1.0} if op.crash else {}
    try:
        with crash_plan(points, rng):
            entry["r"] = _dispatch_op(ctx, op)
    except SimulatedCrash as crash:
        ctx.crashes[crash.point] += 1
        entry["crash"] = crash.point
        spec = ctx.specs.get(op.index)
        if op.kind == OpKind.REPLAY:
            spec = ctx.specs.get(op.ref if op.ref is not None else -1)
        if spec is not None and spec.kind != "PAYOUT":
            with ctx.lock:
                if spec.key not in ctx.answered:
                    ctx.unacked[spec.key] = spec
    except RelayPayError as error:
        ctx.rejections[error.code] += 1
        entry["rejected"] = error.code
        with ctx.lock:
            ctx.unacked.pop(_spec_key(ctx, op), None)
    except Exception as error:
        entry["error"] = f"{type(error).__name__}: {error}"
        ctx.fail(f"op {op.index} {op.kind}: unexpected {type(error).__name__}: {error}")
    if len(ctx.result.log) < 5000:
        ctx.result.log.append(entry)


def _spec_key(ctx: SeedContext, op: Op) -> str:
    index = op.ref if op.kind == OpKind.REPLAY and op.ref is not None else op.index
    spec = ctx.specs.get(index)
    return spec.key if spec is not None else ""


def _dispatch_op(ctx: SeedContext, op: Op) -> str:
    kind = op.kind
    if kind == OpKind.RECOVER_TICK:
        return _op_recover_tick(ctx, op)
    if kind == OpKind.RESOLVE_PENDING:
        return _op_resolve_pending(ctx, op)
    if kind == OpKind.SETTLE:
        return f"settled={settle(ctx.factory, ctx.world)}"
    if kind == OpKind.PAYOUT:
        return _op_payout(ctx, op)
    if kind == OpKind.REPLAY:
        replayed = ctx.specs.get(op.ref if op.ref is not None else -1)
        if replayed is None:
            ctx.result.skipped += 1
            return "skipped:replay-target-not-run"
        execute_command(ctx, replayed)
        return f"replay-of={op.ref}"
    command = op.command if kind == OpKind.CONCURRENT_DUPLICATE else kind
    assert command is not None
    spec = build_spec(ctx, op, str(command))
    if spec is None:
        ctx.result.skipped += 1
        return "skipped:target-not-created"
    ctx.specs[op.index] = spec
    if kind == OpKind.CONCURRENT_DUPLICATE:
        return _run_duplicates(ctx, op, spec)
    execute_command(ctx, spec)
    return "ok"


# ----- quiescence ------------------------------------------------------------------------------


def quiesce(ctx: SeedContext) -> None:
    """Stop injecting faults and drive every operation to a resting state."""
    rng = random.Random(f"{ctx.seed}:quiesce")  # noqa: S311
    provider = ctx.provider
    for key in sorted(k for k, e in provider.effects.items() if e.outcome == "PENDING"):
        provider.resolve(key, Outcome.SUCCEED if rng.random() < 0.75 else Outcome.DECLINE)

    unsent = [name for name, was_sent in _processing_operations(ctx) if not was_sent]
    ctx.findings["unsent_operations_before_client_retry"] = len(unsent)
    for spec in sorted(ctx.unacked.values(), key=lambda s: s.op_index):
        ctx.result.unacked_retried += 1
        try:
            execute_command(ctx, spec, inject=False)
        except RelayPayError as error:
            ctx.rejections[error.code] += 1
            ctx.unacked.pop(spec.key, None)
        except Exception as error:
            ctx.fail(f"client retry of op {spec.op_index}: {type(error).__name__}: {error}")

    for pass_number in range(1, QUIESCE_MAX_PASSES + 1):
        ctx.result.quiesce_passes = pass_number
        _expire_leases(ctx)
        processing = _processing_operations(ctx)
        if not processing:
            ctx.result.quiesced = True
            break
        progressed = 0
        for operation_id, was_sent in processing:
            if not was_sent:
                continue
            try:
                _recover(ctx, operation_id)
                progressed += 1
            except RelayPayError as error:
                ctx.rejections[error.code] += 1
        for payout_id in _payouts_with_attempt_status(ctx, ("PREPARED", "AMBIGUOUS")):
            _dispatch_payout(ctx, payout_id)
        if not progressed and all(not was_sent for _, was_sent in processing):
            break

    # Operator pass: one retry_lookup per operation resting in REQUIRES_REVIEW.
    for operation_id in _review_operations(ctx):
        _expire_leases(ctx)
        try:
            _recover(ctx, operation_id, review=True)
        except RelayPayError as error:
            ctx.rejections[error.code] += 1
    ctx.result.review_remaining = len(_review_operations(ctx))
    ctx.result.quiesced = not _processing_operations(ctx)

    # The poller's payout batch only picks PREPARED and AMBIGUOUS attempts. Anything still
    # SENT (a worker died after committing the send) is invisible to it; an operator looks it up.
    stuck = _payouts_with_attempt_status(ctx, ("SENT",))
    ctx.findings["payouts_stuck_sent_after_worker_sweeps"] = len(stuck)
    for payout_id in stuck + _payouts_with_attempt_status(ctx, ("PREPARED", "AMBIGUOUS")):
        try:
            _dispatch_payout(ctx, payout_id)
        except RelayPayError as error:
            ctx.rejections[error.code] += 1
    with ctx.factory() as session, session.begin():
        rows = session.execute(
            select(Payout.status, PayoutAttempt.status)
            .join(PayoutAttempt, PayoutAttempt.payout_id == Payout.id)
            .where(Payout.organisation_id == ctx.world.organisation_id)
        ).all()
    ctx.payout_counts.update(f"final_{payout}_{attempt}".lower() for payout, attempt in rows)


# ----- one seed --------------------------------------------------------------------------------


def run_seed(
    seed: int,
    n_ops: int,
    bank: sessionmaker[Session],
    *,
    schedule: list[Op] | None = None,
    keep_snapshot: bool = False,
) -> SeedResult:
    started = time.monotonic()
    ops = schedule if schedule is not None else generate_schedule(seed, n_ops)
    result = SeedResult(seed=seed, n_ops=len(ops))
    result.schedule = [op.to_dict() for op in ops]
    result.ops_by_kind = dict(schedule_stats(ops)["by_kind"])
    provider = FakeProvider()
    try:
        with seed_factory(f"chaos-seed-{seed}") as factory:
            world = build_world(factory, label=f"chaos-{seed}")
            beneficiary = make_beneficiary(factory, world)
            ctx = SeedContext(seed, factory, bank, world, beneficiary, provider, result)
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"chaos-{seed}") as pool:
                ctx.duplicate_pool = pool
                for op in ops:
                    run_op(ctx, op)
                    result.executed += 1
                quiesce(ctx)
            _finish(ctx, keep_snapshot=keep_snapshot)
    except Exception as error:
        result.failures.append(f"seed aborted: {type(error).__name__}: {error}")
    result.elapsed = round(time.monotonic() - started, 2)
    return result


def _finish(ctx: SeedContext, *, keep_snapshot: bool = False) -> None:
    result, provider = ctx.result, ctx.provider
    with ctx.factory() as session, session.begin():
        review_keys = list(
            session.scalars(
                select(ProviderOperation.stable_provider_key).where(
                    ProviderOperation.organisation_id == ctx.world.organisation_id,
                    ProviderOperation.status == "REQUIRES_REVIEW",
                )
            )
        )
    known_effects = set(provider.effects)
    # An operation whose first send was committed but never reached the provider (a crash
    # between the send record and the mutate) can only ever look up a 404: it rests in review.
    ctx.findings["review_operations_never_reached_provider"] = sum(
        1 for key in review_keys if key not in known_effects
    )
    result.crashes = dict(sorted(ctx.crashes.items()))
    result.rejections = dict(sorted(ctx.rejections.items()))
    result.payouts = dict(sorted(ctx.payout_counts.items()))
    result.findings = {key: value for key, value in sorted(ctx.findings.items()) if value}
    applied = Counter(outcome for key, outcome in ctx.scripted.items() if key in provider.effects)
    result.faults = dict(sorted(applied.items()))
    result.acked_ops = len(ctx.acked)
    if not result.quiesced:
        left = _processing_operations(ctx)
        unsent = sum(1 for _, was_sent in left if not was_sent)
        ctx.fail(
            f"did not quiesce: {len(left)} operations still PROCESSING after "
            f"{result.quiesce_passes} passes ({unsent} never sent: "
            f"{[name for name, sent in left if not sent][:5]})"
        )
    if ctx.unacked:
        ctx.fail(f"{len(ctx.unacked)} commands never got an answer after client retry")
    try:
        provider.assert_exactly_once()
    except AssertionError as error:
        ctx.fail(f"provider exactly-once violated: {error}")

    snapshot = capture_snapshot(
        ctx.factory,
        None,
        organisation_ids=[ctx.world.organisation_id],
        acked_ops=sorted(
            ctx.acked.values(), key=lambda a: (a.kind, a.resource_id, a.idempotency_key_hint)
        ),
        extra_provider_effects=fake_provider_effects(provider),
    )
    if keep_snapshot:
        result.snapshot = snapshot
    result.snapshot_counts = {
        name: len(getattr(snapshot, name))
        for name in snapshot.__dataclass_fields__
        if isinstance(getattr(snapshot, name), tuple)
    }
    try:
        violations = check_invariants(snapshot, quiesced=result.quiesced)
    except NotImplementedError:
        result.invariants = "SKIPPED"
        return
    result.invariants = {
        "violations": len(violations),
        "codes": dict(sorted(Counter(v.code for v in violations).items())),
    }
    result.violations = [asdict(v) for v in violations[:50]]


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]
