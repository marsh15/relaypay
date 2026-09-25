"""Deterministic settlement-window mathematics for immutable merchant policies.

Every date and timestamp produced here is a pure function of the immutable
policy fields and the input moment, so forecasts, arrival expectations, and
question answers remain reproducible across processes and releases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, available_timezones

from relaypay.errors import RelayPayError

DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_CUTOFF_HOUR = 17
DEFAULT_CUTOFF_MINUTE = 0
DEFAULT_SETTLEMENT_DELAY_DAYS = 1
DEFAULT_WEEKEND_HANDLING = "SKIP"
WEEKEND_HANDLING_VALUES = ("INCLUDE", "SKIP")
MAX_SETTLEMENT_DELAY_DAYS = 2

_AVAILABLE_TIMEZONES = frozenset(available_timezones())


@dataclass(frozen=True, slots=True)
class PolicyWindow:
    """The immutable settlement timing rule for one merchant account."""

    timezone_name: str
    cutoff_hour: int
    cutoff_minute: int
    settlement_delay_days: int
    weekend_handling: str

    @classmethod
    def default(cls) -> PolicyWindow:
        return cls(
            timezone_name=DEFAULT_TIMEZONE,
            cutoff_hour=DEFAULT_CUTOFF_HOUR,
            cutoff_minute=DEFAULT_CUTOFF_MINUTE,
            settlement_delay_days=DEFAULT_SETTLEMENT_DELAY_DAYS,
            weekend_handling=DEFAULT_WEEKEND_HANDLING,
        )

    def policy_value(self) -> dict[str, object]:
        return {
            "timezone": self.timezone_name,
            "cutoffHour": self.cutoff_hour,
            "cutoffMinute": self.cutoff_minute,
            "settlementDelayDays": self.settlement_delay_days,
            "weekendHandling": self.weekend_handling,
        }


def validate_window(window: PolicyWindow) -> None:
    if window.timezone_name not in _AVAILABLE_TIMEZONES:
        raise RelayPayError(
            code="SETTLEMENT_POLICY_INVALID",
            message="Settlement policy timezone must be a valid IANA zone name",
            http_status=422,
            details={"timezone": window.timezone_name},
        )
    if not 0 <= window.cutoff_hour <= 23:
        raise RelayPayError(
            code="SETTLEMENT_POLICY_INVALID",
            message="Settlement cutoff hour must be between 0 and 23",
            http_status=422,
            details={"cutoffHour": window.cutoff_hour},
        )
    if not 0 <= window.cutoff_minute <= 59:
        raise RelayPayError(
            code="SETTLEMENT_POLICY_INVALID",
            message="Settlement cutoff minute must be between 0 and 59",
            http_status=422,
            details={"cutoffMinute": window.cutoff_minute},
        )
    if window.settlement_delay_days not in {0, 1, MAX_SETTLEMENT_DELAY_DAYS}:
        raise RelayPayError(
            code="SETTLEMENT_POLICY_INVALID",
            message="Settlement delay must be T+0, T+1, or T+2",
            http_status=422,
            details={"settlementDelayDays": window.settlement_delay_days},
        )
    if window.weekend_handling not in WEEKEND_HANDLING_VALUES:
        raise RelayPayError(
            code="SETTLEMENT_POLICY_INVALID",
            message="Weekend handling must be INCLUDE or SKIP",
            http_status=422,
            details={"weekendHandling": window.weekend_handling},
        )


def resolve_zone(window: PolicyWindow) -> ZoneInfo:
    return ZoneInfo(window.timezone_name)


def cutoff_at(window: PolicyWindow, business_date: date) -> datetime:
    """The inclusive daily cutoff instant for one business date."""
    return datetime.combine(
        business_date,
        time(window.cutoff_hour, window.cutoff_minute),
        tzinfo=resolve_zone(window),
    )


def business_date_of(window: PolicyWindow, moment: datetime) -> date:
    """The business date a moment belongs to; the cutoff instant itself is inclusive."""
    local = moment.astimezone(resolve_zone(window))
    if local.time() <= time(window.cutoff_hour, window.cutoff_minute):
        return local.date()
    return local.date() + timedelta(days=1)


def next_business_date(window: PolicyWindow, value: date) -> date:
    """The next settlement business date strictly after ``value``."""
    current = value + timedelta(days=1)
    if window.weekend_handling == "SKIP":
        while current.weekday() >= 5:
            current += timedelta(days=1)
    return current


def arrival_date(window: PolicyWindow, business_date: date) -> date:
    """Expected arrival for a business date under T+0/T+1/T+2 weekday rules.

    Weekends are counted as delay days when included and skipped otherwise;
    a T+0 weekend settlement under skip semantics still arrives on Monday.
    """
    current = business_date
    remaining = window.settlement_delay_days
    skip_weekends = window.weekend_handling == "SKIP"
    while remaining > 0 or (skip_weekends and current.weekday() >= 5):
        current += timedelta(days=1)
        if skip_weekends:
            if current.weekday() < 5:
                remaining -= 1
        else:
            remaining -= 1
    return current


def local_day_bounds(window: PolicyWindow, value: date) -> tuple[datetime, datetime]:
    """[start, end) instants of the local calendar day of ``value``."""
    zone = resolve_zone(window)
    start = datetime.combine(value, time.min, tzinfo=zone)
    return start, start + timedelta(days=1)


def format_inr(paise: int) -> str:
    """Canonical deterministic narrative formatting for an INR paise amount."""
    return f"INR {paise / 100:.2f}"
