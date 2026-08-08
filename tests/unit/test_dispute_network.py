from fastapi.testclient import TestClient
from relaypay.disputes.network import DeterministicDisputeNetwork

from apps.dispute_network.main import create_app


def test_synthetic_network_replays_one_effect_for_stable_key() -> None:
    network = DeterministicDisputeNetwork()
    client = TestClient(create_app(network))
    headers = {"Content-Type": "application/zip", "Idempotency-Key": "dispute:stable"}
    first = client.post("/v1/submissions", content=b"synthetic-zip", headers=headers)
    replay = client.post("/v1/submissions", content=b"synthetic-zip", headers=headers)
    lookup = client.get("/v1/submissions/dispute:stable")
    assert first.status_code == replay.status_code == lookup.status_code == 200
    assert first.json()["status"] == lookup.json()["status"] == "SUCCEEDED"
    assert replay.json()["effectCount"] == 1
    assert network.effect_count == 1
