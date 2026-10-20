"""ADR-011: only a signed, identity-bound provider "not found" is evidence of absence."""

import hashlib
import hmac

from relaypay.idempotency import canonical_json_bytes
from relaypay.provider_operations.service_types import ProviderObservation
from relaypay.provider_operations.validation import (
    ExpectedProviderResult,
    classify_provider_observation,
)

SECRET = "unit-test-provider-signing-secret"
EXPECTED = ExpectedProviderResult(
    account_id="acct_1",
    stable_key="authorize:pay_1",
    operation_kind="AUTHORIZE",
    reference="pay_1",
    amount=100_000,
    currency="INR",
)


def _signed(body: bytes, *, status: int = 404, secret: str = SECRET) -> ProviderObservation:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return ProviderObservation(status, body, {"X-Provider-Signature": digest})


def _not_found(**overrides: str) -> bytes:
    payload = {"accountId": "acct_1", "outcome": "NOT_FOUND", "stableKey": "authorize:pay_1"}
    payload.update(overrides)
    return canonical_json_bytes(payload)


def _classify(observation: ProviderObservation, *, is_lookup: bool = True) -> tuple[str, str]:
    result = classify_provider_observation(
        observation, expected=EXPECTED, signing_secret=SECRET, is_lookup=is_lookup
    )
    return result.action, result.classification


def test_signed_identity_bound_not_found_is_verified() -> None:
    assert _classify(_signed(_not_found())) == ("NOT_FOUND", "VERIFIED_NOT_FOUND")


def test_unsigned_not_found_is_never_evidence() -> None:
    assert _classify(ProviderObservation(404, _not_found(), {})) == (
        "PROCESSING",
        "NON_BUSINESS_HTTP_4XX",
    )
    assert _classify(ProviderObservation(404, b"", {})) == ("PROCESSING", "NON_BUSINESS_HTTP_4XX")


def test_not_found_signed_with_another_key_is_never_evidence() -> None:
    observation = _signed(_not_found(), secret="some-other-signing-secret")
    assert _classify(observation) == ("PROCESSING", "NON_BUSINESS_HTTP_4XX")


def test_signed_not_found_for_another_identity_goes_to_review() -> None:
    assert _classify(_signed(_not_found(stableKey="authorize:pay_2"))) == (
        "REVIEW",
        "MISMATCHED_EVIDENCE",
    )
    assert _classify(_signed(_not_found(accountId="acct_2"))) == ("REVIEW", "MISMATCHED_EVIDENCE")


def test_not_found_body_must_match_the_schema_exactly() -> None:
    body = canonical_json_bytes(
        {
            "accountId": "acct_1",
            "outcome": "NOT_FOUND",
            "stableKey": "authorize:pay_1",
            "extra": "field",
        }
    )
    assert _classify(_signed(body)) == ("PROCESSING", "NON_BUSINESS_HTTP_4XX")


def test_not_found_only_counts_on_lookups() -> None:
    # A mutation answered 404 is a non-business error; the stable key may still be applied later.
    assert _classify(_signed(_not_found()), is_lookup=False) == ("LOOKUP", "NON_BUSINESS_HTTP_4XX")
