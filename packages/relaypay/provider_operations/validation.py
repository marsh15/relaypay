import hashlib
import hmac
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from relaypay.provider_operations.service_types import ProviderObservation

ProviderAction = Literal[
    "FINALIZE_SUCCESS",
    "FINALIZE_FAILURE",
    "LOOKUP",
    "PROCESSING",
    "REVIEW",
]


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    accountId: str
    stableKey: str
    operationKind: Literal["AUTHORIZE", "CAPTURE", "REFUND"]
    reference: str
    amount: int
    currency: Literal["INR"]
    outcome: Literal["PENDING", "SUCCEEDED", "DECLINED"]
    declineCode: str | None
    effectId: str

    @model_validator(mode="after")
    def outcome_is_consistent(self) -> "ProviderResult":
        if self.outcome == "DECLINED" and not self.declineCode:
            raise ValueError("declined result requires a business decline code")
        if self.outcome != "DECLINED" and self.declineCode is not None:
            raise ValueError("non-declined result cannot carry a decline code")
        return self


@dataclass(frozen=True, slots=True)
class ExpectedProviderResult:
    account_id: str
    stable_key: str
    operation_kind: str
    reference: str
    amount: int
    currency: str


@dataclass(frozen=True, slots=True)
class ClassifiedProviderResult:
    action: ProviderAction
    classification: str
    signature_valid: bool | None
    safe_error_code: str | None = None
    decline_code: str | None = None
    effect_id: str | None = None


def transport_error_result() -> ClassifiedProviderResult:
    return ClassifiedProviderResult(
        action="LOOKUP",
        classification="AMBIGUOUS_TRANSPORT",
        signature_valid=None,
        safe_error_code="PROVIDER_TRANSPORT_ERROR",
    )


def classify_provider_observation(
    observation: ProviderObservation,
    *,
    expected: ExpectedProviderResult,
    signing_secret: str,
    is_lookup: bool,
) -> ClassifiedProviderResult:
    if observation.status_code >= 500:
        return ClassifiedProviderResult(
            action="LOOKUP" if not is_lookup else "PROCESSING",
            classification="AMBIGUOUS_HTTP_5XX",
            signature_valid=None,
            safe_error_code="PROVIDER_HTTP_5XX",
        )
    if not 200 <= observation.status_code < 300:
        return ClassifiedProviderResult(
            action="LOOKUP" if not is_lookup else "PROCESSING",
            classification="NON_BUSINESS_HTTP_4XX",
            signature_valid=None,
            safe_error_code="PROVIDER_NON_BUSINESS_HTTP",
        )

    supplied_signature = next(
        (
            value
            for name, value in observation.headers.items()
            if name.lower() == "x-provider-signature"
        ),
        None,
    )
    expected_signature = hmac.new(
        signing_secret.encode("utf-8"), observation.body, hashlib.sha256
    ).hexdigest()
    if supplied_signature is None or not hmac.compare_digest(
        supplied_signature, expected_signature
    ):
        return ClassifiedProviderResult(
            action="REVIEW",
            classification="INVALID_SIGNATURE",
            signature_valid=False,
            safe_error_code="INVALID_EVIDENCE",
        )

    try:
        result = ProviderResult.model_validate_json(observation.body)
    except ValidationError:
        return ClassifiedProviderResult(
            action="REVIEW",
            classification="INVALID_SCHEMA",
            signature_valid=True,
            safe_error_code="INVALID_EVIDENCE",
        )

    actual_identity = (
        result.accountId,
        result.stableKey,
        result.operationKind,
        result.reference,
        result.amount,
        result.currency,
    )
    expected_identity = (
        expected.account_id,
        expected.stable_key,
        expected.operation_kind,
        expected.reference,
        expected.amount,
        expected.currency,
    )
    if actual_identity != expected_identity:
        return ClassifiedProviderResult(
            action="REVIEW",
            classification="MISMATCHED_EVIDENCE",
            signature_valid=True,
            safe_error_code="INVALID_EVIDENCE",
            effect_id=result.effectId,
        )

    if result.outcome == "SUCCEEDED":
        return ClassifiedProviderResult(
            action="FINALIZE_SUCCESS",
            classification="VERIFIED_SUCCESS",
            signature_valid=True,
            effect_id=result.effectId,
        )
    if result.outcome == "DECLINED":
        return ClassifiedProviderResult(
            action="FINALIZE_FAILURE",
            classification="VERIFIED_BUSINESS_DECLINE",
            signature_valid=True,
            decline_code=result.declineCode,
            effect_id=result.effectId,
        )
    return ClassifiedProviderResult(
        action="PROCESSING",
        classification="VERIFIED_PENDING",
        signature_valid=True,
        effect_id=result.effectId,
    )
