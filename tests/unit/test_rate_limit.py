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
