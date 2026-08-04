import re
from dataclasses import dataclass
from typing import Final

from opentelemetry import trace
from pydantic import BaseModel

from relaypay.agent_runtime.contracts import ReadTool
from relaypay.idempotency import canonical_json_bytes

MAX_TOOL_RESULT_BYTES: Final = 262_144
_UNTRUSTED_OPEN: Final = "<relaypay-untrusted-evidence>"
_UNTRUSTED_CLOSE: Final = "</relaypay-untrusted-evidence>"
_PII_PATTERN = re.compile(
    r"(?P<email>[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})|"
    r"(?P<phone>(?<!\d)(?:\+91[ -]?)?[6-9]\d{9}(?!\d))|"
    r"(?P<account>\b(?:acct|account|msg|message)[_-]?[A-Za-z0-9]{5,}\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class TokenizedText:
    text: str
    values: tuple[str, ...]

    def restore(self, rendered: str) -> str:
        restored = rendered
        for index, value in enumerate(self.values, start=1):
            restored = restored.replace(f"{{{{PII_{index}}}}}", value)
        return restored


def tokenize_pii(value: str) -> TokenizedText:
    values: list[str] = []

    def replace(match: re.Match[str]) -> str:
        values.append(match.group(0))
        return f"{{{{PII_{len(values)}}}}}"

    return TokenizedText(text=_PII_PATTERN.sub(replace, value), values=tuple(values))


def delimit_untrusted(value: str) -> str:
    escaped = value.replace(_UNTRUSTED_CLOSE, "&lt;/relaypay-untrusted-evidence&gt;")
    return f"{_UNTRUSTED_OPEN}\n{escaped}\n{_UNTRUSTED_CLOSE}"


class ToolRegistry:
    def __init__(self, tools: tuple[ReadTool, ...]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def execute(
        self,
        *,
        name: str,
        organisation_id: str,
        environment_id: str,
        raw_arguments: dict[str, object],
    ) -> bytes:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError("tool is not allowlisted")
        arguments: BaseModel = tool.argument_model.model_validate(raw_arguments)
        with trace.get_tracer("relaypay.agent_runtime").start_as_current_span(
            "agent.tool.execute",
            attributes={"tool.name": tool.name, "tool.version": tool.version},
        ):
            result = canonical_json_bytes(
                tool.execute(
                    organisation_id=organisation_id,
                    environment_id=environment_id,
                    arguments=arguments,
                )
            )
        if len(result) > MAX_TOOL_RESULT_BYTES:
            raise ValueError("tool result exceeds the bounded output limit")
        return result
