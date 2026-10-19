"""Crash points: inert unless configured, never in production, deterministic, uncatchable."""

import random
import threading
from collections.abc import Iterator

import pytest
from relaypay.proof import crashpoints
from relaypay.proof.crashpoints import (
    SimulatedCrash,
    crash_plan,
    maybe_crash,
    parse_points,
    reset_config,
)


def make_rng(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311 - a simulation, not a secret


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in ("RELAYPAY_CRASH_POINTS", "RELAYPAY_CRASH_SEED", "RELAYPAY_CRASH_MODE", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    reset_config()
    yield
    reset_config()


def _hits(point: str, attempts: int) -> list[int]:
    fired = []
    for attempt in range(attempts):
        try:
            maybe_crash(point)
        except SimulatedCrash:
            fired.append(attempt)
    return fired


def test_disabled_is_a_no_op() -> None:
    assert _hits("provider.after_prepare_send", 1000) == []


def test_unconfigured_point_never_fires_even_when_others_are() -> None:
    with crash_plan({"finalizer.after_commit": 1.0}, make_rng(1)):
        assert _hits("provider.after_prepare_send", 100) == []


def test_probability_one_always_fires_and_zero_never() -> None:
    with crash_plan({"a": 1.0, "b": 0.0}, make_rng(1)):
        assert len(_hits("a", 10)) == 10
        assert _hits("b", 10) == []


def test_probabilities_are_deterministic_per_seed() -> None:
    def run(seed: int) -> list[int]:
        with crash_plan({"p": 0.3}, make_rng(seed)):
            return _hits("p", 200)

    assert run(7) == run(7)
    assert run(7) != run(8)
    assert 30 < len(run(7)) < 90  # roughly 30% of 200


def test_simulated_crash_is_a_base_exception_that_ordinary_handlers_miss() -> None:
    assert issubclass(SimulatedCrash, BaseException)
    assert not issubclass(SimulatedCrash, Exception)
    swallowed = False
    with crash_plan({"p": 1.0}, make_rng(0)), pytest.raises(SimulatedCrash) as raised:
        try:
            maybe_crash("p")
        except Exception:
            swallowed = True
    assert not swallowed
    assert raised.value.point == "p"


def test_plan_reports_the_points_that_fired() -> None:
    with crash_plan({"p": 1.0}, make_rng(0)) as log:
        _hits("p", 3)
    assert log.fired == ("p", "p", "p")


def test_plans_are_thread_local() -> None:
    other_thread_hits: list[int] = []

    def other() -> None:
        other_thread_hits.extend(_hits("p", 20))

    with crash_plan({"p": 1.0}, make_rng(0)):
        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        assert len(_hits("p", 5)) == 5
    assert other_thread_hits == []
    assert _hits("p", 5) == []  # plan removed on exit


def test_plans_nest_and_restore() -> None:
    with crash_plan({"outer": 1.0}, make_rng(0)):
        with crash_plan({"inner": 1.0}, make_rng(0)):
            assert _hits("outer", 3) == []
            assert len(_hits("inner", 3)) == 3
        assert len(_hits("outer", 3)) == 3


def test_production_refuses_plans_and_ignores_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="production"), crash_plan({"p": 1.0}, make_rng(0)):
        pytest.fail("a plan must not start in production")
    monkeypatch.setenv("RELAYPAY_CRASH_POINTS", "p:1.0")
    monkeypatch.setenv("RELAYPAY_CRASH_MODE", "raise")
    reset_config()
    assert _hits("p", 20) == []


def test_environment_configuration_raises_in_raise_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYPAY_CRASH_POINTS", "p:1.0,q:0")
    monkeypatch.setenv("RELAYPAY_CRASH_MODE", "raise")
    monkeypatch.setenv("RELAYPAY_CRASH_SEED", "5")
    reset_config()
    assert len(_hits("p", 4)) == 4
    assert _hits("q", 4) == []


def test_environment_exit_mode_exits_137(monkeypatch: pytest.MonkeyPatch) -> None:
    exited: list[int] = []

    def fake_exit(status: int) -> None:
        exited.append(status)
        raise SimulatedCrash("exit-intercepted")

    monkeypatch.setattr(crashpoints.os, "_exit", fake_exit)
    monkeypatch.setenv("RELAYPAY_CRASH_POINTS", "p:1.0")
    monkeypatch.setenv("RELAYPAY_CRASH_MODE", "exit")
    reset_config()
    with pytest.raises(SimulatedCrash):
        maybe_crash("p")
    assert exited == [137]


def test_environment_draws_are_deterministic_per_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(seed: str) -> list[int]:
        monkeypatch.setenv("RELAYPAY_CRASH_POINTS", "p:0.25")
        monkeypatch.setenv("RELAYPAY_CRASH_MODE", "raise")
        monkeypatch.setenv("RELAYPAY_CRASH_SEED", seed)
        reset_config()
        return _hits("p", 200)

    assert run("1") == run("1")
    assert run("1") != run("2")


def test_parse_points_accepts_bare_names_and_rejects_bad_probabilities() -> None:
    assert parse_points("a:0.5, b ,,c:1") == {"a": 0.5, "b": 1.0, "c": 1.0}
    for bad in ("a:1.5", "a:-0.1", ":0.5", "a:x"):
        with pytest.raises(ValueError, match=r".+"):
            parse_points(bad)
