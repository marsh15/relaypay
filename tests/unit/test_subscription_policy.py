from datetime import UTC, datetime

import pytest
from relaypay.subscriptions.policy import (
    PolicyDefinition,
    classify_provider_code,
    propose_actions,
)


def test_soft_decline_is_bounded_by_policy_and_consent() -> None:
    started = datetime(2026, 8, 11, 9, 0, tzinfo=UTC)
    actions = propose_actions(
        classification=classify_provider_code("INSUFFICIENT_FUNDS"),
        consent={"channels": ["EMAIL"]},
        started_at=started,
        policy=PolicyDefinition(),
    )
    assert sum(item.action_type == "PAYMENT_RETRY" for item in actions) == 3
    assert sum(item.action_type == "MESSAGE" for item in actions) == 3
    assert all(item.channel in {None, "EMAIL"} for item in actions)
    assert max(item.scheduled_for for item in actions) <= datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("code", ["DO_NOT_HONOUR", "LOST_OR_STOLEN", "TRANSPORT_UNKNOWN"])
def test_hard_or_unknown_failure_never_proposes_an_action(code: str) -> None:
    assert (
        propose_actions(
            classification=classify_provider_code(code),
            consent={"channels": ["EMAIL", "WHATSAPP"]},
            started_at=datetime(2026, 8, 11, tzinfo=UTC),
            policy=PolicyDefinition(),
        )
        == ()
    )


def test_unconsented_channel_and_out_of_bounds_policy_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported consent channel"):
        propose_actions(
            classification="AUTHENTICATION_REQUIRED",
            consent={"channels": ["SMS"]},
            started_at=datetime(2026, 8, 11, tzinfo=UTC),
            policy=PolicyDefinition(),
        )
    with pytest.raises(ValueError, match="between zero and three"):
        PolicyDefinition(max_payment_retries=4)
