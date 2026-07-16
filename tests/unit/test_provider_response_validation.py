import hashlib
import hmac

import pytest
from relaypay.idempotency import canonical_json_bytes
from relaypay.provider_operations.service_types import ProviderObservation
from relaypay.provider_operations.validation import (
    ExpectedProviderResult,
    classify_provider_observation,
    transport_error_result,
)

SECRET = "provider-signing-secret-for-unit-tests"
EXPECTED = ExpectedProviderResult(
    account_id="provider_demo",
    stable_key="capture:pay_123",
    operation_kind="CAPTURE",
    reference="cap_123",
    amount=1_000,
    currency="INR",
)


def _body(*, outcome: str = "SUCCEEDED", **overrides: object) -> bytes:
    values: dict[str, object] = {
        "accountId": EXPECTED.account_id,
        "stableKey": EXPECTED.stable_key,
        "operationKind": EXPECTED.operation_kind,
        "reference": EXPECTED.reference,
        "amount": EXPECTED.amount,
        "currency": EXPECTED.currency,
        "outcome": outcome,
        "declineCode": "DO_NOT_HONOR" if outcome == "DECLINED" else None,
        "effectId": "effect-123",
    }
    values.update(overrides)
    return canonical_json_bytes(values)


def _observation(
    *, body: bytes | None = None, status_code: int = 200, signed: bool = True
) -> ProviderObservation:
    resolved = body if body is not None else _body()
    headers: dict[str, str] = {}
    if signed:
        headers["X-Provider-Signature"] = hmac.new(
            SECRET.encode(), resolved, hashlib.sha256
        ).hexdigest()
    return ProviderObservation(status_code, resolved, headers)


@pytest.mark.parametrize(
    ("observation", "is_lookup", "action", "classification"),
    [
        (_observation(), False, "FINALIZE_SUCCESS", "VERIFIED_SUCCESS"),
        (
            _observation(body=_body(outcome="DECLINED")),
            False,
            "FINALIZE_FAILURE",
            "VERIFIED_BUSINESS_DECLINE",
        ),
        (_observation(status_code=503), False, "LOOKUP", "AMBIGUOUS_HTTP_5XX"),
        (_observation(status_code=409), False, "LOOKUP", "NON_BUSINESS_HTTP_4XX"),
        (
            _observation(body=_body(outcome="PENDING")),
            True,
            "PROCESSING",
            "VERIFIED_PENDING",
        ),
        (_observation(), True, "FINALIZE_SUCCESS", "VERIFIED_SUCCESS"),
        (
            _observation(body=_body(outcome="DECLINED")),
            True,
            "FINALIZE_FAILURE",
            "VERIFIED_BUSINESS_DECLINE",
        ),
    ],
    ids=[
        "valid-matching-success",
        "valid-matching-business-decline",
        "mutation-http-5xx",
        "mutation-non-business-http-4xx",
        "lookup-pending",
        "lookup-matching-success",
        "lookup-verified-business-failure",
    ],
)
def test_provider_outcome_matrix(
    observation: ProviderObservation,
    is_lookup: bool,
    action: str,
    classification: str,
) -> None:
    result = classify_provider_observation(
        observation,
        expected=EXPECTED,
        signing_secret=SECRET,
        is_lookup=is_lookup,
    )

    assert result.action == action
    assert result.classification == classification


@pytest.mark.parametrize(
    ("observation", "classification"),
    [
        (_observation(body=b'{"outcome":'), "INVALID_SCHEMA"),
        (_observation(signed=False), "INVALID_SIGNATURE"),
        (_observation(body=_body(amount=1_001)), "MISMATCHED_EVIDENCE"),
        (
            _observation(body=_body(outcome="SUCCEEDED", declineCode="CONTRADICTORY")),
            "INVALID_SCHEMA",
        ),
    ],
    ids=[
        "malformed",
        "unsigned",
        "mismatched",
        "contradictory",
    ],
)
def test_invalid_provider_evidence_requires_review(
    observation: ProviderObservation, classification: str
) -> None:
    result = classify_provider_observation(
        observation,
        expected=EXPECTED,
        signing_secret=SECRET,
        is_lookup=False,
    )

    assert result.action == "REVIEW"
    assert result.classification == classification
    assert result.safe_error_code == "INVALID_EVIDENCE"


def test_timeout_or_reset_requires_status_lookup() -> None:
    result = transport_error_result()

    assert result.action == "LOOKUP"
    assert result.classification == "AMBIGUOUS_TRANSPORT"
