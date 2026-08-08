import hashlib
import json
from pathlib import Path

from relaypay.disputes.evidence import REQUIRED_EVIDENCE, draft_from_allowlisted_evidence
from relaypay.disputes.models import DisputeCase
from relaypay.idempotency import canonical_json_bytes
from relaypay.ids import new_public_id, new_uuid


def test_fixed_dispute_set_has_precise_retrieval_and_schema_conformance() -> None:
    fixtures = json.loads((Path(__file__).parents[1] / "fixtures" / "disputes-v1.json").read_text())
    assert len(fixtures) == 30
    correct = 0
    selected = 0
    for fixture in fixtures:
        reason = fixture["reason"]
        snapshot = {name: f"synthetic-{name}" for name in fixture["present"]}
        snapshot_bytes = canonical_json_bytes(snapshot)
        case = DisputeCase(
            id=new_uuid(),
            public_id=new_public_id("dpc"),
            organisation_id=new_uuid(),
            environment_id=new_uuid(),
            payment_intent_id=new_uuid(),
            network_dispute_id=f"network-{new_uuid().hex}",
            reason_code=reason,
            amount=100,
            currency="INR",
            due_at="2026-08-15T00:00:00Z",
            source_snapshot=snapshot,
            source_sha256=hashlib.sha256(snapshot_bytes).digest(),
            status="OPEN",
        )
        draft = draft_from_allowlisted_evidence(case)
        expected_selected = set(REQUIRED_EVIDENCE[reason]) & set(snapshot)
        expected_missing = set(REQUIRED_EVIDENCE[reason]) - set(snapshot)
        actual_selected = set(draft.citations[0].field_paths)
        correct += len(actual_selected & expected_selected)
        selected += len(actual_selected)
        assert draft.classification == reason
        assert set(draft.missing_evidence) == expected_missing
        assert draft.citations[0].snapshot_sha256 == case.source_sha256.hex()
        assert draft.model_dump(mode="json", by_alias=True)
    assert correct / max(selected, 1) >= 0.90
