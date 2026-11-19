import logging
import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError

logger = logging.getLogger(__name__)

_MAX_TRACKED_KEYS = 10_000


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
            if len(self._events) >= _MAX_TRACKED_KEYS:
                # Bound the process-local key map: drop fully expired keys so
                # a long-lived single-instance sandbox cannot grow it forever.
                expired = [
                    tracked
                    for tracked, moments in self._events.items()
                    if not moments or moments[-1] <= cutoff
                ]
                for tracked in expired[: len(expired) // 2]:
                    del self._events[tracked]
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


# GCRA (generic cell rate algorithm) in one statement. ``tat`` is the theoretical arrival time of
# the next request. A request is admitted when it is no more than ``tolerance`` ahead of that
# schedule, and admitting it pushes the schedule one ``interval`` later. A new key always inserts,
# so a key is never refused its first request. ``ON CONFLICT ... WHERE`` makes the check and the
# advance one atomic row operation: concurrent callers serialise on the row lock, and a refused
# request changes nothing (no RETURNING row).
_ADMIT = text(
    """
    INSERT INTO api_rate_limit_state AS s (key, tat)
    VALUES (:key, clock_timestamp() + make_interval(secs => :interval))
    ON CONFLICT (key) DO UPDATE
       SET tat = GREATEST(s.tat, clock_timestamp()) + make_interval(secs => :interval)
     WHERE s.tat <= clock_timestamp() + make_interval(secs => :tolerance)
    RETURNING 1
    """
)
_WAIT = text(
    """
    SELECT EXTRACT(EPOCH FROM (tat - clock_timestamp() - make_interval(secs => :tolerance)))
      FROM api_rate_limit_state WHERE key = :key
    """
)
_PRUNE = text(
    "DELETE FROM api_rate_limit_state WHERE tat < clock_timestamp() - make_interval(secs => :age)"
)


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    retry_after_seconds: int = 0
    failed_open: bool = False


class GcraLimiter:
    """Per-key GCRA limiter on an UNLOGGED Postgres table.

    ``burst`` is the number of requests a fully rested key may make at once; after that the key
    is held to ``rps``. The engine should be an AUTOCOMMIT one so each admission is a single
    statement and a single round trip. If Postgres errors, the limiter fails open (the request is
    served, ``failed_open`` is set): an outage of this best-effort control must not become an
    outage of the payment API.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def admit(self, key: str, *, rps: int, burst: int) -> RateDecision:
        interval = 1.0 / rps
        tolerance = (burst - 1) * interval
        params = {"key": key, "interval": interval, "tolerance": tolerance}
        try:
            with self._engine.connect() as connection:
                if connection.execute(_ADMIT, params).first() is not None:
                    return RateDecision(True)
                wait = connection.execute(_WAIT, params).scalar()
        except SQLAlchemyError:
            logger.warning("rate limiter unavailable; failing open", exc_info=True)
            return RateDecision(True, failed_open=True)
        # tat may have moved between the two statements; a stale answer only shifts Retry-After.
        return RateDecision(False, max(1, math.ceil(float(wait or 0))))


def prune_rate_limit_state(
    factory: sessionmaker[Session], *, older_than_seconds: int = 3600
) -> int:
    """Drop limiter rows whose schedule lapsed long ago; they would be re-created identically."""
    with factory() as session, session.begin():
        return int(session.execute(_PRUNE, {"age": older_than_seconds}).rowcount or 0)  # type: ignore[attr-defined]
