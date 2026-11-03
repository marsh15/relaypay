"""Optimistic payout reservation (implemented by Claude at the maintainer's request).

Read ``merchant_accounts.balance_version`` with the balances, insert the reservation and
``UPDATE merchant_accounts SET balance_version = v + 1 WHERE id = :id AND balance_version = v`` in
the same transaction. If the UPDATE touches no row another reservation won: the transaction rolls
back and the reservation is retried, at most ``max_attempts`` times. ``ReserveOutcome.attempts``
reports how many transactions were tried. A repeated idempotency key returns REPLAYED. Under heavy
contention every round admits one winner, so ``max_attempts`` bounds the work a caller will spend
before the failure surfaces as ``OptimisticReservationExhausted``. See PROVENANCE.md.
"""

import random
import time
from collections.abc import Callable
from typing import ClassVar

from relaypay.errors import RelayPayError
from relaypay.payouts.locking.base import DEFAULT_KEY_PEPPER, GuardedReserver
from relaypay.payouts.service import CommandResult, ReservationGuard, StaleBalanceVersion

DEFAULT_MAX_ATTEMPTS = 1_000
MAX_BACKOFF_SECONDS = 0.005


class OptimisticReservationExhausted(RuntimeError):
    """Every attempt lost the compare-and-swap; the request was neither reserved nor refused."""


class OptimisticReserver(GuardedReserver):
    name: ClassVar[str] = "optimistic"
    guard: ClassVar[ReservationGuard] = ReservationGuard.VERSION

    def __init__(
        self, key_pepper: str = DEFAULT_KEY_PEPPER, max_attempts: int = DEFAULT_MAX_ATTEMPTS
    ) -> None:
        super().__init__(key_pepper)
        self._max_attempts = max_attempts

    def _execute(self, create: Callable[[], CommandResult]) -> tuple[CommandResult | None, int]:
        for attempt in range(1, self._max_attempts + 1):
            try:
                return create(), attempt
            except StaleBalanceVersion:
                # Jitter keeps the losers from retrying in lockstep.
                time.sleep(random.uniform(0, MAX_BACKOFF_SECONDS))  # noqa: S311 - not security
            except RelayPayError as error:
                if error.code == "INSUFFICIENT_PAYOUT_BALANCE":
                    return None, attempt
                raise
        raise OptimisticReservationExhausted(
            f"balance_version changed on all {self._max_attempts} attempts"
        )
