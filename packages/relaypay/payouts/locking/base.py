"""Shared plumbing for reservers that go through the production payout creation path.

Each strategy differs only in the ``ReservationGuard`` it hands to ``create_payout`` (and whether
it retries); resolving public ids, the idempotency fingerprint and the outcome mapping are common.
"""

import json
import uuid
from collections.abc import Callable
from typing import ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.idempotency import build_fingerprint
from relaypay.identity.models import Environment
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.payouts.locking.types import ReserveOutcome, ReserveRequest, ReserveStatus
from relaypay.payouts.models import Beneficiary, Payout
from relaypay.payouts.service import CommandResult, ReservationGuard, create_payout

DEFAULT_KEY_PEPPER = "legacy-pessimistic-reserver-pepper"


class _PayoutFingerprintBody(BaseModel):
    merchant_account_id: str
    beneficiary_id: str
    amount: int


class GuardedReserver:
    """Reserve through ``create_payout`` under one ``ReservationGuard``.

    Subclasses override ``_execute`` to add what their guard needs around a single
    ``create_payout`` call (a retry loop, an error mapping).
    """

    name: ClassVar[str]
    guard: ClassVar[ReservationGuard]

    def __init__(self, key_pepper: str = DEFAULT_KEY_PEPPER) -> None:
        self._key_pepper = key_pepper

    def reserve(self, factory: sessionmaker[Session], request: ReserveRequest) -> ReserveOutcome:
        environment, merchant, beneficiary = self._public_ids(factory, request)
        fingerprint = build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template="/environments/{environment_id}/payouts",
            path_params={"environment_id": environment},
            body=_PayoutFingerprintBody(
                merchant_account_id=merchant, beneficiary_id=beneficiary, amount=request.amount
            ),
        )

        def create() -> CommandResult:
            return create_payout(
                factory,
                principal=request.principal,
                environment_public_id=environment,
                merchant_public_id=merchant,
                beneficiary_public_id=beneficiary,
                amount=request.amount,
                idempotency_key=request.idempotency_key,
                fingerprint=fingerprint,
                key_pepper=self._key_pepper,
                guard=self.guard,
            )

        result, attempts = self._execute(create)
        return self._outcome(factory, result, attempts)

    def _execute(self, create: Callable[[], CommandResult]) -> tuple[CommandResult | None, int]:
        """Run ``create`` once; ``None`` means the payout-eligible balance was insufficient."""
        try:
            return create(), 1
        except RelayPayError as error:
            if error.code == "INSUFFICIENT_PAYOUT_BALANCE":
                return None, 1
            raise

    @staticmethod
    def _outcome(
        factory: sessionmaker[Session], result: CommandResult | None, attempts: int
    ) -> ReserveOutcome:
        if result is None:
            return ReserveOutcome(ReserveStatus.INSUFFICIENT, None, attempts=attempts)
        payout_public_id = str(json.loads(result.body)["id"])
        with factory() as session, session.begin():
            payout_id: uuid.UUID | None = session.scalar(
                select(Payout.id).where(Payout.public_id == payout_public_id)
            )
        if payout_id is None:
            raise LookupError(f"payout {payout_public_id} not found after creation")
        status = ReserveStatus.REPLAYED if result.replayed else ReserveStatus.RESERVED
        return ReserveOutcome(status, payout_id, attempts=attempts)

    @staticmethod
    def _public_ids(
        factory: sessionmaker[Session], request: ReserveRequest
    ) -> tuple[str, str, str]:
        with factory() as session, session.begin():
            environment = session.scalar(
                select(Environment.public_id).where(Environment.id == request.environment_id)
            )
            merchant = session.scalar(
                select(MerchantAccount.public_id).where(
                    MerchantAccount.id == request.merchant_account_id
                )
            )
            beneficiary = session.scalar(
                select(Beneficiary.public_id).where(Beneficiary.id == request.beneficiary_id)
            )
        if environment is None or merchant is None or beneficiary is None:
            raise LookupError("environment, merchant account or beneficiary not found")
        return str(environment), str(merchant), str(beneficiary)
