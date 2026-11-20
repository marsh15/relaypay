"""M18: the provider simulator's statement carries fee and tax, and reconciliation compares them.

The simulator export (JSON) is converted to the streamed CSV the way the daily import does it,
imported, and matched by algorithm 2. Fee terms equal to the merchant's RelayPay schedule must
produce no fee or tax exception; different terms must produce both.
"""

import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.fees.service import create_fee_schedule_version
from relaypay.mock_provider.models import ProviderAccount, ProviderStatementItem
from relaypay.mock_provider.service import (
    EffectCommand,
    apply_effect,
    configure_fee_terms,
    export_statement,
)
from relaypay.reconciliation.schedule import json_statement_to_csv
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.integration.test_reconciliation_scale import Op, _import_v2, _operations, _outcome, _run
from tests.support.db import provider_factory, relaypay_factory, require_postgres
from tests.support.fake_provider import FakeProvider
from tests.support.world import World, build_world, paid_payment

pytestmark = pytest.mark.integration

SECRET = "statement-fees-secret"
MDR_BPS = 200
GST_BPS = 1800


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("statement-fee-tests", pool_size=6) as session_factory:
        yield session_factory


@pytest.fixture
def provider() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with provider_factory("statement-fee-provider-tests", pool_size=4) as session_factory:
        yield session_factory


def _world_with_scheduled_captures(
    factory: sessionmaker[Session], label: str
) -> tuple[World, list[Op]]:
    world = build_world(factory, label=label)
    with factory() as session, session.begin():
        create_fee_schedule_version(
            session,
            merchant_account_id=world.merchant_account_id,
            effective_from=datetime.now(UTC) - timedelta(days=1),
            mdr_bps=MDR_BPS,
            fixed_paise=0,
            gst_bps=GST_BPS,
        )
    fake = FakeProvider()
    captures: list[Op] = []
    for _ in range(3):
        paid = paid_payment(factory, world, fake)
        operations = _operations(
            factory, world, paid.authorization_operation_id, paid.capture_operation_id
        )
        captures.append(next(op for op in operations if op.kind == "CAPTURE"))
    return world, captures


def _provider_account(provider: sessionmaker[Session], captures: list[Op]) -> str:
    account = f"acct_fee_{uuid.uuid4().hex[:12]}"
    with provider() as session, session.begin():
        session.add(
            ProviderAccount(
                public_id=account,
                name="Fee statement test",
                signing_secret_digest=hashlib.sha256(SECRET.encode()).digest(),
            )
        )
    for capture in captures:
        apply_effect(
            provider,
            command=EffectCommand(
                account, capture.stable_key, "CAPTURE", f"ref_{capture.stable_key}", 100_000, "INR"
            ),
            signing_secret=SECRET,
        )
    return account


def _reconcile(
    factory: sessionmaker[Session], provider: sessionmaker[Session], world: World, account: str
) -> set[str]:
    now = datetime.now(UTC)
    exported = export_statement(
        provider,
        account_public_id=account,
        source_reference=f"fees-{uuid.uuid4().hex}",
        period_start=now - timedelta(hours=1),
        period_end=now + timedelta(hours=1),
    )
    _, run, _ = _import_v2(factory, world, json_statement_to_csv(exported.body), when=now)
    _run(factory, run)
    return set(_outcome(factory, run).types.values())


def test_the_export_has_no_fee_columns_until_terms_are_set(
    factory: sessionmaker[Session], provider: sessionmaker[Session]
) -> None:
    _, captures = _world_with_scheduled_captures(factory, "fees-none")
    account = _provider_account(provider, captures)
    now = datetime.now(UTC)

    plain = export_statement(
        provider,
        account_public_id=account,
        source_reference=f"plain-{uuid.uuid4().hex}",
        period_start=now - timedelta(hours=1),
        period_end=now + timedelta(hours=1),
    )

    assert all("feeAmount" not in item for item in json.loads(plain.body)["items"])
    assert json_statement_to_csv(plain.body).decode().splitlines()[0].endswith("occurredAt")


def test_matching_fee_terms_reconcile_with_no_fee_or_tax_exception(
    factory: sessionmaker[Session], provider: sessionmaker[Session]
) -> None:
    world, captures = _world_with_scheduled_captures(factory, "fees-match")
    account = _provider_account(provider, captures)
    configure_fee_terms(
        provider, account_public_id=account, mdr_bps=MDR_BPS, fixed_paise=0, gst_bps=GST_BPS
    )

    types = _reconcile(factory, provider, world, account)

    assert "FEE_MISMATCH" not in types and "TAX_MISMATCH" not in types
    with provider() as session, session.begin():
        fees = set(
            session.execute(
                select(ProviderStatementItem.fee_amount, ProviderStatementItem.tax_amount)
            ).all()
        ) & {(2_000, 360)}
    assert fees == {(2_000, 360)}  # 2% of 100,000 paise, and 18% of that fee


def test_different_fee_terms_are_reported_as_fee_and_tax_mismatches(
    factory: sessionmaker[Session], provider: sessionmaker[Session]
) -> None:
    world, captures = _world_with_scheduled_captures(factory, "fees-differ")
    account = _provider_account(provider, captures)
    configure_fee_terms(
        provider, account_public_id=account, mdr_bps=MDR_BPS + 50, fixed_paise=0, gst_bps=GST_BPS
    )

    types = _reconcile(factory, provider, world, account)

    assert {"FEE_MISMATCH", "TAX_MISMATCH"} <= types


def test_clearing_the_terms_restores_the_plain_export(
    factory: sessionmaker[Session], provider: sessionmaker[Session]
) -> None:
    _, captures = _world_with_scheduled_captures(factory, "fees-clear")
    account = _provider_account(provider, captures)
    configure_fee_terms(
        provider, account_public_id=account, mdr_bps=MDR_BPS, fixed_paise=0, gst_bps=GST_BPS
    )
    configure_fee_terms(
        provider, account_public_id=account, mdr_bps=None, fixed_paise=None, gst_bps=None
    )
    now = datetime.now(UTC)

    cleared = export_statement(
        provider,
        account_public_id=account,
        source_reference=f"cleared-{uuid.uuid4().hex}",
        period_start=now - timedelta(hours=1),
        period_end=now + timedelta(hours=1),
    )

    assert all("feeAmount" not in item for item in json.loads(cleared.body)["items"])


def test_partial_fee_terms_are_rejected(provider: sessionmaker[Session]) -> None:
    require_postgres()
    from relaypay.errors import RelayPayError

    with pytest.raises(RelayPayError) as raised:
        configure_fee_terms(
            provider, account_public_id="acct_none", mdr_bps=100, fixed_paise=None, gst_bps=None
        )
    assert raised.value.code == "INVALID_FEE_TERMS"
