"""M17: provider chaos decisions are a pure, seeded function of (seed, stable key, aspect)."""

from relaypay.mock_provider.chaos import BPS, draw, effect_chaos, webhook_chaos
from relaypay.mock_provider.models import ProviderChaosProfile


def _profile(**bps: int) -> ProviderChaosProfile:
    defaults = {
        "seed": 7,
        "error_before_bps": 0,
        "error_after_bps": 0,
        "lose_response_bps": 0,
        "slow_bps": 0,
        "slow_ms": 0,
        "pending_bps": 0,
        "decline_bps": 0,
        "webhook_drop_bps": 0,
        "webhook_duplicate_bps": 0,
        "webhook_max_delay_ms": 0,
    }
    return ProviderChaosProfile(**(defaults | bps))


def test_draw_is_deterministic_and_separates_seeds_keys_and_aspects() -> None:
    assert draw(7, "authorize:pay_1", "decline") == draw(7, "authorize:pay_1", "decline")
    variants = {
        draw(7, "authorize:pay_1", "decline"),
        draw(8, "authorize:pay_1", "decline"),
        draw(7, "authorize:pay_2", "decline"),
        draw(7, "authorize:pay_1", "pending"),
    }
    assert len(variants) == 4
    assert all(0 <= value < BPS for value in variants)


def test_probabilities_are_respected_at_the_edges_and_on_average() -> None:
    keys = [f"capture:pay_{index}" for index in range(4_000)]
    assert all(effect_chaos(_profile(), key).outcome == "SUCCEEDED" for key in keys)
    assert all(effect_chaos(_profile(decline_bps=BPS), key).outcome == "DECLINED" for key in keys)
    lost = sum(effect_chaos(_profile(lose_response_bps=2_500), key).lose_response for key in keys)
    assert abs(lost / len(keys) - 0.25) < 0.03


def test_pending_takes_precedence_over_decline() -> None:
    chaos = effect_chaos(_profile(pending_bps=BPS, decline_bps=BPS), "authorize:pay_1")
    assert chaos.outcome == "PENDING"


def test_webhook_chaos_without_a_profile_delivers_once_immediately() -> None:
    chaos = webhook_chaos(None, "authorize:pay_1")
    assert (chaos.dropped, chaos.copies, chaos.delay_ms) == (False, 1, 0)


def test_webhook_delay_is_bounded_by_the_profile() -> None:
    profile = _profile(webhook_max_delay_ms=2_000, webhook_duplicate_bps=BPS)
    delays = [webhook_chaos(profile, f"refund:ref_{index}").delay_ms for index in range(500)]
    assert all(0 <= delay < 2_000 for delay in delays)
    assert len(set(delays)) > 100  # spread out, so deliveries arrive out of order
    assert webhook_chaos(profile, "refund:ref_1").copies == 2
