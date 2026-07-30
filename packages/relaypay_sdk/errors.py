from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RelayPayAPIError(Exception):
    status_code: int
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    retry_after: int | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"
