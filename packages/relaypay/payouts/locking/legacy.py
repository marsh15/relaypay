"""Control adapter: the existing production payout creation path behind ``PayoutReserver``.

No locking logic lives here. ``reserve`` resolves public ids, calls ``create_payout`` (which
takes ``SELECT ... FOR UPDATE`` on the merchant row inside ``_create_payout_transaction``) and
maps its result and ``INSUFFICIENT_PAYOUT_BALANCE`` error onto ``ReserveOutcome``.
"""

import json
from typing import ClassVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from relaypay.errors import RelayPayError
from relaypay.idempotency import build_fingerprint
from relaypay.identity.models import Environment
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.payouts.locking.types import (
    ReserveOutcome,
    ReserveRequest,
    ReserveStatus,
)
from relaypay.payouts.models import Beneficiary, Payout
from relaypay.payouts.service import create_payout

DEFAULT_KEY_PEPPER = "legacy-pessimistic-reserver-pepper"


class _PayoutFingerprintBody(BaseModel):
    merchant_account_id: str
    beneficiary_id: str
    amount: int


class LegacyPessimisticReserver:
    name: ClassVar[str] = "legacy_pessimistic"

    def __init__(self, key_pepper: str = DEFAULT_KEY_PEPPER) -> None:
        self._key_pepper = key_pepper

    def reserve(self, factory: sessionmaker[Session], request: ReserveRequest) -> ReserveOutcome:
        environment_public_id, merchant_public_id, beneficiary_public_id = self._public_ids(
            factory, request
        )
        fingerprint = build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template="/environments/{environment_id}/payouts",
            path_params={"environment_id": environment_public_id},
            body=_PayoutFingerprintBody(
                merchant_account_id=merchant_public_id,
                beneficiary_id=beneficiary_public_id,
                amount=request.amount,
            ),
        )
        try:
            result = create_payout(
                factory,
                principal=request.principal,
                environment_public_id=environment_public_id,
                merchant_public_id=merchant_public_id,
                beneficiary_public_id=beneficiary_public_id,
                amount=request.amount,
                idempotency_key=request.idempotency_key,
                fingerprint=fingerprint,
                key_pepper=self._key_pepper,
            )
        except RelayPayError as error:
            if error.code == "INSUFFICIENT_PAYOUT_BALANCE":
                return ReserveOutcome(ReserveStatus.INSUFFICIENT, None, attempts=1)
            raise
        payout_public_id = str(json.loads(result.body)["id"])
        with factory() as session, session.begin():
            payout_id = session.scalar(
                select(Payout.id).where(Payout.public_id == payout_public_id)
            )
        if payout_id is None:
            raise LookupError(f"payout {payout_public_id} not found after creation")
        status = ReserveStatus.REPLAYED if result.replayed else ReserveStatus.RESERVED
        return ReserveOutcome(status, payout_id, attempts=1)

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
