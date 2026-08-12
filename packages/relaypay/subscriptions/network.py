from dataclasses import dataclass
from typing import Literal

import httpx

from relaypay.idempotency import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class RecoveryNetworkObservation:
    status: Literal["SUCCEEDED", "FAILED", "UNKNOWN"]
    code: str
    response_bytes: bytes


class DeterministicCommunicationNetwork:
    """Synthetic message service with one delivery effect per stable key."""

    def __init__(self, *, lose_first_response: bool = False) -> None:
        self._effects: dict[str, RecoveryNetworkObservation] = {}
        self._lose_first_response = lose_first_response
        self._lost: set[str] = set()

    @property
    def effect_count(self) -> int:
        return len(self._effects)

    def send(self, *, stable_key: str, channel: str, body: str) -> RecoveryNetworkObservation:
        observation = self._effects.setdefault(
            stable_key,
            RecoveryNetworkObservation(
                "SUCCEEDED",
                "DELIVERED",
                canonical_json_bytes(
                    {"stableKey": stable_key, "channel": channel, "byteLength": len(body.encode())}
                ),
            ),
        )
        if self._lose_first_response and stable_key not in self._lost:
            self._lost.add(stable_key)
            raise TimeoutError("synthetic response loss after committed message effect")
        return observation

    def lookup(self, *, stable_key: str) -> RecoveryNetworkObservation:
        return self._effects.get(
            stable_key,
            RecoveryNetworkObservation("UNKNOWN", "NOT_FOUND", canonical_json_bytes({})),
        )


class DeterministicRecurringPaymentNetwork:
    """Synthetic recurring payment service with configurable immutable outcomes."""

    def __init__(
        self,
        *,
        outcome: Literal["SUCCEEDED", "FAILED"] = "SUCCEEDED",
        failure_code: str = "INSUFFICIENT_FUNDS",
        lose_first_response: bool = False,
    ) -> None:
        self._outcome = outcome
        self._failure_code = failure_code
        self._effects: dict[str, RecoveryNetworkObservation] = {}
        self._lose_first_response = lose_first_response
        self._lost: set[str] = set()

    @property
    def effect_count(self) -> int:
        return len(self._effects)

    def retry(self, *, stable_key: str, invoice_id: str, amount: int) -> RecoveryNetworkObservation:
        code = "CAPTURED" if self._outcome == "SUCCEEDED" else self._failure_code
        observation = self._effects.setdefault(
            stable_key,
            RecoveryNetworkObservation(
                self._outcome,
                code,
                canonical_json_bytes(
                    {"stableKey": stable_key, "invoiceId": invoice_id, "amount": amount}
                ),
            ),
        )
        if self._lose_first_response and stable_key not in self._lost:
            self._lost.add(stable_key)
            raise TimeoutError("synthetic response loss after committed payment effect")
        return observation

    def lookup(self, *, stable_key: str) -> RecoveryNetworkObservation:
        return self._effects.get(
            stable_key,
            RecoveryNetworkObservation("UNKNOWN", "NOT_FOUND", canonical_json_bytes({})),
        )


def _http_observation(response: httpx.Response) -> RecoveryNetworkObservation:
    response.raise_for_status()
    value = response.json()
    status = value.get("status")
    if status not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
        raise ValueError("invalid recovery-network status")
    return RecoveryNetworkObservation(status, str(value.get("code", "UNKNOWN")), response.content)


class HTTPCommunicationNetwork:
    def __init__(self, base_url: str, *, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def send(self, *, stable_key: str, channel: str, body: str) -> RecoveryNetworkObservation:
        return _http_observation(
            httpx.post(
                f"{self._base_url}/v1/messages",
                json={"channel": channel, "body": body},
                headers={"Idempotency-Key": stable_key},
                timeout=self._timeout_seconds,
            )
        )

    def lookup(self, *, stable_key: str) -> RecoveryNetworkObservation:
        return _http_observation(
            httpx.get(f"{self._base_url}/v1/messages/{stable_key}", timeout=self._timeout_seconds)
        )


class HTTPRecurringPaymentNetwork:
    def __init__(self, base_url: str, *, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def retry(self, *, stable_key: str, invoice_id: str, amount: int) -> RecoveryNetworkObservation:
        return _http_observation(
            httpx.post(
                f"{self._base_url}/v1/payment-retries",
                json={"invoiceId": invoice_id, "amount": amount},
                headers={"Idempotency-Key": stable_key},
                timeout=self._timeout_seconds,
            )
        )

    def lookup(self, *, stable_key: str) -> RecoveryNetworkObservation:
        return _http_observation(
            httpx.get(
                f"{self._base_url}/v1/payment-retries/{stable_key}",
                timeout=self._timeout_seconds,
            )
        )
