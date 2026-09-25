"""Deterministic in-process provider for settlement intelligence.

The shared FakeProvider factory only receives the output schema; settlement
classification and narration must read the prompt (delimited question text or
the embedded deterministic brief), so this provider keeps the same wire result
shape while handing the full request to the fixed factories.
"""

from __future__ import annotations

from relaypay.agent_runtime.contracts import (
    ModelRequest,
    ModelResult,
    TerminalModelError,
)
from relaypay.idempotency import canonical_json_bytes
from relaypay.settlement_intelligence.classification import classification_factory
from relaypay.settlement_intelligence.narrative import narrative_factory


class SettlementFakeProvider:
    name = "fake"

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        from relaypay.settlement_intelligence.classification import QuestionClassification
        from relaypay.settlement_intelligence.narrative import SettlementNarrative

        if request.schema is QuestionClassification:
            raw: dict[str, object] = classification_factory(request.schema, request.prompt)
        elif request.schema is SettlementNarrative:
            raw = narrative_factory(request.schema, request.prompt)
        else:
            raise TerminalModelError("unsupported settlement model schema")
        output = request.schema.model_validate(raw)
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
