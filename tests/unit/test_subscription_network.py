from fastapi.testclient import TestClient
from relaypay.subscriptions.network import (
    DeterministicCommunicationNetwork,
    DeterministicRecurringPaymentNetwork,
)

from apps.communication_network.main import create_app


def test_message_replay_and_lookup_have_one_external_effect() -> None:
    messages = DeterministicCommunicationNetwork()
    client = TestClient(create_app(messages=messages))
    headers = {"Idempotency-Key": "recovery:case:message:1"}
    payload = {"channel": "EMAIL", "body": "Synthetic recovery message"}
    first = client.post("/v1/messages", headers=headers, json=payload)
    replay = client.post("/v1/messages", headers=headers, json=payload)
    lookup = client.get("/v1/messages/recovery:case:message:1")
    assert first.status_code == replay.status_code == lookup.status_code == 200
    assert lookup.json()["status"] == "SUCCEEDED"
    assert replay.json()["effectCount"] == messages.effect_count == 1


def test_payment_retry_replay_has_one_external_effect() -> None:
    payments = DeterministicRecurringPaymentNetwork(outcome="FAILED")
    client = TestClient(create_app(payments=payments))
    headers = {"Idempotency-Key": "recovery:case:payment:1"}
    payload = {"invoiceId": "inv_" + "a" * 32, "amount": 12_500}
    first = client.post("/v1/payment-retries", headers=headers, json=payload)
    replay = client.post("/v1/payment-retries", headers=headers, json=payload)
    assert first.status_code == replay.status_code == 200
    assert first.json()["status"] == "FAILED"
    assert replay.json()["effectCount"] == payments.effect_count == 1


def test_strict_mock_rejects_unknown_channels_and_fields() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/v1/messages",
        headers={"Idempotency-Key": "strict"},
        json={"channel": "SMS", "body": "No", "customerEmail": "not-allowed@example.test"},
    )
    assert response.status_code == 422
