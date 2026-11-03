from collections.abc import Callable
from types import SimpleNamespace
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


@pytest.fixture
def inbound_only(
    batches: tuple[list[float], list[tuple[str, float]]], monkeypatch: pytest.MonkeyPatch
) -> tuple[list[float], list[tuple[str, float]]]:
    monkeypatch.setattr(poller, "SCHEDULE_SECONDS", {"process_inbound_webhooks": 2.0, "short": 1.0})
    return batches


def test_inbound_batch_is_bounded_and_the_rest_continues_next_tick(
    inbound_only: tuple[list[float], list[tuple[str, float]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, calls = inbound_only
    pending = list(range(30))  # finite queue, longer than one batch
    handled: list[int] = []
    monkeypatch.setattr(
        poller,
        "claim_inbound_webhook",
        lambda _factory: SimpleNamespace(event_id=pending.pop(0)) if pending else None,
    )
    monkeypatch.setattr(
        poller,
        "process_inbound_claim",
        lambda _factory, claim, **_kwargs: handled.append(claim.event_id),
    )
    settings = MagicMock(spec=list(Settings.model_fields))
    due: dict[str, float] = {}
    factory = sessionmaker()

    first = poller.poll_once(settings, factory=factory, commerce_factory=factory, due=due)
    assert first["process_inbound_webhooks"] == poller.INBOUND_CLAIMS_PER_BATCH
    assert handled == list(range(poller.INBOUND_CLAIMS_PER_BATCH))
    assert first["short"] == 1  # a later due task still ran in the same tick
    assert calls == [("short", 0.0)]
    assert due["process_inbound_webhooks"] == 2.0  # existing cadence, not shortened

    clock[0] = 2.0
    second = poller.poll_once(settings, factory=factory, commerce_factory=factory, due=due)
    assert second["process_inbound_webhooks"] == 30 - poller.INBOUND_CLAIMS_PER_BATCH
    assert handled == list(range(30))
    assert pending == []


def test_arrivals_during_an_inbound_batch_cannot_hold_the_tick(
    inbound_only: tuple[list[float], list[tuple[str, float]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, calls = inbound_only
    pending = [0]
    arrivals = [0]
    monkeypatch.setattr(
        poller,
        "claim_inbound_webhook",
        lambda _factory: SimpleNamespace(event_id=pending.pop(0)) if pending else None,
    )

    def process(_factory: object, claim: SimpleNamespace, **_kwargs: object) -> None:
        # Every handled event is followed by a new arrival, up to a finite ceiling so an
        # unbounded drain terminates (and fails the assertion) instead of hanging.
        if arrivals[0] < 1000:
            arrivals[0] += 1
            pending.append(claim.event_id + 1)

    monkeypatch.setattr(poller, "process_inbound_claim", process)
    factory = sessionmaker()
    result = poller.poll_once(
        MagicMock(spec=list(Settings.model_fields)),
        factory=factory,
        commerce_factory=factory,
        due={},
    )
    assert result["process_inbound_webhooks"] == poller.INBOUND_CLAIMS_PER_BATCH
    assert ("short", 0.0) in calls
    assert len(pending) == 1  # the arrival stream waits for the next tick


def test_crash_inside_an_inbound_claim_is_not_swallowed(
    inbound_only: tuple[list[float], list[tuple[str, float]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        poller, "claim_inbound_webhook", lambda _factory: SimpleNamespace(event_id=1)
    )

    def crash(*_args: object, **_kwargs: object) -> None:
        raise SimulatedCrash("inbound_claim")

    monkeypatch.setattr(poller, "process_inbound_claim", crash)
    factory = sessionmaker()
    with pytest.raises(SimulatedCrash):
        poller.poll_once(
            MagicMock(spec=list(Settings.model_fields)),
            factory=factory,
            commerce_factory=factory,
            due={},
        )
