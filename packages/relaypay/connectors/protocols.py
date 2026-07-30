from dataclasses import dataclass
from typing import Protocol

from relaypay.provider_operations.service_types import ProviderObservation


@dataclass(frozen=True, slots=True)
class ConnectorRequest:
    stable_key: str
    body: bytes


@dataclass(frozen=True, slots=True)
class ConnectorError:
    category: str
    code: str
    retry_after_seconds: int | None = None


class ConnectorAdapter(Protocol):
    capability: str

    def request(self, command: ConnectorRequest) -> ProviderObservation: ...

    def lookup(self, stable_key: str) -> ProviderObservation: ...

    def validate(self, observation: ProviderObservation) -> bool: ...

    def classify(self, observation: ProviderObservation | None) -> ConnectorError | None: ...

    def health(self) -> ProviderObservation: ...
