from collections.abc import Callable
from unittest.mock import MagicMock

import pytest
from relaypay.config import Settings
from relaypay.proof.crashpoints import SimulatedCrash
from sqlalchemy.orm import sessionmaker

from apps.worker import poller


@pytest.fixture
def batches(monkeypatch: pytest.MonkeyPatch) -> tuple[list[float], list[tuple[str, float]]]:
    clock = [0.0]
    calls: list[tuple[str, float]] = []

    def runner(name: str) -> Callable[..., int]:
        def run(*_: object) -> int:
            calls.append((name, clock[0]))
            return 1

        return run

    monkeypatch.setattr(poller, "SCHEDULE_SECONDS", {"short": 1.0, "long": 3.0})
    monkeypatch.setattr(poller, "_RUNNERS", {name: runner(name) for name in ("short", "long")})
    monkeypatch.setattr(poller.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(poller, "RedpandaPublisher", lambda _: MagicMock())
    monkeypatch.setattr(poller, "publish_one", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(poller, "maybe_crash", lambda _: None)
    return clock, calls


def test_long_interval_tasks_run_again_without_postponement(
    batches: tuple[list[float], list[tuple[str, float]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, calls = batches
    settings = MagicMock(spec=list(Settings.model_fields))
    factory = sessionmaker()
    monkeypatch.setattr(poller, "get_settings", lambda: settings)
    monkeypatch.setattr(poller, "configure_tracing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(poller, "start_worker_metrics_server", lambda _: None)
    monkeypatch.setattr(poller, "build_engine", lambda *_args, **_kwargs: MagicMock())
    monkeypatch.setattr(poller, "build_session_factory", lambda _: factory)

    def sleep(_: float) -> None:
        clock[0] += 1.0
        if clock[0] == 5:
            raise KeyboardInterrupt

    monkeypatch.setattr(poller.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        poller.main()
    assert calls == [
        ("short", 0.0),
        ("long", 0.0),
        ("short", 1.0),
        ("short", 2.0),
        ("short", 3.0),
        ("long", 3.0),
        ("short", 4.0),
    ]


def test_failed_batch_does_not_starve_later_tasks_or_outbox(
    batches: tuple[list[float], list[tuple[str, float]]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, calls = batches

    def fail(*_: object) -> int:
        raise RuntimeError("synthetic batch failure")

    monkeypatch.setitem(poller._RUNNERS, "short", fail)
    due: dict[str, float] = {}
    factory = sessionmaker()
    result = poller.poll_once(
        MagicMock(spec=list(Settings.model_fields)),
        factory=factory,
        commerce_factory=factory,
        due=due,
    )
    assert calls == [("long", 0.0)]
    assert result == {"long": 1, "published": 0}
    assert due == {"short": 1.0, "long": 3.0}
    assert "postgres_poller_task_failed" in caplog.text


def test_outbox_outage_preserves_task_deadlines(
    batches: tuple[list[float], list[tuple[str, float]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, calls = batches

    def unavailable(_: str) -> None:
        raise RuntimeError("synthetic broker outage")

    monkeypatch.setattr(poller, "RedpandaPublisher", unavailable)
    due: dict[str, float] = {}
    factory = sessionmaker()
    for instant in (0.0, 1.0, 2.0, 3.0):
        clock[0] = instant
        with pytest.raises(RuntimeError, match="synthetic broker outage"):
            poller.poll_once(
                MagicMock(spec=list(Settings.model_fields)),
                factory=factory,
                commerce_factory=factory,
                due=due,
            )
    assert [instant for name, instant in calls if name == "long"] == [0.0, 3.0]


def test_injected_crash_is_never_swallowed(
    batches: tuple[list[float], list[tuple[str, float]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, calls = batches

    def crash(point: str) -> None:
        raise SimulatedCrash(point)

    monkeypatch.setattr(poller, "maybe_crash", crash)
    factory = sessionmaker()
    with pytest.raises(SimulatedCrash):
        poller.poll_once(
            MagicMock(spec=list(Settings.model_fields)), factory=factory, commerce_factory=factory
        )
    assert calls == [("short", 0.0)]
