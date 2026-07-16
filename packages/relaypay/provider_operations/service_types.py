from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    status_code: int
    body: bytes
    headers: dict[str, str]
