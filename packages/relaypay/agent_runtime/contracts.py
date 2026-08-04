from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    prompt: str
    schema: type[BaseModel]
    model_id: str
    max_output_tokens: int
    trace_id: str


@dataclass(frozen=True, slots=True)
class ModelResult:
    output: BaseModel
    provider: str
    model_id: str
    request_bytes: bytes
    response_bytes: bytes
    latency_ms: int
    input_tokens: int
    output_tokens: int
    finish_status: str


class StructuredModelProvider(Protocol):
    name: str

    def generate_structured(self, request: ModelRequest) -> ModelResult: ...


class RetryableProviderError(Exception):
    """A timeout, connection, rate-limit, or provider 5xx failure."""


class TerminalModelError(Exception):
    """A refusal, policy failure, or schema-invalid response; never fail over."""


class ReadTool(Protocol):
    name: str
    version: int
    argument_model: type[BaseModel]

    def execute(
        self, *, organisation_id: str, environment_id: str, arguments: BaseModel
    ) -> Any: ...
