import pytest
from pydantic import ValidationError
from relaypay.errors import RelayPayError
from relaypay.subscriptions.intake import (
    RecurringPaymentFailedEnvelope,
    assert_pii_free_evidence,
)


def test_recurring_failure_envelope_is_strict_and_pii_free() -> None:
    with pytest.raises(ValidationError):
        RecurringPaymentFailedEnvelope.model_validate(
            {
                "eventId": "bev_" + "a" * 32,
                "eventType": "recurring-payment.failed.v1",
                "schemaVersion": 1,
                "occurredAt": "2026-08-11T00:00:00Z",
                "organisationId": "org_" + "b" * 32,
                "environmentId": "env_" + "c" * 32,
                "resourceType": "subscription_invoice",
                "resourceId": "inv_" + "d" * 32,
                "payload": {
                    "subscriptionId": "sub_" + "e" * 32,
                    "invoiceId": "inv_" + "d" * 32,
                    "providerAttemptId": "provider-1",
                    "providerCode": "INSUFFICIENT_FUNDS",
                    "outcome": "VERIFIED_FAILED",
                    "evidence": {},
                    "customerEmail": "forbidden@example.test",
                },
                "payloadSha256": "f" * 64,
            }
        )
    assert_pii_free_evidence({"declineCode": "INSUFFICIENT_FUNDS", "attempt": 1})
    with pytest.raises(RelayPayError) as error:
        assert_pii_free_evidence({"providerNote": "contact person@example.test"})
    assert error.value.code == "EVENT_CONTAINS_PII"
    assert "tokenized" in error.value.message
    with pytest.raises(RelayPayError) as forbidden:
        assert_pii_free_evidence({"accountReference": "token_1"})
    assert "forbidden field" in forbidden.value.message
