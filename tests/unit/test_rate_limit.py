import pytest
from relaypay.errors import RelayPayError
from relaypay.identity.rate_limit import FixedWindowRateLimiter


def test_rate_limit_includes_retry_after() -> None:
    now = [100.0]
    limiter = FixedWindowRateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])
    limiter.check("client")
    limiter.check("client")
    with pytest.raises(RelayPayError) as caught:
        limiter.check("client")
    assert caught.value.http_status == 429
    assert caught.value.retry_after == 60

    now[0] += 61
    limiter.check("client")


def test_limiter_drops_expired_keys_when_the_map_grows_unbounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import relaypay.identity.rate_limit as module
    from relaypay.identity.rate_limit import FixedWindowRateLimiter

    monkeypatch.setattr(module, "_MAX_TRACKED_KEYS", 4)
    clock = {"now": 0.0}
    limiter = FixedWindowRateLimiter(limit=10, window_seconds=60, clock=lambda: clock["now"])
    for index in range(4):
        limiter.check(f"old-{index}")
    clock["now"] = 120.0  # everything from the first window is expired
    limiter.check("fresh")
    assert "old-0" not in limiter._events or len(limiter._events) <= module._MAX_TRACKED_KEYS
    # A long-stale key cannot still enforce a limit against a new client.
    limiter.check("old-0")
