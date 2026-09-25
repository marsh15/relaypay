"""Narrative generation and post-generation validation for settlement answers.

The narrative model receives only the deterministic result payload and may only
describe it. Validation rejects altered numeric values, unsupported claims,
missing citations, cross-tenant record references, and malformed output before
anything is persisted or returned.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from relaypay.errors import RelayPayError
from relaypay.settlement_intelligence.queries import (
    RECORD_ID_PREFIXES,
    DeterministicResult,
)
from relaypay.settlement_intelligence.windows import format_inr

NARRATIVE_BRIEF_MARKER = "DETERMINISTIC_RESULT_JSON:"

NARRATIVE_TEMPLATE = (
    "Describe the deterministic settlement result below for the operator. "
    "Use only the supplied numbers, dates, and record identifiers; cite every "
    "required record identifier from requiredCitationIds in citedRecordIds. "
    "Never invent values, promises, or records."
)

UNSUPPORTED_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"guarantee",
        r"risk[- ]free",
        r"\bno risk\b",
        r"certain to",
        r"will always",
        r"fee[- ]free",
        r"error[- ]free",
        r"100% safe",
        r"ignore (?:all |any )?instructions",
        r"system prompt",
        r"relaypay-untrusted-evidence",
    )
)

_NUMBER_TOKEN = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RECORD_ID = re.compile(r"(?<![a-z0-9_])(?:" + "|".join(RECORD_ID_PREFIXES) + r")_[0-9a-f]{32}")


class SettlementNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    narrative: str = Field(min_length=20, max_length=4000)
    cited_record_ids: list[str] = Field(alias="citedRecordIds", max_length=128)


def narrative_prompt(result: DeterministicResult) -> str:
    brief = {
        **result.payload(),
        "requiredCitationIds": list(result.required_citation_ids),
    }
    return (
        NARRATIVE_TEMPLATE
        + "\n"
        + NARRATIVE_BRIEF_MARKER
        + "\n"
        + json.dumps(brief, sort_keys=True, separators=(",", ":"), default=str)
    )


def allowed_number_tokens(numbers: dict[str, int], dates: dict[str, str]) -> set[str]:
    tokens: set[str] = {"0", "0.00"}
    for number in numbers.values():
        tokens.add(str(number))
        tokens.add(f"{number:02d}")
        tokens.add(f"{number / 100:.2f}")
    for stamp in dates.values():
        for part in re.findall(r"\d+", stamp):
            tokens.add(part)
            tokens.add(str(int(part)))
    return tokens


def validate_narrative(narrative: SettlementNarrative, result: DeterministicResult) -> None:
    text = narrative.narrative
    for pattern in UNSUPPORTED_CLAIM_PATTERNS:
        if pattern.search(text):
            raise RelayPayError(
                code="SETTLEMENT_NARRATIVE_UNSUPPORTED_CLAIM",
                message="Narrative contains an unsupported claim",
                http_status=422,
            )
    allowed = allowed_number_tokens(result.numbers, result.dates)
    scrubbed = _ISO_DATE.sub(" ", text)
    for token in _NUMBER_TOKEN.findall(scrubbed):
        normalized = token.replace(",", "")
        if normalized not in allowed and normalized.lstrip("-") not in allowed:
            raise RelayPayError(
                code="SETTLEMENT_NARRATIVE_NUMBER_MISMATCH",
                message="Narrative altered or invented a numeric value",
                http_status=422,
                details={"token": normalized},
            )
    supplied_ids = {citation.record_id for citation in result.citations}
    for record_id in _RECORD_ID.findall(text):
        if record_id not in supplied_ids:
            raise RelayPayError(
                code="SETTLEMENT_NARRATIVE_CROSS_RECORD_REFERENCE",
                message="Narrative referenced a record outside the deterministic result",
                http_status=422,
                details={"recordId": record_id},
            )
    cited_ids = set(narrative.cited_record_ids)
    unknown = sorted(cited_ids - supplied_ids)
    if unknown:
        raise RelayPayError(
            code="SETTLEMENT_NARRATIVE_CITATION_UNKNOWN",
            message="Narrative cited an unknown record",
            http_status=422,
            details={"recordIds": unknown},
        )
    missing = [
        record_id for record_id in result.required_citation_ids if record_id not in cited_ids
    ]
    if missing:
        raise RelayPayError(
            code="SETTLEMENT_NARRATIVE_MISSING_CITATION",
            message="Narrative is missing a required citation",
            http_status=422,
            details={"recordIds": missing},
        )


def _extract_brief(prompt: str) -> dict[str, Any]:
    marker_at = prompt.find(NARRATIVE_BRIEF_MARKER)
    if marker_at == -1:
        return {}
    payload = prompt[marker_at + len(NARRATIVE_BRIEF_MARKER) :].strip()
    decoded, _ = json.JSONDecoder().raw_decode(payload)
    return decoded if isinstance(decoded, dict) else {}


def narrative_factory(schema: type[BaseModel], prompt: str) -> dict[str, object]:
    """FakeProvider factory: template narration of the supplied deterministic result."""
    brief = _extract_brief(prompt)
    required = brief.get("requiredCitationIds") or []
    numbers = {str(key): int(value) for key, value in (brief.get("numbers") or {}).items()}
    dates = {str(key): str(value) for key, value in (brief.get("dates") or {}).items()}
    intent = str(brief.get("intent") or "")
    sentences: list[str] = []
    if intent == "SETTLEMENT_LOWER":
        if numbers.get("forecastAvailable") == 1:
            sentences.append(
                f"The latest pre-cutoff forecast expected "
                f"{format_inr(numbers.get('forecastExpectedSettlement', 0))} to settle, and "
                f"the completed runs today settled "
                f"{format_inr(numbers.get('actualSettlement', 0))}, a delta of "
                f"{format_inr(numbers.get('settlementDelta', 0))}."
            )
            sentences.append(
                f"Refunds completed after that forecast reduced the outcome by "
                f"{format_inr(numbers.get('lateRefundTotal', 0))}, captures recorded before "
                f"the cutoff added {format_inr(numbers.get('lateCaptureTotal', 0))}, and new "
                f"receivable positions diverted "
                f"{format_inr(numbers.get('receivableOffsetDelta', 0))}."
            )
        else:
            sentences.append(
                f"No pre-cutoff forecast exists for {dates.get('questionDate', '')}, so "
                f"today's completed runs settled "
                f"{format_inr(numbers.get('actualSettlement', 0))} against a zero baseline."
            )
    elif intent == "UNSETTLED_PAYMENTS":
        sentences.append(
            f"{numbers.get('unsettledCount', 0)} captured payments are still unsettled, "
            f"totalling {format_inr(numbers.get('unsettledTotal', 0))}, with the earliest "
            f"expected arrival on "
            f"{dates.get('expectedArrival', dates.get('questionDate', ''))}."
        )
    elif intent == "REFUND_IMPACT":
        sentences.append(
            f"Refunds today total {format_inr(numbers.get('refundTotal', 0))}: "
            f"{format_inr(numbers.get('reducesPendingPayable', 0))} reduced pending payable, "
            f"{format_inr(numbers.get('drawsAvailablePayable', 0))} drew from available "
            f"payable, and {format_inr(numbers.get('createsReceivable', 0))} created "
            f"merchant receivable."
        )
    elif intent == "ARRIVAL_TOMORROW":
        sentences.append(
            f"Tomorrow ({dates.get('businessDate', '')}) is expected to settle "
            f"{format_inr(numbers.get('expectedSettlement', 0))} after "
            f"{format_inr(numbers.get('expectedCaptureTotal', 0))} of captures, "
            f"{format_inr(numbers.get('expectedRefundTotal', 0))} of refunds, and a "
            f"{format_inr(numbers.get('receivableOffsetTotal', 0))} receivable offset, "
            f"arriving {dates.get('expectedArrival', '')}."
        )
    else:
        sentences.append("The deterministic result is available in the supplied records.")
    narrative = " ".join(sentences)
    return {"narrative": narrative, "citedRecordIds": list(required)}
