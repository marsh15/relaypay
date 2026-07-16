import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from relaypay.errors import RelayPayError


class FixedWindowRateLimiter:
    """Small process-local limiter for the single-instance portfolio sandbox."""

    def __init__(
        self, *, limit: int, window_seconds: int, clock: Callable[[], float] = time.monotonic
    ):
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                retry_after = max(1, int(self._window - (now - events[0])))
                raise RelayPayError(
                    code="RATE_LIMITED",
                    message="Too many requests; retry later",
                    http_status=429,
                    retry_after=retry_after,
                )
            events.append(now)
