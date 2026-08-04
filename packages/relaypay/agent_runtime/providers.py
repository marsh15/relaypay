from __future__ import annotations

import time
from collections.abc import Callable

from opentelemetry import trace
from pydantic import BaseModel

from relaypay.agent_runtime.contracts import (
    ModelRequest,
    ModelResult,
    RetryableProviderError,
    StructuredModelProvider,
    TerminalModelError,
)
from relaypay.idempotency import canonical_json_bytes


class FakeProvider:
    name = "fake"

    def __init__(self, factory: Callable[[type[BaseModel]], BaseModel]) -> None:
        self._factory = factory

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        output = request.schema.model_validate(self._factory(request.schema))
        request_bytes = canonical_json_bytes(
            {
                "model": request.model_id,
                "prompt": request.prompt,
                "schema": request.schema.model_json_schema(),
            }
        )
        response_bytes = canonical_json_bytes(output.model_dump(mode="json"))
        return ModelResult(
            output=output,
            provider=self.name,
            model_id=request.model_id,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            latency_ms=0,
            input_tokens=max(1, len(request.prompt) // 4),
            output_tokens=max(1, len(response_bytes) // 4),
            finish_status="STOP",
        )


class JsonHttpProvider:
    """Release-pinned adapter boundary shared by OpenAI, Claude, and Gemini clients."""

    def __init__(
        self,
        *,
        name: str,
        transport: Callable[[ModelRequest], dict[str, object]],
    ) -> None:
        self.name = name
        self._transport = transport

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        started = time.monotonic_ns()
        raw = self._transport(request)
        try:
            output = request.schema.model_validate(raw["output"])
            raw_input_tokens = raw["inputTokens"]
            raw_output_tokens = raw["outputTokens"]
            if not isinstance(raw_input_tokens, int) or not isinstance(raw_output_tokens, int):
                raise TypeError("token counts must be integers")
            input_tokens = raw_input_tokens
            output_tokens = raw_output_tokens
            finish_status = str(raw["finishStatus"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TerminalModelError("provider returned an invalid structured result") from exc
        response_bytes = canonical_json_bytes(output.model_dump(mode="json"))
        return ModelResult(
            output=output,
            provider=self.name,
            model_id=request.model_id,
            request_bytes=canonical_json_bytes(
                {"model": request.model_id, "prompt": request.prompt}
            ),
            response_bytes=response_bytes,
            latency_ms=(time.monotonic_ns() - started) // 1_000_000,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_status=finish_status,
        )


class ProviderRouter:
    def __init__(self, providers: tuple[StructuredModelProvider, ...]) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = providers

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        last_error: RetryableProviderError | None = None
        for provider in self._providers:
            try:
                with trace.get_tracer("relaypay.agent_runtime").start_as_current_span(
                    "agent.model.generate",
                    attributes={"model.provider": provider.name, "model.id": request.model_id},
                ):
                    return provider.generate_structured(request)
            except RetryableProviderError as exc:
                last_error = exc
        raise RetryableProviderError("all configured model providers failed") from last_error
