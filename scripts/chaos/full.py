"""Full chaos mode: drive the real Compose stack over HTTP and kill processes while it works.

    docker compose -f compose.yaml -f compose.chaos.yaml up -d --build api poller
    uv run python -m scripts.chaos.run --mode full --seeds 200 --ops 10000

What is real: the merchant API (:8000), the poller, the provider and bank simulators, Postgres.
Each seed is its own tenant (``build_world`` against the Compose database; the API key it mints is
what the seed sends). The seed's schedule becomes HTTP calls (``Idempotency-Key`` on every
command, retried with the same key on connection errors, exactly what a merchant SDK does).

Faults:

* Provider behaviour per command through the simulator's ``POST /control/faults``
  (LOSE_RESPONSE / PENDING / DECLINE) for AUTHORIZE and CAPTURE (their stable keys are known
  before the request; a refund's key is not, so refunds run un-scripted).
* Process faults from a seeded plan (``plan_process_faults``): ``docker compose kill -s SIGKILL
  api|poller`` followed by ``start``, and ``docker compose restart postgres`` followed by a
  health wait, each fired when the run's completed-operation counter crosses its position.
* Crash points inside the containers via ``RELAYPAY_CRASH_POINTS`` /
  ``RELAYPAY_CRASH_MODE=exit`` (``compose.chaos.yaml``); the operation's own ``crash`` field is
  ignored here because the containers draw their own crashes.

RECOVER_TICK and RESOLVE_PENDING are no-ops (the real poller recovers continuously; the
simulator has no endpoint that resolves a PENDING effect, so PENDING effects end in review).
SETTLE and PAYOUT call the service layer directly against the Compose database (the real
poller sends the payout to the bank). Quiescence: stop faults, restart anything that is down, wait
for the poller to drain the tenant, re-send unanswered commands, give REQUIRES_REVIEW operations
one operator ``retry_lookup``; then snapshot the RelayPay *and* provider databases.
"""

import argparse
import json
import os
import random
import subprocess
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from relaypay.contracts import EmptyCommand, PaymentIntentCreate, RefundCreate
from relaypay.errors import RelayPayError
from relaypay.idempotency import build_fingerprint, key_hint
from relaypay.proof import AckedOp, capture_snapshot, check_invariants
from relaypay.provider_operations.recovery import claim_specific_operation, recover_claim
from relaypay.provider_operations.service import HTTPProviderTransport
from sqlalchemy.exc import OperationalError

from scripts.chaos.driver import (
    SeedResult,
    _key,
    processing_operations,
    review_operations,
)
from scripts.chaos.ops import Op, OpKind, generate_schedule, schedule_stats
from tests.support.db import provider_factory, relaypay_factory
from tests.support.world import PayoutBody, World, balances, build_world, make_beneficiary, settle

COMPOSE_FILES = ("compose.yaml", "compose.chaos.yaml")
OUTPUT_DIR = Path("output/chaos")
SERVICE_START_WAIT_SECONDS = 90.0
DRAIN_TIMEOUT_SECONDS = 240.0
RETRY_ATTEMPTS = 8
PROVIDER_FAULT_TYPES = {
    "LOSE_RESPONSE": "LOSE_RESPONSE",
    "LOSE_RESPONSE_DECLINE": "LOSE_RESPONSE",
    "DECLINE": "DECLINE",
    "PENDING": "PENDING",
}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True, slots=True)
class FullConfig:
    api_url: str
    provider_url: str
    provider_account_id: str
    provider_signing_secret: str
    provider_control_secret: str

    @classmethod
    def from_env(cls) -> "FullConfig":
        return cls(
            api_url=_env("CHAOS_API_URL", "http://localhost:8000").rstrip("/"),
            provider_url=_env("CHAOS_PROVIDER_URL", "http://localhost:8001").rstrip("/"),
            provider_account_id=_env("PROVIDER_ACCOUNT_ID", "acct_relaypay_demo"),
            provider_signing_secret=_env(
                "PROVIDER_SIGNING_SECRET", "dev-provider-signing-secret-change-me"
            ),
            provider_control_secret=_env(
                "PROVIDER_CONTROL_SECRET", "dev-provider-control-secret-change-me"
            ),
        )


# ----- process-fault plan ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcessFault:
    """One scheduled process failure, fired when ``at_op`` operations have completed."""

    at_op: int
    action: str  # "kill" | "restart_postgres"
    service: str  # "api" | "poller" | "postgres"

    def commands(self) -> list[list[str]]:
        if self.action == "kill":
            return [
                compose("kill", "-s", "SIGKILL", self.service),
                compose("start", self.service),
            ]
        return [compose("restart", self.service)]


def compose(*args: str) -> list[str]:
    command = ["docker", "compose"]
    for file in COMPOSE_FILES:
        command += ["-f", file]
    return [*command, *args]


def plan_process_faults(seed: int, total_ops: int) -> list[ProcessFault]:
    """Seeded kill/restart plan over ``total_ops`` completed operations (all seeds together).

    About one service kill per 400 operations (api and poller, poller more often) and one
    Postgres restart per 3,000, never in the first 5% or last 10% of the run, and never two
    faults at the same position.
    """
    rng = random.Random(f"process-faults:{seed}")  # noqa: S311 - simulation
    kills = max(2, total_ops // 400)
    restarts = max(1, total_ops // 3000)
    low, high = max(1, total_ops // 20), max(2, total_ops - total_ops // 10)
    positions = sorted(rng.sample(range(low, high), min(kills + restarts, high - low)))
    restart_slots = set(rng.sample(range(len(positions)), min(restarts, len(positions))))
    plan: list[ProcessFault] = []
    for slot, position in enumerate(positions):
        if slot in restart_slots:
            plan.append(ProcessFault(position, "restart_postgres", "postgres"))
        else:
            plan.append(ProcessFault(position, "kill", "poller" if rng.random() < 0.6 else "api"))
    return plan


def format_plan(plan: list[ProcessFault]) -> list[str]:
    lines = []
    for fault in plan:
        commands = " && ".join(" ".join(command) for command in fault.commands())
        lines.append(f"  after {fault.at_op:>7} ops: {commands}")
    return lines


class ProcessChaos:
    """Fires the plan as the shared completed-operation counter advances."""

    def __init__(self, plan: list[ProcessFault], config: FullConfig) -> None:
        self.plan = plan
        self.config = config
        self.completed = 0
        self.fired: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="process-chaos", daemon=True)

    def tick(self) -> None:
        with self._lock:
            self.completed += 1

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=SERVICE_START_WAIT_SECONDS * 2)

    def _run(self) -> None:
        pending = list(self.plan)
        while pending and not self._stop.is_set():
            with self._lock:
                done = self.completed
            if done >= pending[0].at_op:
                fault = pending.pop(0)
                self._execute(fault)
            else:
                self._stop.wait(0.25)

    def _execute(self, fault: ProcessFault) -> None:
        for command in fault.commands():
            subprocess.run(command, check=False, capture_output=True, timeout=120)  # noqa: S603
        wait_for_service(fault.service, self.config)
        self.fired.append(f"{fault.action}:{fault.service}@{fault.at_op}")


def wait_for_service(service: str, config: FullConfig) -> bool:
    """Block until ``service`` answers again (False on timeout)."""
    deadline = time.monotonic() + SERVICE_START_WAIT_SECONDS
    while time.monotonic() < deadline:
        if service == "postgres":
            probe = subprocess.run(  # noqa: S603
                compose("exec", "-T", "postgres", "pg_isready", "-U", "postgres", "-d", "postgres"),
                check=False,
                capture_output=True,
                timeout=30,
            )
            if probe.returncode == 0:
                return True
        elif service == "api":
            try:
                if httpx.get(f"{config.api_url}/health/ready", timeout=3).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
        else:  # the poller has no health endpoint; give it a moment to start ticking
            time.sleep(3)
            return True
        time.sleep(1)
    return False


def ensure_stack_up(config: FullConfig) -> None:
    subprocess.run(  # noqa: S603
        compose("start", "postgres", "api", "poller"), check=False, capture_output=True, timeout=180
    )
    for service in ("postgres", "api"):
        wait_for_service(service, config)


# ----- one seed over HTTP ----------------------------------------------------------------------


class Rejected(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"{status} {code}")
        self.code = code


@dataclass(slots=True)
class HttpCommand:
    kind: str
    key: str
    op_index: int
    path: str
    body: dict[str, Any]
    payment_id: str | None
    amount: int
    outcome: str | None


class FullSeed:
    def __init__(
        self,
        seed: int,
        config: FullConfig,
        factory: Any,
        world: World,
        beneficiary_id: str,
        chaos: ProcessChaos,
        result: SeedResult,
    ) -> None:
        self.seed = seed
        self.config = config
        self.factory = factory
        self.world = world
        self.beneficiary_id = beneficiary_id
        self.chaos = chaos
        self.result = result
        self.client = httpx.Client(
            base_url=f"{config.api_url}/api/v1",
            headers={"Authorization": f"Bearer {world.api_key}"},
            timeout=15,
        )
        self.lock = threading.RLock()
        self.payments: dict[int, str] = {}
        self.payment_amounts: dict[str, int] = {}
        self.specs: dict[int, HttpCommand] = {}
        self.answered: set[str] = set()
        self.unanswered: dict[str, HttpCommand] = {}
        self.acked: dict[tuple[str, str, str], AckedOp] = {}
        self.rejections: Counter[str] = Counter()
        self.faults: Counter[str] = Counter()
        self.scripted: set[str] = set()

    def close(self) -> None:
        self.client.close()

    # -- HTTP --

    def send(self, command: HttpCommand) -> dict[str, Any] | None:
        """POST with the command's key, retrying connection failures and 5xx with the same key."""
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = self.client.post(
                    command.path, json=command.body, headers={"Idempotency-Key": command.key}
                )
            except httpx.TransportError:
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code >= 500:
                time.sleep(0.5 * (attempt + 1))
                continue
            if response.status_code >= 400:
                error = response.json().get("error", {})
                raise Rejected(response.status_code, str(error.get("code", "UNKNOWN")))
            return dict(response.json())
        return None

    def script_provider(self, command: HttpCommand) -> None:
        if not command.outcome or command.kind not in {"AUTHORIZE", "CAPTURE"}:
            return
        fault_type = PROVIDER_FAULT_TYPES.get(command.outcome)
        stable_key = f"{command.kind.lower()}:{command.payment_id}"
        if fault_type is None or stable_key in self.scripted:
            return
        self.scripted.add(stable_key)
        httpx.post(
            f"{self.config.provider_url}/control/faults",
            headers={"X-Provider-Control": self.config.provider_control_secret},
            json={
                "accountId": self.config.provider_account_id,
                "stableKey": stable_key,
                "faultType": fault_type,
            },
            timeout=10,
        ).raise_for_status()
        self.faults[command.outcome] += 1

    # -- commands --

    def build(self, op: Op, command: str) -> HttpCommand | None:
        if command == "CREATE":
            payload = PaymentIntentCreate(
                customer_id=self.world.customer_public_id,
                merchant_reference=f"chaos-{self.seed}-{op.index}",
                amount=op.amount or 1,
                currency="INR",
            )
            return HttpCommand(
                "CREATE",
                _key(self.seed, op.index, "create"),
                op.index,
                "/payment_intents",
                payload.model_dump(mode="json"),
                None,
                payload.amount,
                None,
            )
        if op.target is None:
            payment_id = "pay_" + "0" * 32
        else:
            found = self.payments.get(op.target)
            if found is None:
                return None
            payment_id = found
        key = _key(self.seed, op.index, command[:3].lower())
        if command == "REFUND":
            refund = RefundCreate(
                amount=op.amount or 1,
                currency="INR",
                merchant_refund_reference=f"chaos-{self.seed}-{op.index}",
            )
            return HttpCommand(
                "REFUND",
                key,
                op.index,
                f"/payment_intents/{payment_id}/refunds",
                refund.model_dump(mode="json"),
                payment_id,
                refund.amount,
                None,
            )
        return HttpCommand(
            command,
            key,
            op.index,
            f"/payment_intents/{payment_id}/{command.lower()}",
            EmptyCommand().model_dump(mode="json"),
            payment_id,
            self.payment_amounts.get(payment_id, 1_000),
            op.outcome,
        )

    def execute(self, command: HttpCommand, *, inject: bool = True) -> None:
        if inject:
            self.script_provider(command)
        answer = self.send(command)
        if answer is None:
            with self.lock:
                self.unanswered[command.key] = command
            return
        with self.lock:
            self.answered.add(command.key)
            self.unanswered.pop(command.key, None)
            if command.kind == "CREATE":
                self.payments.setdefault(command.op_index, str(answer["id"]))
                self.payment_amounts[str(answer["id"])] = command.amount
            elif isinstance(answer.get("resourceId"), str):
                hint = key_hint(command.key)
                resource_id = str(answer["resourceId"])
                self.acked[(command.kind, resource_id, hint)] = AckedOp(
                    command.kind, resource_id, hint, command.amount
                )

    def run_op(self, op: Op) -> None:
        try:
            self._run_op(op)
        except Rejected as error:
            self.rejections[error.code] += 1
        except (OperationalError, httpx.HTTPError) as error:  # stack is being killed under us
            self.result.skipped += 1
            self.rejections[f"transient:{type(error).__name__}"] += 1
        except Exception as error:
            if len(self.result.failures) < 25:
                self.result.failures.append(
                    f"op {op.index} {op.kind}: unexpected {type(error).__name__}: {error}"
                )
        finally:
            self.chaos.tick()

    def _run_op(self, op: Op) -> None:
        kind = op.kind
        if kind in {OpKind.RECOVER_TICK, OpKind.RESOLVE_PENDING}:
            self.result.skipped += 1
            return
        if kind == OpKind.SETTLE:
            settle(self.factory, self.world)
            return
        if kind == OpKind.PAYOUT:
            self._payout(op)
            return
        if kind == OpKind.REPLAY:
            replayed = self.specs.get(op.ref if op.ref is not None else -1)
            if replayed is None:
                self.result.skipped += 1
                return
            self.execute(replayed, inject=False)
            return
        command = op.command if kind == OpKind.CONCURRENT_DUPLICATE else kind
        built = self.build(op, str(command))
        if built is None:
            self.result.skipped += 1
            return
        self.specs[op.index] = built
        if kind == OpKind.CONCURRENT_DUPLICATE:
            self.script_provider(built)
            threads = [
                threading.Thread(target=self._safe_execute, args=(built,))
                for _ in range(op.threads or 2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            return
        self.execute(built)

    def _safe_execute(self, command: HttpCommand) -> None:
        try:
            self.execute(command, inject=False)
        except Rejected as error:
            self.rejections[error.code] += 1
        except httpx.HTTPError:
            self.rejections["transient:HTTPError"] += 1

    def _payout(self, op: Op) -> None:
        from relaypay.payouts.service import create_payout

        eligible = balances(self.factory, self.world).payout_eligible
        if eligible <= 0:
            self.result.skipped += 1
            return
        amount = eligible + 1 if op.invalid else max(1, eligible * (op.pct or 100) // 100)
        body = PayoutBody(
            merchant_account_id=self.world.merchant_public_id,
            beneficiary_id=self.beneficiary_id,
            amount=amount,
        )
        try:
            create_payout(
                self.factory,
                principal=self.world.admin_principal,
                environment_public_id=self.world.environment_public_id,
                merchant_public_id=self.world.merchant_public_id,
                beneficiary_public_id=self.beneficiary_id,
                amount=amount,
                idempotency_key=_key(self.seed, op.index, "pay"),
                fingerprint=build_fingerprint(
                    api_version="admin-v1",
                    method="POST",
                    route_template="/environments/{environment_id}/payouts",
                    path_params={"environment_id": self.world.environment_public_id},
                    body=body,
                ),
                key_pepper=self.world.key_pepper,
            )
        except RelayPayError as error:
            self.rejections[error.code] += 1

    # -- quiescence --

    def quiesce(self) -> None:
        result = self.result
        organisation_id = self.world.organisation_id
        for command in sorted(self.unanswered.values(), key=lambda c: c.op_index):
            result.unacked_retried += 1
            try:
                self.execute(command, inject=False)
            except Rejected as error:
                self.rejections[error.code] += 1
                self.unanswered.pop(command.key, None)
        deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
        passes = 0
        while time.monotonic() < deadline:
            passes += 1
            if not processing_operations(self.factory, organisation_id):
                break
            time.sleep(2)
        result.quiesce_passes = passes
        transport = HTTPProviderTransport(base_url=self.config.provider_url)
        for operation_id in review_operations(self.factory, organisation_id):
            try:
                claim = claim_specific_operation(
                    self.factory,
                    organisation_id=organisation_id,
                    operation_public_id=operation_id,
                    require_review=True,
                )
                recover_claim(
                    self.factory,
                    claim=claim,
                    provider_account_id=self.config.provider_account_id,
                    provider_signing_secret=self.config.provider_signing_secret,
                    transport=transport,
                    actor_type="ADMIN_LOOKUP",
                )
            except RelayPayError as error:
                self.rejections[error.code] += 1
        result.review_remaining = len(review_operations(self.factory, organisation_id))
        result.quiesced = not processing_operations(self.factory, organisation_id)

    def finish(self) -> None:
        result = self.result
        result.rejections = dict(sorted(self.rejections.items()))
        result.faults = dict(sorted(self.faults.items()))
        result.acked_ops = len(self.acked)
        if not result.quiesced:
            result.failures.append("did not quiesce: operations still PROCESSING after the drain")
        if self.unanswered:
            result.failures.append(f"{len(self.unanswered)} commands never got an answer")
        with provider_factory("chaos-full-provider") as provider_sessions:
            snapshot = capture_snapshot(
                self.factory,
                provider_sessions,
                organisation_ids=[self.world.organisation_id],
                acked_ops=sorted(
                    self.acked.values(),
                    key=lambda a: (a.kind, a.resource_id, a.idempotency_key_hint),
                ),
            )
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
        result.violations = [
            {"code": v.code, "subject": v.subject, "message": v.message} for v in violations[:50]
        ]


# ----- entry point -----------------------------------------------------------------------------


def run_full(args: argparse.Namespace) -> int:
    from scripts.chaos.run import NOT_EVALUATED, resolve_seeds

    seeds = resolve_seeds(args)
    n_ops = int(args.ops)
    total = len(seeds) * n_ops
    plan = plan_process_faults(seeds[0] if seeds else 0, total)
    config = FullConfig.from_env()
    if args.dry_run:
        print(f"chaos[full] dry run: {len(seeds)} seeds x {n_ops} ops = {total} operations")
        print(f"api {config.api_url}, provider {config.provider_url} (nothing is touched)")
        for seed in seeds[:3]:
            print(f"  seed {seed}: {json.dumps(schedule_stats(generate_schedule(seed, n_ops)))}")
        print(f"process fault plan ({len(plan)} faults):")
        print("\n".join(format_plan(plan)))
        return 0

    workers = int(args.workers) if args.workers else min(4, len(seeds))
    print(
        f"chaos[full] {len(seeds)} seeds x {n_ops} ops, {workers} parallel, "
        f"{len(plan)} process faults planned",
        flush=True,
    )
    started_at = datetime.now(UTC)
    started = time.monotonic()
    chaos = ProcessChaos(plan, config)
    results: list[SeedResult] = []
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with relaypay_factory("chaos-full", pool_size=workers + 4, max_overflow=4) as factory:

        def phase_one(seed: int) -> FullSeed:
            ops = generate_schedule(seed, n_ops)
            result = SeedResult(seed=seed, n_ops=n_ops)
            result.schedule = [op.to_dict() for op in ops]
            result.ops_by_kind = dict(schedule_stats(ops)["by_kind"])
            world = replace(
                build_world(factory, label=f"chaos-full-{seed}"),
                provider_account_id=config.provider_account_id,
                provider_signing_secret=config.provider_signing_secret,
            )
            seeded = FullSeed(
                seed, config, factory, world, make_beneficiary(factory, world), chaos, result
            )
            t0 = time.monotonic()
            for op in ops:
                seeded.run_op(op)
                result.executed += 1
            result.elapsed = round(time.monotonic() - t0, 2)
            return seeded

        chaos.start()
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                seeded_runs = list(pool.map(phase_one, seeds))
        finally:
            chaos.stop()
        ensure_stack_up(config)
        for seeded in seeded_runs:
            try:
                seeded.quiesce()
                seeded.finish()
            except Exception as error:
                seeded.result.failures.append(f"quiesce/snapshot: {type(error).__name__}: {error}")
            finally:
                seeded.close()
            results.append(seeded.result)

    elapsed = time.monotonic() - started
    failing = [r for r in results if r.failed]
    skipped = all(r.invariants == "SKIPPED" for r in results)
    summary = {
        "mode": "full",
        "started_at": started_at.isoformat(),
        "seeds": seeds,
        "ops_per_seed": n_ops,
        "workers": workers,
        "process_faults_planned": [f"{f.action}:{f.service}@{f.at_op}" for f in plan],
        "process_faults_fired": chaos.fired,
        "elapsed_seconds": round(elapsed, 2),
        "invariants": "SKIPPED"
        if skipped
        else {"violations": sum(len(r.violations) for r in results)},
        "failed_seeds": [r.seed for r in failing],
        "per_seed": [r.summary() for r in results],
    }
    path = OUTPUT_DIR / f"summary-full-{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    for result in failing:
        (OUTPUT_DIR / f"seed-{result.seed}.json").write_text(
            json.dumps(
                {
                    **result.summary(),
                    "failures": result.failures,
                    "violations": result.violations,
                    "schedule": result.schedule,
                },
                indent=2,
                sort_keys=True,
            )
        )
        print(
            f"repro seed {result.seed}: uv run python -m scripts.chaos.run --mode full "
            f"--seeds-list {result.seed} --ops {n_ops}"
        )
    print(f"elapsed={elapsed:.1f}s process_faults_fired={len(chaos.fired)}/{len(plan)}")
    if skipped:
        print(NOT_EVALUATED)
    print(f"harness: {'FAIL' if failing else 'PASS'} ({len(failing)} failing seeds)")
    print(f"summary: {path}")
    if failing:
        return 1
    if skipped and args.require_invariants:
        return 2
    return 0
