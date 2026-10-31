"""Fixed clock for the M13 settlement scenario.

The scenario records captures, a forecast, a refund and questions at moments derived from one
anchor. The refund cash-impact query counts refunds on the Asia/Kolkata calendar day of the
question, so the refund and the question must share that day. Deriving them from the wall clock
put the question after local midnight whenever the run began in the last minutes of the day,
which failed the refund count in CI. The scenario therefore uses a fixed midday anchor, before
the default 17:00 cutoff, so every moment falls on one local day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

SCENARIO_ZONE = ZoneInfo("Asia/Kolkata")

# Thursday 12:00 IST. The captures, forecast, refund and questions all fall on this local day and
# before the default 17:00 cutoff.
SCENARIO_ANCHOR = datetime(2026, 10, 22, 6, 30, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ScenarioMoments:
    early_capture: datetime
    late_capture: datetime
    refund: datetime
    question: datetime


def scenario_moments(anchor: datetime) -> ScenarioMoments:
    return ScenarioMoments(
        early_capture=anchor - timedelta(hours=2),
        late_capture=anchor - timedelta(hours=1),
        refund=anchor + timedelta(seconds=1),
        question=anchor + timedelta(minutes=1),
    )


def local_calendar_day(moment: datetime) -> date:
    return moment.astimezone(SCENARIO_ZONE).date()
