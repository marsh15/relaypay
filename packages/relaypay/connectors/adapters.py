import hashlib
import hmac

import httpx2

from relaypay.connectors.protocols import ConnectorError, ConnectorRequest
from relaypay.provider_operations.service_types import ProviderObservation


class SignedHTTPConnectorAdapter:
    capability = "generic.signed_effect"

    def __init__(
        self,
        *,
        base_url: str,
        mutation_path: str,
        lookup_path: str,
        health_path: str,
        signing_secret: str,
        timeout_seconds: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.mutation_path = mutation_path
        self.lookup_path = lookup_path
        self.health_path = health_path
        self.signing_secret = signing_secret
        self.timeout_seconds = timeout_seconds

    def request(self, command: ConnectorRequest) -> ProviderObservation:
        response = httpx2.post(
            self.base_url + self.mutation_path,
            content=command.body,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout_seconds,
        )
        return ProviderObservation(response.status_code, response.content, dict(response.headers))

    def lookup(self, stable_key: str) -> ProviderObservation:
        response = httpx2.get(
            self.base_url + self.lookup_path.format(stable_key=stable_key),
            timeout=self.timeout_seconds,
        )
        return ProviderObservation(response.status_code, response.content, dict(response.headers))

    def health(self) -> ProviderObservation:
        response = httpx2.get(
            self.base_url + self.health_path,
            timeout=self.timeout_seconds,
        )
        return ProviderObservation(response.status_code, response.content, dict(response.headers))

    def validate(self, observation: ProviderObservation) -> bool:
        if observation.status_code != 200:
            return False
        signature = next(
            (
                value
                for key, value in observation.headers.items()
                if key.lower() in {"x-provider-signature", "x-bank-signature"}
            ),
            None,
        )
        if signature is None:
            return self.health_path in {"/health/live", "/health/connector"}
        expected = hmac.new(
            self.signing_secret.encode(), observation.body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def classify(self, observation: ProviderObservation | None) -> ConnectorError | None:
        if observation is None or observation.status_code >= 500:
            return ConnectorError("AMBIGUOUS", "CONNECTOR_OUTCOME_AMBIGUOUS")
        if observation.status_code == 429:
            retry_after = observation.headers.get("Retry-After")
            return ConnectorError(
                "RATE_LIMITED",
                "CONNECTOR_RATE_LIMITED",
                int(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if observation.status_code >= 400:
            return ConnectorError("PERMANENT", "CONNECTOR_REQUEST_REJECTED")
        return None


class PaymentConnectorAdapter(SignedHTTPConnectorAdapter):
    capability = "payments.effects"

    def __init__(self, *, base_url: str, signing_secret: str, timeout_seconds: float) -> None:
        super().__init__(
            base_url=base_url,
            mutation_path="/v1/effects",
            lookup_path="/v1/effects/{stable_key}",
            health_path="/health/live",
            signing_secret=signing_secret,
            timeout_seconds=timeout_seconds,
        )


class BankConnectorAdapter(SignedHTTPConnectorAdapter):
    capability = "payouts.transfers"

    def __init__(self, *, base_url: str, signing_secret: str, timeout_seconds: float) -> None:
        super().__init__(
            base_url=base_url,
            mutation_path="/v1/transfers",
            lookup_path="/v1/transfers/{stable_key}",
            health_path="/health/live",
            signing_secret=signing_secret,
            timeout_seconds=timeout_seconds,
        )
