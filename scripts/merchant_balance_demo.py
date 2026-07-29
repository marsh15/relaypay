"""Run the permanent lost-response proof through merchant settlement."""

import json
import uuid

from relaypay.config import get_settings
from relaypay.contracts import EmptyCommand
from relaypay.database import build_engine, build_session_factory
from relaypay.idempotency import build_fingerprint
from relaypay.identity.models import Environment, Organisation
from relaypay.identity.security import Principal
from relaypay.merchant_balances.models import MerchantAccount
from relaypay.merchant_balances.service import derive_balances, run_settlement
from sqlalchemy import select

from scripts.lost_response_demo import run as run_lost_response


def main() -> None:
    settings = get_settings()
    lost_response = run_lost_response(settings)
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-merchant-balance-demo",
    )
    factory = build_session_factory(engine)
    try:
        with factory() as session, session.begin():
            organisation = session.scalar(
                select(Organisation)
                .where(Organisation.name == "Lost response proof")
                .order_by(Organisation.created_at.desc())
            )
            assert organisation is not None
            environment = session.scalar(
                select(Environment).where(
                    Environment.organisation_id == organisation.id,
                    Environment.environment_type == "TEST",
                )
            )
            assert environment is not None
            merchant = session.scalar(
                select(MerchantAccount).where(
                    MerchantAccount.organisation_id == organisation.id,
                    MerchantAccount.environment_id == environment.id,
                    MerchantAccount.is_default.is_(True),
                )
            )
            assert merchant is not None
            before = derive_balances(session, merchant)
            principal = Principal(
                kind="SESSION",
                organisation_id=organisation.id,
                organisation_public_id=organisation.public_id,
                environment_id=None,
                environment_public_id=None,
                display_name="Synthetic M3 demo administrator",
                scopes=frozenset(),
                membership_role="ORGANISATION_ADMIN",
            )
        fingerprint = build_fingerprint(
            api_version="admin-v1",
            method="POST",
            route_template=(
                "/environments/{environment_id}/merchant-accounts/{merchant_account_id}/settlements"
            ),
            path_params={
                "environment_id": environment.public_id,
                "merchant_account_id": merchant.public_id,
            },
            body=EmptyCommand(),
        )
        settlement = run_settlement(
            factory,
            principal=principal,
            environment_public_id=environment.public_id,
            merchant_public_id=merchant.public_id,
            idempotency_key=f"m3-demo-settlement-{uuid.uuid4().hex}",
            fingerprint=fingerprint,
            key_pepper=settings.IDEMPOTENCY_KEY_PEPPER.get_secret_value(),
        )
        with factory() as session, session.begin():
            current = session.get(MerchantAccount, merchant.id)
            assert current is not None
            after = derive_balances(session, current)
        print(
            json.dumps(
                {
                    "availableAfter": after.available,
                    "lostResponseProof": lost_response,
                    "merchantAccountId": merchant.public_id,
                    "pendingAfter": after.pending,
                    "pendingBefore": before.pending,
                    "receivableAfter": after.receivable,
                    "settlement": json.loads(settlement.body),
                    "syntheticDataOnly": True,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
