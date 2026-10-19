"""Crash points: named places where the chaos harness can kill a process.

Production code calls ``maybe_crash("<point>")`` at the few places where a real crash would
leave the system half-way through a multi-step protocol (after a commit, before the next step).
The call is a no-op unless crash injection is configured, and injection can never be enabled
when ``APP_ENV`` is ``production``.

Two ways to configure it:

* **Environment (full chaos mode, real containers).** ``RELAYPAY_CRASH_POINTS`` is a comma list
  of ``point:probability`` pairs, ``RELAYPAY_CRASH_SEED`` seeds the draws, and
  ``RELAYPAY_CRASH_MODE`` is ``exit`` (``os._exit(137)``, what SIGKILL looks like to peers)
  or ``raise`` (the default: raise ``SimulatedCrash``). The environment is read once per process.
* **In-process (CI chaos mode).** ``crash_plan(points, rng)`` installs a plan for the *current
  thread only*, so parallel seeds never interfere. Plans always use ``raise`` behaviour.

``SimulatedCrash`` derives from ``BaseException`` so the ordinary ``except Exception`` handlers
in the services cannot swallow it, exactly like a process dying cannot be caught.
"""

from __future__ import annotations

import logging
import os
import random
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

POINTS_ENV = "RELAYPAY_CRASH_POINTS"
SEED_ENV = "RELAYPAY_CRASH_SEED"
MODE_ENV = "RELAYPAY_CRASH_MODE"
EXIT_STATUS = 137  # 128 + SIGKILL


class SimulatedCrash(BaseException):  # a crash, not an error: BaseException on purpose
    """Raised at a crash point to emulate the process dying right there."""

    def __init__(self, point: str) -> None:
        super().__init__(point)
        self.point = point


def _production() -> bool:
    return os.environ.get("APP_ENV", "development").strip().lower() == "production"


def parse_points(spec: str) -> dict[str, float]:
    """Parse ``"point:probability,point:probability"``; a bare ``point`` means probability 1."""
    points: dict[str, float] = {}
    for raw in spec.split(","):
        item = raw.strip()
        if not item:
            continue
        name, separator, probability_text = item.partition(":")
        name = name.strip()
        probability = float(probability_text) if separator else 1.0
        if not name or not 0.0 <= probability <= 1.0:
            raise ValueError(f"invalid crash point entry: {item!r}")
        points[name] = probability
    return points


@dataclass(slots=True)
class _Plan:
    points: dict[str, float]
    rng: random.Random
    mode: str
    lock: threading.Lock | None = None
    fired: list[str] = field(default_factory=list)

    def maybe(self, point: str) -> None:
        probability = self.points.get(point)
        if not probability:
            return
        if self.lock is None:
            draw = self.rng.random()
        else:
            with self.lock:
                draw = self.rng.random()
        if draw >= probability:
            return
        self.fired.append(point)
        if self.mode == "exit":
            os._exit(EXIT_STATUS)
        raise SimulatedCrash(point)


class _Local(threading.local):
    plan: _Plan | None = None


_local = _Local()
_env_loaded = False
_env_plan: _Plan | None = None
_env_lock = threading.Lock()


def _load_env_plan() -> None:
    global _env_loaded, _env_plan
    with _env_lock:
        if _env_loaded:
            return
        plan: _Plan | None = None
        spec = os.environ.get(POINTS_ENV, "").strip()
        if spec:
            if _production():
                logger.warning("%s is ignored because APP_ENV=production", POINTS_ENV)
            else:
                mode = os.environ.get(MODE_ENV, "raise").strip().lower()
                if mode not in {"raise", "exit"}:
                    raise ValueError(f"{MODE_ENV} must be 'raise' or 'exit', got {mode!r}")
                seed = int(os.environ.get(SEED_ENV, "0"))
                plan = _Plan(parse_points(spec), random.Random(seed), mode, threading.Lock())  # noqa: S311
        _env_plan = plan
        _env_loaded = True


def reset_config() -> None:
    """Forget the cached environment configuration (tests change the environment)."""
    global _env_loaded, _env_plan
    with _env_lock:
        _env_loaded = False
        _env_plan = None


def maybe_crash(point: str) -> None:
    """Crash here if the active configuration says so; otherwise do nothing."""
    plan = _local.plan
    if plan is None:
        if not _env_loaded:
            _load_env_plan()
        plan = _env_plan
        if plan is None:
            return
    plan.maybe(point)


class CrashLog:
    """Handle returned by ``crash_plan``: the points that fired inside the context."""

    def __init__(self, plan: _Plan) -> None:
        self._plan = plan

    @property
    def fired(self) -> tuple[str, ...]:
        return tuple(self._plan.fired)


@contextmanager
def crash_plan(points: Mapping[str, float], rng: random.Random) -> Iterator[CrashLog]:
    """Inject crashes for the current thread only: each hit of ``point`` raises
    ``SimulatedCrash`` with the given probability, drawn from ``rng``.

    Refuses to run when ``APP_ENV`` is ``production``. Plans nest; the previous one is restored.
    """
    if _production():
        raise RuntimeError("crash injection is disabled when APP_ENV=production")
    for name, probability in points.items():
        if not 0.0 <= probability <= 1.0:
            raise ValueError(f"probability for {name!r} must be within [0, 1]")
    plan = _Plan(dict(points), rng, "raise")
    previous = _local.plan
    _local.plan = plan
    try:
        yield CrashLog(plan)
    finally:
        _local.plan = previous
