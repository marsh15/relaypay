import json
from datetime import UTC, datetime
from pathlib import Path

from relaypay.subscriptions.policy import PolicyDefinition, classify_provider_code, propose_actions


def test_fixed_thirty_case_recovery_dataset_has_perfect_policy_agreement() -> None:
    path = Path(__file__).parents[1] / "fixtures/subscription-recovery-v1.json"
    cases = json.loads(path.read_text())
    assert len(cases) == 30
    started_at = datetime(2026, 8, 11, tzinfo=UTC)
    for case in cases:
        classification = classify_provider_code(case["code"])
        actions = propose_actions(
            classification=classification,
            consent={"channels": case["channels"]},
            started_at=started_at,
            policy=PolicyDefinition(),
        )
        assert sum(item.action_type == "PAYMENT_RETRY" for item in actions) == case["retries"]
        assert sum(item.action_type == "MESSAGE" for item in actions) == case["messages"]
        assert (len(actions) == 0) == case["terminal"]
