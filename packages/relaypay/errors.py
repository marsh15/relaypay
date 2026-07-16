from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RelayPayError(Exception):
    code: str
    message: str
    http_status: int
    details: dict[str, Any] = field(default_factory=dict)
    retry_after: int | None = None


def not_found(resource: str) -> RelayPayError:
    return RelayPayError(
        code="RESOURCE_NOT_FOUND",
        message=f"{resource} was not found",
        http_status=404,
    )
