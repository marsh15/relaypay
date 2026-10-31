"""Regression guard for the M13 scenario clock. It needs no database.

The integration scenario in ``tests/integration/test_m13_settlement_intelligence.py`` records a
refund and asks its refund cash-impact question one minute later. The production query counts
refunds on the question's Asia/Kolkata calendar day, so both moments must share that day. CI
failed when the scenario began at 2026-10-22T18:29:30Z (23:59:30 IST), because the question
crossed local midnight.
"""

from datetime import UTC, datetime

from relaypay.settlement_intelligence.windows import PolicyWindow, business_date_of, cutoff_at

from tests.support.m13_clock import SCENARIO_ANCHOR, local_calendar_day, scenario_moments

# The wall-clock reading at which the M13 scenario failed in CI (23:59:30 IST).
CI_FAILING_CLOCK = datetime(2026, 10, 22, 18, 29, 30, tzinfo=UTC)


def test_every_scenario_moment_falls_on_one_local_day() -> None:
    moments = scenario_moments(SCENARIO_ANCHOR)
    local_days = {
        local_calendar_day(moment)
        for moment in (
            SCENARIO_ANCHOR,
            moments.early_capture,
            moments.late_capture,
            moments.refund,
            moments.question,
        )
    }

    assert local_days == {local_calendar_day(SCENARIO_ANCHOR)}


def test_scenario_forecast_and_question_precede_the_default_cutoff() -> None:
    window = PolicyWindow.default()
    day = local_calendar_day(SCENARIO_ANCHOR)

    assert cutoff_at(window, day) > SCENARIO_ANCHOR
    assert business_date_of(window, SCENARIO_ANCHOR) == day
    assert business_date_of(window, scenario_moments(SCENARIO_ANCHOR).question) == day


def test_wall_clock_near_local_midnight_splits_refund_and_question_days() -> None:
    moments = scenario_moments(CI_FAILING_CLOCK)

    assert local_calendar_day(moments.refund) != local_calendar_day(moments.question)
