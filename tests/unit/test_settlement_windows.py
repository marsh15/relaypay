from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from relaypay.errors import RelayPayError
from relaypay.settlement_intelligence.windows import (
    PolicyWindow,
    arrival_date,
    business_date_of,
    cutoff_at,
    format_inr,
    local_day_bounds,
    next_business_date,
    validate_window,
)


def _window(**overrides: object) -> PolicyWindow:
    values: dict[str, object] = {
        "timezone_name": "Asia/Kolkata",
        "cutoff_hour": 17,
        "cutoff_minute": 0,
        "settlement_delay_days": 1,
        "weekend_handling": "SKIP",
    }
    values.update(overrides)
    return PolicyWindow(**values)  # type: ignore[arg-type]


def test_default_policy_is_kolkata_cutoff_t_plus_one_skip() -> None:
    window = PolicyWindow.default()
    assert window.timezone_name == "Asia/Kolkata"
    assert (window.cutoff_hour, window.cutoff_minute) == (17, 0)
    assert window.settlement_delay_days == 1
    assert window.weekend_handling == "SKIP"


def test_cutoff_boundary_is_inclusive_for_business_date() -> None:
    window = _window()
    zone = ZoneInfo("Asia/Kolkata")
    before = datetime(2026, 9, 25, 16, 59, 59, tzinfo=zone)
    at_cutoff = datetime(2026, 9, 25, 17, 0, 0, tzinfo=zone)
    after = datetime(2026, 9, 25, 17, 0, 1, tzinfo=zone)
    assert business_date_of(window, before) == date(2026, 9, 25)
    assert business_date_of(window, at_cutoff) == date(2026, 9, 25)
    assert business_date_of(window, after) == date(2026, 9, 26)


def test_cutoff_boundary_respects_timezone_not_utc() -> None:
    window = _window()
    utc_moment = datetime(2026, 9, 25, 11, 30, tzinfo=UTC)  # 17:00 Kolkata
    assert business_date_of(window, utc_moment) == date(2026, 9, 25)
    utc_later = datetime(2026, 9, 25, 11, 30, 1, tzinfo=UTC)
    assert business_date_of(window, utc_later) == date(2026, 9, 26)


def test_cutoff_at_converts_to_the_policy_timezone() -> None:
    window = _window(timezone_name="America/New_York", cutoff_hour=12, cutoff_minute=30)
    cutoff = cutoff_at(window, date(2026, 9, 25))
    assert cutoff.utcoffset() is not None
    assert cutoff.astimezone(ZoneInfo("America/New_York")).hour == 12
    assert cutoff.astimezone(ZoneInfo("America/New_York")).minute == 30


@pytest.mark.parametrize(
    ("delay", "weekend", "business", "expected"),
    [
        (0, "SKIP", date(2026, 9, 24), date(2026, 9, 24)),  # Thursday T+0
        (1, "SKIP", date(2026, 9, 24), date(2026, 9, 25)),  # Thursday T+1
        (2, "SKIP", date(2026, 9, 24), date(2026, 9, 28)),  # Thursday T+2 skips weekend
        (1, "SKIP", date(2026, 9, 25), date(2026, 9, 28)),  # Friday T+1 skips weekend
        (2, "SKIP", date(2026, 9, 25), date(2026, 9, 29)),  # Friday T+2 skips weekend
        (1, "INCLUDE", date(2026, 9, 25), date(2026, 9, 26)),  # Friday T+1 includes Saturday
        (2, "INCLUDE", date(2026, 9, 25), date(2026, 9, 27)),  # Friday T+2 includes Sunday
        (0, "SKIP", date(2026, 9, 26), date(2026, 9, 28)),  # T+0 weekend rolls to Monday
        (2, "SKIP", date(2026, 9, 28), date(2026, 9, 30)),  # Monday T+2 midweek
    ],
)
def test_arrival_date_matrix(delay: int, weekend: str, business: date, expected: date) -> None:
    window = _window(settlement_delay_days=delay, weekend_handling=weekend)
    assert arrival_date(window, business) == expected


def test_next_business_date_rolls_over_weekends_only_when_skipped() -> None:
    skip = _window(weekend_handling="SKIP")
    include = _window(weekend_handling="INCLUDE")
    friday = date(2026, 9, 25)
    assert next_business_date(skip, friday) == date(2026, 9, 28)
    assert next_business_date(include, friday) == date(2026, 9, 26)


def test_local_day_bounds_cover_the_whole_local_day() -> None:
    window = _window()
    start, end = local_day_bounds(window, date(2026, 9, 25))
    assert start.astimezone(ZoneInfo("Asia/Kolkata")).hour == 0
    assert (end - start).total_seconds() == 86_400


@pytest.mark.parametrize(
    "overrides",
    [
        {"timezone_name": "Mars/Olympus_Mons"},
        {"cutoff_hour": 24},
        {"cutoff_minute": 60},
        {"settlement_delay_days": 3},
        {"settlement_delay_days": -1},
        {"weekend_handling": "SOMETIMES"},
    ],
)
def test_invalid_policy_windows_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(RelayPayError) as error:
        validate_window(_window(**overrides))
    assert error.value.code == "SETTLEMENT_POLICY_INVALID"


def test_format_inr_is_deterministic() -> None:
    assert format_inr(123_456) == "INR 1234.56"
    assert format_inr(0) == "INR 0.00"
    assert format_inr(-500) == "INR -5.00"
