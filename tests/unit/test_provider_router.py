import pytest
from pydantic import BaseModel, ConfigDict
from relaypay.agent_runtime.contracts import (
    ModelRequest,
    ModelResult,
    RetryableProviderError,
    TerminalModelError,
)
from relaypay.agent_runtime.providers import FakeProvider, ProviderRouter


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


class OutageProvider:
    name = "outage"

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        del request
        raise RetryableProviderError("provider outage")


def test_router_raises_after_every_provider_is_exhausted() -> None:
    router = ProviderRouter((OutageProvider(), OutageProvider(), OutageProvider()))
    with pytest.raises(RetryableProviderError, match="all configured model providers failed"):
        router.generate_structured(ModelRequest("prompt", Answer, "pinned", 32, "trace"))


def test_router_does_not_fall_back_on_terminal_model_error() -> None:
    class TerminalProvider:
        name = "terminal"

        def generate_structured(self, request: ModelRequest) -> ModelResult:
            del request
            raise TerminalModelError("schema-violating response")

    router = ProviderRouter((TerminalProvider(), FakeProvider(lambda schema: schema(value="x"))))
    with pytest.raises(TerminalModelError):
        router.generate_structured(ModelRequest("prompt", Answer, "pinned", 32, "trace"))


def test_router_requires_at_least_one_provider() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        ProviderRouter(())
