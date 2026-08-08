import httpx

from relaypay.disputes.service import NetworkObservation
from relaypay.idempotency import canonical_json_bytes


class DeterministicDisputeNetwork:
    """Synthetic network with one immutable effect per stable submission key."""

    def __init__(self, *, lose_first_response: bool = False) -> None:
        self._effects: dict[str, NetworkObservation] = {}
        self._lose_first_response = lose_first_response
        self._lost: set[str] = set()

    @property
    def effect_count(self) -> int:
        return len(self._effects)

    def submit(self, *, stable_key: str, package_bytes: bytes) -> NetworkObservation:
        observation = self._effects.setdefault(
            stable_key,
            NetworkObservation(
                "SUCCEEDED",
                "SUBMITTED",
                canonical_json_bytes({"stableKey": stable_key, "byteLength": len(package_bytes)}),
            ),
        )
        if self._lose_first_response and stable_key not in self._lost:
            self._lost.add(stable_key)
            raise TimeoutError("synthetic response loss after committed network effect")
        return observation

    def lookup(self, *, stable_key: str) -> NetworkObservation:
        return self._effects.get(
            stable_key, NetworkObservation("UNKNOWN", "NOT_FOUND", canonical_json_bytes({}))
        )


class HTTPDisputeNetwork:
    def __init__(self, base_url: str, *, timeout_seconds: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def _observation(self, response: httpx.Response) -> NetworkObservation:
        response.raise_for_status()
        body = response.content
        value = response.json()
        status = value.get("status")
        if status not in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
            raise ValueError("invalid dispute-network status")
        return NetworkObservation(status, str(value.get("code", "UNKNOWN")), body)

    def submit(self, *, stable_key: str, package_bytes: bytes) -> NetworkObservation:
        response = httpx.post(
            f"{self._base_url}/v1/submissions",
            content=package_bytes,
            headers={"Content-Type": "application/zip", "Idempotency-Key": stable_key},
            timeout=self._timeout_seconds,
        )
        return self._observation(response)

    def lookup(self, *, stable_key: str) -> NetworkObservation:
        response = httpx.get(
            f"{self._base_url}/v1/submissions/{stable_key}", timeout=self._timeout_seconds
        )
        return self._observation(response)
