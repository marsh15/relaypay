"""Stateful proof: a Hypothesis state machine drives the real payment service layer.

One fresh world (organisation, merchant, ledger) is built per machine instance, so examples
are isolated in a shared Postgres. Only the provider is faked (``FakeProvider``, with scripted
decline / pending / lost-response outcomes). A small independent model predicts every status,
error code and refundable amount; invariants are checked straight from SQL after every step
(never through the invariant checker under development), and again after a teardown that
drives recovery until nothing is in flight.
"""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    invariant,
    multiple,
    precondition,
    rule,
    run_state_machine_as_test,
)
from relaypay.contracts import EmptyCommand, PaymentIntentCreate, RefundCreate
from relaypay.errors import RelayPayError
from relaypay.idempotency import build_fingerprint
from relaypay.ledger.models import Journal, LedgerAccount, Posting
from relaypay.payments.models import Authorization, Capture, PaymentIntent, Refund
from relaypay.payments.service import (
    HTTPResult,
    create_payment_intent,
    initiate_authorization,
    initiate_capture,
    initiate_refund,
    read_operation,
    read_payment,
)
from relaypay.provider_operations.models import IdempotencyRecord, ProviderOperation
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.support.db import relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider, Outcome
from tests.support.world import World, balances, build_world, dispatch, recover

pytestmark = [pytest.mark.integration, pytest.mark.proof]

Kind = Literal["AUTHORIZE", "CAPTURE", "REFUND"]
STATUSES = frozenset({"PROCESSING", "SUCCEEDED", "FAILED", "REQUIRES_REVIEW"})
TERMINAL = frozenset({"SUCCEEDED", "FAILED"})
ACTIVE = frozenset({"PROCESSING", "REQUIRES_REVIEW"})
RESERVING = frozenset({"PROCESSING", "REQUIRES_REVIEW", "SUCCEEDED"})
MAX_PAYMENTS = 3  # few payments per run, so each one is driven deep into the lifecycle
INDETERMINATE_AFTER = 5  # PENDING lookups before RelayPay parks an operation for review

# What the provider holds after the first mutate, per scripted outcome.
EFFECT = {
    Outcome.SUCCEED: "SUCCEEDED",
    Outcome.LOSE_RESPONSE: "SUCCEEDED",
    Outcome.DECLINE: "DECLINED",
    Outcome.LOSE_RESPONSE_DECLINE: "DECLINED",
    Outcome.PENDING: "PENDING",
}
LOST = frozenset({Outcome.LOSE_RESPONSE, Outcome.LOSE_RESPONSE_DECLINE})

# SUCCEED is repeated so most runs reach capture and refund instead of dying at a decline.
AUTH_OUTCOMES = st.sampled_from(
    [Outcome.SUCCEED] * 4 + [Outcome.DECLINE, Outcome.LOSE_RESPONSE, Outcome.PENDING]
)
CAPTURE_OUTCOMES = st.sampled_from(
    [Outcome.SUCCEED] * 4
    + [Outcome.DECLINE, Outcome.LOSE_RESPONSE, Outcome.LOSE_RESPONSE_DECLINE, Outcome.PENDING]
)
REFUND_OUTCOMES = CAPTURE_OUTCOMES


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("stateful-payments-tests") as session_factory:
        yield session_factory


@dataclass
class Op:
    """Model of one provider operation (one stable provider key)."""

    kind: Kind
    payment: str
    op_id: str
    stable_key: str
    amount: int
    scripted: Outcome
    status: str = "PROCESSING"
    sent: bool = False
    effect: str | None = None  # provider-side truth: PENDING / SUCCEEDED / DECLINED
    pending_lookups: int = 0


@dataclass(frozen=True)
class Command:
    """A command already accepted by the service, kept so it can be replayed."""

    kind: Literal["CREATE", "AUTHORIZE", "CAPTURE", "REFUND"]
    key: str
    payment: str  # empty for CREATE until the id is known
    payload: PaymentIntentCreate | RefundCreate | None
    first_body: bytes


@dataclass
class PaymentModel:
    amount: int
    auth: str | None = None  # operation public id
    capture: str | None = None
    refunds: list[str] = field(default_factory=list)


def _command_fingerprint(payment_id: str, suffix: str, body: EmptyCommand | RefundCreate) -> Any:
    return build_fingerprint(
        api_version="v1",
        method="POST",
        route_template=f"/payment_intents/{{payment_intent_id}}/{suffix}",
        path_params={"payment_intent_id": payment_id},
        body=body,
    )


def _send(
    factory: sessionmaker[Session],
    world: World,
    kind: Literal["CREATE", "AUTHORIZE", "CAPTURE", "REFUND"],
    *,
    key: str,
    payment_id: str,
    payload: PaymentIntentCreate | RefundCreate | None,
) -> HTTPResult:
    """Issue one command through the real service function with an explicit idempotency key."""
    common: dict[str, Any] = {
        "organisation_id": world.organisation_id,
        "environment_id": world.environment_id,
        "idempotency_key": key,
        "key_pepper": world.key_pepper,
    }
    if kind == "CREATE":
        assert isinstance(payload, PaymentIntentCreate)
        return create_payment_intent(
            factory,
            payload=payload,
            fingerprint=build_fingerprint(
                api_version="v1",
                method="POST",
                route_template="/payment_intents",
                path_params={},
                body=payload,
            ),
            **common,
        )
    if kind == "AUTHORIZE":
        return initiate_authorization(
            factory,
            payment_public_id=payment_id,
            fingerprint=_command_fingerprint(payment_id, "authorize", EmptyCommand()),
            **common,
        )
    if kind == "CAPTURE":
        return initiate_capture(
            factory,
            payment_public_id=payment_id,
            fingerprint=_command_fingerprint(payment_id, "capture", EmptyCommand()),
            **common,
        )
    assert isinstance(payload, RefundCreate)
    return initiate_refund(
        factory,
        payment_public_id=payment_id,
        payload=payload,
        fingerprint=_command_fingerprint(payment_id, "refunds", payload),
        **common,
    )


class PaymentMachine(RuleBasedStateMachine):
    payments = Bundle("payments")
    pending_keys = Bundle("pending_keys")

    def __init__(self, factory: sessionmaker[Session]) -> None:
        super().__init__()
        self.factory = factory
        self.world = build_world(factory, label="stateful")
        self.provider = FakeProvider()
        self.model: dict[str, PaymentModel] = {}
        self.ops: dict[str, Op] = {}  # by operation public id
        self.by_key: dict[str, Op] = {}  # by stable provider key
        self.commands: list[Command] = []
        self.keys: set[str] = set()  # idempotency keys that were accepted by the service
        self.touched: set[str] = set()

    # ----- model helpers ---------------------------------------------------------------

    def _has_active_ops(self) -> bool:
        return any(op.sent and op.status in ACTIVE for op in self.ops.values())

    def _status(self, op_id: str | None) -> str | None:
        return self.ops[op_id].status if op_id is not None else None

    def _captured(self, payment: str) -> int:
        capture = self.model[payment].capture
        return self.model[payment].amount if self._status(capture) == "SUCCEEDED" else 0

    def _reserved(self, payment: str) -> int:
        return sum(
            self.ops[op_id].amount
            for op_id in self.model[payment].refunds
            if self.ops[op_id].status in RESERVING
        )

    def _refundable(self, payment: str) -> int:
        return max(0, self._captured(payment) - self._reserved(payment))

    def _expected_error(self, kind: Kind, payment: str, amount: int) -> str | None:
        model = self.model[payment]
        if kind == "CAPTURE" and self._status(model.auth) != "SUCCEEDED":
            return "CAPTURE_REQUIRES_SUCCEEDED_AUTHORIZATION"
        if kind == "REFUND":
            if self._status(model.capture) != "SUCCEEDED":
                return "REFUND_REQUIRES_SUCCEEDED_CAPTURE"
            if amount > self._refundable(payment):
                return "REFUND_AMOUNT_EXCEEDS_AVAILABLE"
        return None

    def _observe(self, op: Op, *, lookup: bool) -> None:
        """Apply what RelayPay learns from the provider's current effect for ``op``."""
        if op.status in TERMINAL:
            return
        if op.effect == "SUCCEEDED":
            op.status = "SUCCEEDED"
        elif op.effect == "DECLINED":
            op.status = "FAILED"
        elif lookup:
            op.pending_lookups += 1
            if op.pending_lookups >= INDETERMINATE_AFTER:
                op.status = "REQUIRES_REVIEW"

    def _dispatch(self, op: Op) -> None:
        """Real ``dispatch_operation``: first send, or a lookup when it was already sent."""
        was_terminal = op.status in TERMINAL
        dispatch(self.factory, self.world, op.op_id, self.provider)
        if was_terminal:
            return
        if op.sent:
            self._observe(op, lookup=True)
            return
        op.sent = True
        op.effect = EFFECT[op.scripted]
        if op.scripted not in LOST:
            self._observe(op, lookup=False)

    def _stable_key(self, op_id: str) -> str:
        with self.factory() as session, session.begin():
            key = session.scalar(
                select(ProviderOperation.stable_provider_key).where(
                    ProviderOperation.organisation_id == self.world.organisation_id,
                    ProviderOperation.public_id == op_id,
                )
            )
        assert key is not None
        return str(key)

    # ----- rules -----------------------------------------------------------------------

    @precondition(lambda self: len(self.model) < MAX_PAYMENTS)
    @rule(target=payments, amount=st.integers(min_value=100, max_value=1_000_000))
    def create_payment(self, amount: int) -> str:
        payload = PaymentIntentCreate(
            customer_id=self.world.customer_public_id,
            merchant_reference=f"order-{uuid.uuid4().hex}",
            amount=amount,
            currency="INR",
        )
        key = f"payment-{uuid.uuid4().hex}"
        result = _send(self.factory, self.world, "CREATE", key=key, payment_id="", payload=payload)
        assert result.status_code == 201
        payment_id = str(json.loads(result.body)["id"])
        self.model[payment_id] = PaymentModel(amount)
        self.keys.add(key)
        self.commands.append(Command("CREATE", key, payment_id, payload, result.body))
        self.touched.add(payment_id)
        return payment_id

    @rule(target=pending_keys, payment=payments, outcome=AUTH_OUTCOMES)
    def authorize(self, payment: str, outcome: Outcome) -> Any:
        return self._operation_command("AUTHORIZE", payment, 0, outcome)

    @rule(target=pending_keys, payment=payments, outcome=CAPTURE_OUTCOMES)
    def capture(self, payment: str, outcome: Outcome) -> Any:
        return self._operation_command("CAPTURE", payment, 0, outcome)

    @rule(
        target=pending_keys,
        payment=payments,
        outcome=REFUND_OUTCOMES,
        mode=st.sampled_from(["within", "within", "exact", "over"]),
        draw=st.integers(min_value=0, max_value=10**9),
    )
    def refund(self, payment: str, outcome: Outcome, mode: str, draw: int) -> Any:
        refundable = self._refundable(payment)
        if mode == "within":
            amount = 1 + draw % max(refundable, 1)
        elif mode == "exact":
            amount = max(refundable, 1)
        else:  # slightly beyond what is refundable
            amount = refundable + 1 + draw % 50
        return self._operation_command("REFUND", payment, amount, outcome)

    @rule(target=pending_keys, payment=payments)
    def advance_happy_path(self, payment: str) -> Any:
        """Authorize, then capture, with a cooperative provider: reaches refundable states."""
        model = self.model[payment]
        if model.auth is None:
            return self._operation_command("AUTHORIZE", payment, 0, Outcome.SUCCEED)
        if model.capture is None and self._status(model.auth) == "SUCCEEDED":
            return self._operation_command("CAPTURE", payment, 0, Outcome.SUCCEED)
        return multiple()

    @rule(
        target=pending_keys,
        payment=payments,
        amount=st.integers(min_value=1, max_value=1_000_000),
    )
    def refund_within(self, payment: str, amount: int) -> Any:
        """A refund sized to fit the refundable amount, with a cooperative provider."""
        refundable = self._refundable(payment)
        if refundable == 0:
            return multiple()
        return self._operation_command("REFUND", payment, 1 + amount % refundable, Outcome.SUCCEED)

    def _operation_command(self, kind: Kind, payment: str, amount: int, outcome: Outcome) -> Any:
        self.touched.add(payment)
        key = f"{kind.lower()}-{uuid.uuid4().hex}"
        payload = (
            RefundCreate(
                amount=amount, currency="INR", merchant_refund_reference=f"r-{uuid.uuid4().hex}"
            )
            if kind == "REFUND"
            else None
        )
        expected_error = self._expected_error(kind, payment, amount)
        if expected_error is not None:
            before = self._refundable(payment)
            with pytest.raises(RelayPayError) as rejected:
                _send(self.factory, self.world, kind, key=key, payment_id=payment, payload=payload)
            assert rejected.value.code == expected_error
            assert rejected.value.http_status == 409
            if expected_error == "REFUND_AMOUNT_EXCEEDS_AVAILABLE":
                assert (rejected.value.details or {}).get("availableAmount") == before
            return multiple()

        result = _send(self.factory, self.world, kind, key=key, payment_id=payment, payload=payload)
        body = json.loads(result.body)
        op_id = str(body["operationId"])
        self.keys.add(key)
        self.commands.append(Command(kind, key, payment, payload, result.body))
        model = self.model[payment]
        singleton = {"AUTHORIZE": model.auth, "CAPTURE": model.capture}.get(kind)

        if op_id in self.ops:  # a fresh key bound to the existing singleton operation
            op = self.ops[op_id]
            assert kind != "REFUND" and op_id == singleton and op.kind == kind
            self._dispatch(op)  # already sent -> RelayPay must look up, never re-mutate
            return multiple()

        assert singleton is None, "a second operation was created for a singleton resource"
        assert result.status_code == 202 and body["status"] == "PROCESSING"
        stable_key = self._stable_key(op_id)
        op = Op(kind, payment, op_id, stable_key, amount or model.amount, outcome)
        self.ops[op_id] = op
        self.by_key[stable_key] = op
        if kind == "AUTHORIZE":
            model.auth = op_id
        elif kind == "CAPTURE":
            model.capture = op_id
        else:
            model.refunds.append(op_id)
        # Stable keys are only known after initiation: script, then dispatch.
        self.provider.script(stable_key, outcome)
        self._dispatch(op)
        return multiple(stable_key) if outcome is Outcome.PENDING else multiple()

    @rule(payment=payments, pick=st.integers(min_value=0, max_value=10**6))
    def replay_request(self, payment: str, pick: int) -> None:
        """Same key + same body: no new operation, and bytes equal the stored/current truth."""
        commands = [c for c in self.commands if c.payment == payment]
        command = commands[pick % len(commands)]
        replay = _send(
            self.factory,
            self.world,
            command.kind,
            key=command.key,
            payment_id=payment,
            payload=command.payload,
        )
        again = _send(
            self.factory,
            self.world,
            command.kind,
            key=command.key,
            payment_id=payment,
            payload=command.payload,
        )
        assert (replay.status_code, replay.body) == (again.status_code, again.body)
        if command.kind == "CREATE":
            assert replay.status_code == 201 and replay.body == command.first_body
            assert replay.replayed and replay.headers["Idempotency-Replayed"] == "true"
            return
        op_id = str(json.loads(command.first_body)["operationId"])
        current = read_operation(
            self.factory,
            organisation_id=self.world.organisation_id,
            environment_id=self.world.environment_id,
            operation_public_id=op_id,
        )
        assert (replay.status_code, replay.body) == (current.status_code, current.body)
        assert json.loads(replay.body)["operationId"] == op_id
        self.touched.add(payment)

    @rule(
        payment=payments,
        other=payments,
        pick=st.integers(min_value=0, max_value=10**6),
    )
    def replay_with_different_body(self, payment: str, other: str, pick: int) -> None:
        """Same key + different canonical input: 409 IDEMPOTENCY_KEY_REUSED, nothing changes."""
        commands = [c for c in self.commands if c.payment == payment]
        command = commands[pick % len(commands)]
        target, payload = payment, command.payload
        if isinstance(payload, PaymentIntentCreate | RefundCreate):
            payload = payload.model_copy(update={"amount": payload.amount + 1})
        elif other != payment:
            target = other  # authorize/capture have an empty body: change the path instead
        else:
            return
        with pytest.raises(RelayPayError) as rejected:
            _send(
                self.factory,
                self.world,
                command.kind,
                key=command.key,
                payment_id=target,
                payload=payload,
            )
        assert rejected.value.code == "IDEMPOTENCY_KEY_REUSED"
        assert rejected.value.http_status == 409

    @precondition(lambda self: self._has_active_ops())
    @rule()
    def recover_tick(self) -> None:
        """One lookup (the path the poller uses, ignoring backoff) for every in-flight op."""
        for op in list(self.ops.values()):
            if op.sent and op.status in ACTIVE:
                recover(self.factory, self.world, op.op_id, self.provider)
                self._observe(op, lookup=True)
                self.touched.add(op.payment)

    @precondition(lambda self: self._has_active_ops())
    @rule(
        pick=st.integers(min_value=0, max_value=10**6),
        times=st.integers(min_value=1, max_value=INDETERMINATE_AFTER + 1),
    )
    def recover_one(self, pick: int, times: int) -> None:
        """Poll one operation repeatedly: a never-resolving PENDING ends up in review."""
        active = [op for op in self.ops.values() if op.sent and op.status in ACTIVE]
        op = active[pick % len(active)]
        for _ in range(times):
            if op.status in TERMINAL:
                break
            recover(self.factory, self.world, op.op_id, self.provider)
            self._observe(op, lookup=True)
        self.touched.add(op.payment)

    @rule(key=pending_keys, outcome=st.sampled_from([Outcome.SUCCEED, Outcome.DECLINE]))
    def resolve_pending(self, key: str, outcome: Outcome) -> None:
        """The provider settles a PENDING effect; RelayPay only learns it on the next lookup."""
        op = self.by_key[key]
        if op.effect != "PENDING":
            return
        self.provider.resolve(key, outcome)
        op.effect = EFFECT[outcome]

    # ----- invariants (straight from SQL; the invariant checker is deliberately unused) ---

    @invariant()
    def money_invariants_hold(self) -> None:
        self._check()

    def _check(self, *, final: bool = False) -> None:
        world = self.world
        org = world.organisation_id
        with self.factory() as session, session.begin():
            journal_rows = session.execute(
                select(
                    Journal.id,
                    func.count(Posting.id),
                    func.coalesce(
                        func.sum(case((Posting.side == "DEBIT", Posting.amount), else_=0)), 0
                    ),
                    func.coalesce(
                        func.sum(case((Posting.side == "CREDIT", Posting.amount), else_=0)), 0
                    ),
                )
                .select_from(Journal)
                .outerjoin(Posting, Posting.journal_id == Journal.id)
                .where(Journal.organisation_id == org)
                .group_by(Journal.id)
            ).all()
            pending_liability = int(
                session.scalar(
                    select(
                        func.coalesce(
                            func.sum(
                                case(
                                    (Posting.side == "CREDIT", Posting.amount),
                                    else_=-Posting.amount,
                                )
                            ),
                            0,
                        )
                    )
                    .select_from(Posting)
                    .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
                    .where(
                        Posting.organisation_id == org,
                        LedgerAccount.code == "PENDING_PAYABLE_LIABILITY",
                    )
                )
                or 0
            )
            journals = list(
                session.execute(
                    select(Journal.id, Journal.journal_type, Journal.reference_id).where(
                        Journal.organisation_id == org
                    )
                )
            )
            operations = list(
                session.scalars(
                    select(ProviderOperation).where(ProviderOperation.organisation_id == org)
                )
            )
            authorizations = list(
                session.scalars(select(Authorization).where(Authorization.organisation_id == org))
            )
            captures = list(session.scalars(select(Capture).where(Capture.organisation_id == org)))
            refunds = list(session.scalars(select(Refund).where(Refund.organisation_id == org)))
            payment_ids = {
                row.id: row.public_id
                for row in session.scalars(
                    select(PaymentIntent).where(PaymentIntent.organisation_id == org)
                )
            }
            idempotency_records = int(
                session.scalar(
                    select(func.count())
                    .select_from(IdempotencyRecord)
                    .where(IdempotencyRecord.organisation_id == org)
                )
                or 0
            )

        # (a) every journal balances and has at least two postings
        for journal_id, postings, debit, credit in journal_rows:
            assert postings >= 2, f"journal {journal_id} has {postings} postings"
            assert debit == credit > 0, f"journal {journal_id}: debit {debit} != credit {credit}"

        # (e) allowed enums, and resource status mirrors the operation (and the model)
        op_by_id = {operation.id: operation for operation in operations}
        assert {op.public_id for op in operations} == set(self.ops)
        assert len(operations) == len(authorizations) + len(captures) + len(refunds)
        for operation in operations:
            assert operation.status in STATUSES
            assert operation.status == self.ops[operation.public_id].status, operation.public_id
        for resource in (*authorizations, *captures, *refunds):
            assert resource.status in STATUSES
            assert resource.status == op_by_id[resource.provider_operation_id].status

        # model <-> database agreement on what exists per payment
        for payment_id, payment_model in self.model.items():
            assert payment_id in payment_ids.values()
            assert payment_model.amount > 0
        auth_ops = {op_by_id[a.provider_operation_id].public_id for a in authorizations}
        assert auth_ops == {m.auth for m in self.model.values() if m.auth}
        capture_ops = {op_by_id[c.provider_operation_id].public_id for c in captures}
        assert capture_ops == {m.capture for m in self.model.values() if m.capture}
        refund_ops = {op_by_id[r.provider_operation_id].public_id for r in refunds}
        assert refund_ops == {op for m in self.model.values() for op in m.refunds}

        # (b) per payment, Σ refunds in PROCESSING / REQUIRES_REVIEW / SUCCEEDED <= captured
        captured_by_payment = {c.payment_intent_id: c for c in captures}
        for payment_uuid, public_id in payment_ids.items():
            capture = captured_by_payment.get(payment_uuid)
            captured = capture.amount if capture and capture.status == "SUCCEEDED" else 0
            reserving = [
                r.amount
                for r in refunds
                if r.payment_intent_id == payment_uuid and r.status in RESERVING
            ]
            assert sum(reserving) <= captured, f"{public_id}: refunds exceed the captured amount"
            assert sum(reserving) == self._reserved(public_id)
            assert captured == self._captured(public_id)

        # (d) exactly one journal per succeeded capture / refund, none for anything else
        capture_journals = [j for j in journals if j.journal_type == "CAPTURE"]
        refund_journals = [j for j in journals if j.journal_type == "REFUND"]
        assert len(journals) == len(capture_journals) + len(refund_journals)
        for capture in captures:
            matching = [j for j in capture_journals if j.reference_id == capture.id]
            if capture.status == "SUCCEEDED":
                assert [j.id for j in matching] == [capture.journal_id]
            else:
                assert not matching and capture.journal_id is None
        for refund_row in refunds:
            matching = [j for j in refund_journals if j.reference_id == refund_row.id]
            if refund_row.status == "SUCCEEDED":
                assert [j.id for j in matching] == [refund_row.journal_id]
            else:
                assert not matching and refund_row.journal_id is None

        # (c) the provider saw every sent key exactly once; RelayPay never re-mutates
        self.provider.assert_exactly_once()
        sent_keys = {op.stable_key for op in self.ops.values() if op.sent}
        assert self.provider.mutated_keys() == sent_keys
        assert set(self.provider.effects) == sent_keys
        for key in sent_keys:
            assert self.provider.mutation_count(key) == 1
        assert {op.public_id for op in operations if op.last_sent_at is not None} == {
            op.op_id for op in self.ops.values() if op.sent
        }

        # one idempotency record per accepted key; replays and rejections add none
        assert idempotency_records == len(self.keys)

        # derived read model and balances agree with the model
        for payment_id in self.touched & set(self.model):
            self._check_read_model(payment_id)
        self.touched.clear()
        succeeded_captured = sum(c.amount for c in captures if c.status == "SUCCEEDED")
        succeeded_refunded = sum(r.amount for r in refunds if r.status == "SUCCEEDED")
        expected_pending = succeeded_captured - succeeded_refunded
        assert pending_liability == expected_pending
        if final:  # the service's own derived view is slower; compare it once per run
            assert balances(self.factory, world).pending == expected_pending

    def _check_read_model(self, payment_id: str) -> None:
        result = read_payment(
            self.factory,
            organisation_id=self.world.organisation_id,
            environment_id=self.world.environment_id,
            payment_public_id=payment_id,
        )
        view = json.loads(result.body)
        model = self.model[payment_id]
        assert view["authorizationStatus"] == self._status(model.auth)
        assert view["captureStatus"] == self._status(model.capture)
        assert view["capturedAmount"] == self._captured(payment_id)
        assert view["reservedOrRefundedAmount"] == self._reserved(payment_id)
        assert view["refundableAmount"] == self._refundable(payment_id)

    # ----- teardown: quiesce, then re-check ---------------------------------------------

    def teardown(self) -> None:
        self.touched = set(self.model)
        for index, op in enumerate(list(self.ops.values())):
            if op.effect == "PENDING":  # the provider finally decides, deterministically
                outcome = Outcome.SUCCEED if index % 2 == 0 else Outcome.DECLINE
                self.provider.resolve(op.stable_key, outcome)
                op.effect = EFFECT[outcome]
        for _ in range(6):  # bounded: one lookup per op is enough once effects are decided
            active = [op for op in self.ops.values() if op.status in ACTIVE]
            if not active:
                break
            for op in active:
                recover(self.factory, self.world, op.op_id, self.provider)
                self._observe(op, lookup=True)
        assert all(op.status in TERMINAL for op in self.ops.values()), "operations left in flight"
        self._check(final=True)

        with self.factory() as session, session.begin():
            statuses = dict(
                session.execute(
                    select(ProviderOperation.public_id, ProviderOperation.status).where(
                        ProviderOperation.organisation_id == self.world.organisation_id
                    )
                ).all()
            )
        assert not [op for op, status in statuses.items() if status in ACTIVE]
        # Conservation: ledger and provider agree on every effect once nothing is in flight.
        for op in self.ops.values():
            effect = self.provider.effects[op.stable_key]
            assert statuses[op.op_id] == (
                "SUCCEEDED" if effect.outcome == "SUCCEEDED" else "FAILED"
            )
            assert effect.amount == op.amount
        self.provider.assert_exactly_once()


def test_payment_state_machine_never_loses_or_repeats_money(
    factory: sessionmaker[Session],
) -> None:
    run_state_machine_as_test(
        lambda: PaymentMachine(factory),
        settings=settings(stateful_step_count=30),
    )
