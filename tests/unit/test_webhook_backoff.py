"""Outbound webhook retry backoff: capped exponential ceiling with full jitter."""

import uuid
from datetime import timedelta

import pytest
from relaypay.event_delivery.delivery import (
    MAX_ATTEMPTS,
    RETRY_BASE_SECONDS,
    RETRY_CAP_SECONDS,
    next_backoff,
)


@pytest.mark.parametrize("sequence", range(1, MAX_ATTEMPTS))
def test_delay_is_within_the_doubling_ceiling(sequence: int) -> None:
    ceiling = timedelta(seconds=min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2 ** (sequence - 1)))
    for _ in range(200):
        delay = next_backoff(sequence, uuid.uuid4())
        assert timedelta(0) <= delay <= ceiling


def test_same_delivery_and_attempt_always_get_the_same_delay() -> None:
    delivery = uuid.uuid4()
    assert next_backoff(3, delivery) == next_backoff(3, delivery)


def test_deliveries_failing_together_do_not_retry_in_lockstep() -> None:
    delays = {next_backoff(4, uuid.uuid4()) for _ in range(100)}
    assert len(delays) > 90


def test_ceiling_is_capped() -> None:
    assert next_backoff(40, uuid.uuid4()) <= timedelta(seconds=RETRY_CAP_SECONDS)


def test_schedule_rides_out_minutes_of_receiver_failure() -> None:
    worst_case = sum(
        min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2 ** (n - 1)) for n in range(1, MAX_ATTEMPTS)
    )
    assert worst_case >= 600  # versus about 15 s with v1's 1, 2, 4, 8 s schedule


def test_backoff_requires_a_failed_attempt() -> None:
    with pytest.raises(ValueError):
        next_backoff(0, uuid.uuid4())
