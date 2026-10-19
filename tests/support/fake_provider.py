"""Deterministic, thread-safe, in-process provider transport.

Implements ``relaypay.provider_operations.service.ProviderTransport`` and answers with
replies signed exactly like ``mock_provider`` (HMAC-SHA256 in ``X-Provider-Signature`` over
canonical JSON), so RelayPay's real signature, schema and identity validation runs unchanged.

Like the real provider it is idempotent per stable key: the first ``mutate`` for a key creates
the single *effect*; repeated ``mutate`` calls for that key return the recorded effect.
``mutation_count`` counts every ``mutate`` call (RelayPay should send once and recover by
lookup), ``effects`` holds at most one effect per key.
"""

import json
import threading
import uuid
from dataclasses import dataclass, replace
from enum import Enum

import httpx
from relaypay.idempotency import canonical_json_bytes
from relaypay.mock_provider.service import signature
from relaypay.provider_operations.service_types import ProviderObservation

ACCOUNT_ID = "acct_relaypay_demo"
SIGNING_SECRET = "provider-signing-secret-for-proof-tests"
DECLINE_CODE = "DO_NOT_HONOR"
_EFFECT_NAMESPACE = uuid.UUID("5d0f1c3e-9a7b-4c1d-8e2f-0a1b2c3d4e5f")


class Outcome(Enum):
    """Scripted behaviour for the first ``mutate`` of a stable key."""

    SUCCEED = "SUCCEED"
    DECLINE = "DECLINE"
    PENDING = "PENDING"
    # Effect is committed (SUCCEEDED) but the response never arrives: mutate raises.
    LOSE_RESPONSE = "LOSE_RESPONSE"
    # Same, but the committed effect is a business decline.
    LOSE_RESPONSE_DECLINE = "LOSE_RESPONSE_DECLINE"


class LostResponseError(httpx.ReadTimeout):
    """Transport error raised when the provider committed but the reply was lost.

    A subclass of the error the real ``HTTPProviderTransport`` raises on a timeout, which
    ``dispatch_operation`` maps to an ambiguous (lookup-only) outcome.
    """


@dataclass(frozen=True, slots=True)
class ProviderEffectRecord:
    stable_key: str
    effect_id: str
    request_bytes: bytes
    operation_kind: str
    reference: str
    amount: int
    outcome: str  # "SUCCEEDED" | "DECLINED" | "PENDING"
    decline_code: str | None


class FakeProvider:
    """Scriptable ``ProviderTransport``. Pass ``account_id``/``signing_secret`` to dispatch."""

    def __init__(
        self,
        *,
        account_id: str = ACCOUNT_ID,
        signing_secret: str = SIGNING_SECRET,
        default: Outcome = Outcome.SUCCEED,
    ) -> None:
        self.account_id = account_id
        self.signing_secret = signing_secret
        self.default = default
        self._lock = threading.RLock()
        self._scripts: dict[str, Outcome] = {}
        self._effects: dict[str, ProviderEffectRecord] = {}
        self._mutations: dict[str, int] = {}
        self._lookups: dict[str, int] = {}

    # ----- scripting -------------------------------------------------------------------

    def script(self, stable_key: str, outcome: Outcome) -> None:
        """Choose the outcome of the first ``mutate`` for ``stable_key`` (before it is sent)."""
        with self._lock:
            self._scripts[stable_key] = outcome

    def resolve(self, stable_key: str, outcome: Outcome) -> None:
        """Turn a PENDING effect into SUCCEED or DECLINE; later lookups report it."""
        if outcome not in {Outcome.SUCCEED, Outcome.DECLINE}:
            raise ValueError("a pending effect resolves to SUCCEED or DECLINE")
        with self._lock:
            effect = self._effects[stable_key]
            if effect.outcome != "PENDING":
                raise ValueError("only a PENDING effect can be resolved")
            declined = outcome is Outcome.DECLINE
            self._effects[stable_key] = replace(
                effect,
                outcome="DECLINED" if declined else "SUCCEEDED",
                decline_code=DECLINE_CODE if declined else None,
            )

    # ----- observation -----------------------------------------------------------------

    @property
    def effects(self) -> dict[str, ProviderEffectRecord]:
        """Snapshot of the committed effects, one per stable key."""
        with self._lock:
            return dict(self._effects)

    def mutation_count(self, stable_key: str | None = None) -> int:
        """Number of ``mutate`` calls for ``stable_key`` (or in total when omitted)."""
        with self._lock:
            if stable_key is None:
                return sum(self._mutations.values())
            return self._mutations.get(stable_key, 0)

    def lookup_count(self, stable_key: str | None = None) -> int:
        with self._lock:
            if stable_key is None:
                return sum(self._lookups.values())
            return self._lookups.get(stable_key, 0)

    def mutated_keys(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._mutations)

    def assert_exactly_once(self) -> None:
        """Assert every stable key was mutated once and produced exactly one effect."""
        with self._lock:
            repeated = {key: n for key, n in self._mutations.items() if n != 1}
            missing = set(self._mutations) - set(self._effects)
        assert not repeated, f"stable keys sent to the provider more than once: {repeated}"
        assert not missing, f"stable keys mutated without a recorded effect: {sorted(missing)}"

    # ----- ProviderTransport -----------------------------------------------------------

    def mutate(self, request_bytes: bytes) -> ProviderObservation:
        request = json.loads(request_bytes)
        stable_key = str(request["stableKey"])
        with self._lock:
            self._mutations[stable_key] = self._mutations.get(stable_key, 0) + 1
            existing = self._effects.get(stable_key)
            if existing is not None:
                if existing.request_bytes != request_bytes:
                    return self._reply(b'{"code":"PROVIDER_KEY_CONFLICT"}', status_code=409)
                return self._effect_reply(existing)
            if request["accountId"] != self.account_id:
                return self._reply(b'{"code":"PROVIDER_ACCOUNT_NOT_FOUND"}', status_code=404)
            scripted = self._scripts.get(stable_key, self.default)
            declined = scripted in {Outcome.DECLINE, Outcome.LOSE_RESPONSE_DECLINE}
            outcome = (
                "PENDING"
                if scripted is Outcome.PENDING
                else "DECLINED"
                if declined
                else "SUCCEEDED"
            )
            effect = ProviderEffectRecord(
                stable_key=stable_key,
                effect_id=str(uuid.uuid5(_EFFECT_NAMESPACE, stable_key)),
                request_bytes=request_bytes,
                operation_kind=str(request["operationKind"]),
                reference=str(request["reference"]),
                amount=int(request["amount"]),
                outcome=outcome,
                decline_code=DECLINE_CODE if declined else None,
            )
            self._effects[stable_key] = effect
            if scripted in {Outcome.LOSE_RESPONSE, Outcome.LOSE_RESPONSE_DECLINE}:
                raise LostResponseError("provider response was lost after the effect committed")
            return self._effect_reply(effect)

    def lookup(self, *, account_id: str, stable_key: str) -> ProviderObservation:
        with self._lock:
            self._lookups[stable_key] = self._lookups.get(stable_key, 0) + 1
            effect = self._effects.get(stable_key)
            if account_id != self.account_id or effect is None:
                return ProviderObservation(404, b"", {})
            return self._effect_reply(effect)

    # ----- internals -------------------------------------------------------------------

    def _effect_reply(self, effect: ProviderEffectRecord) -> ProviderObservation:
        request = json.loads(effect.request_bytes)
        body = canonical_json_bytes(
            {
                "accountId": request["accountId"],
                "amount": effect.amount,
                "currency": request["currency"],
                "declineCode": effect.decline_code,
                "effectId": effect.effect_id,
                "operationKind": effect.operation_kind,
                "outcome": effect.outcome,
                "reference": effect.reference,
                "stableKey": effect.stable_key,
            }
        )
        return self._reply(body)

    def _reply(self, body: bytes, *, status_code: int = 200) -> ProviderObservation:
        return ProviderObservation(
            status_code,
            body,
            {
                "Content-Type": "application/json",
                "X-Provider-Signature": signature(body, self.signing_secret),
            },
        )
