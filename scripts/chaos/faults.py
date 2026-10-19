"""Fault vocabulary shared by schedule generation and the drivers.

Provider outcomes use the names of ``tests.support.fake_provider.Outcome`` (kept as strings here
so generating a schedule needs no database or test helper). Bank faults use the names accepted by
``relaypay.mock_bank.service.configure_fault``. Crash points are the names passed to
``relaypay.proof.crashpoints.maybe_crash``; ``api.after_initiate`` is called by the chaos driver
itself because the in-process driver plays the part of the API route.
"""

import random
from typing import Final

Weighted = tuple[tuple[str, int], ...]

# First-mutate outcomes the fake provider can be scripted with (SUCCEED is the default).
PROVIDER_FAULTS: Final[Weighted] = (
    ("LOSE_RESPONSE", 9),
    ("PENDING", 7),
    ("DECLINE", 4),
    ("LOSE_RESPONSE_DECLINE", 2),
)
# Faults for the bank simulator on a payout's first transfer.
BANK_FAULTS: Final[Weighted] = (("LOSE_RESPONSE", 3), ("DECLINE", 2))

# Crash points by the kind of unit of work that can reach them.
COMMAND_CRASH_POINTS: Final[Weighted] = (
    ("api.after_initiate", 2),
    ("provider.after_prepare_send", 2),
    ("provider.after_mutate_response", 4),
    ("provider.after_lookup_response", 1),
    ("finalizer.after_commit", 3),
)
TICK_CRASH_POINTS: Final[Weighted] = (
    ("poller.between_tasks", 3),
    ("finalizer.after_commit", 3),
)
PAYOUT_CRASH_POINTS: Final[Weighted] = (
    ("payout.after_reservation", 2),
    ("payout.after_prepare_send", 1),
    ("payout.after_bank_response", 3),
)
ALL_CRASH_POINTS: Final[tuple[str, ...]] = tuple(
    sorted(
        {
            name
            for table in (COMMAND_CRASH_POINTS, TICK_CRASH_POINTS, PAYOUT_CRASH_POINTS)
            for name, _ in table
        }
    )
)

# Share of eligible operations that carry a provider fault / a crash point.
FAULT_RATE: Final = 0.22
CRASH_RATE: Final = 0.12


def weighted_choice(rng: random.Random, table: Weighted) -> str:
    total = sum(weight for _, weight in table)
    draw = rng.randrange(total)
    for name, weight in table:
        if draw < weight:
            return name
        draw -= weight
    raise AssertionError("unreachable")
