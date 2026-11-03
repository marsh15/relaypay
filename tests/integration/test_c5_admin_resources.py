"""C5: read-only admin resources for payments (status filter) and journals (postings detail).

Every read goes through the same tenant and environment scope as the generic operations list:
another organisation's rows never appear, foreign identifiers return 404, and cursors are bound
to their filters. The HTTP test covers the route wiring and the admin session requirement.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from relaypay.config import Settings
from relaypay.errors import RelayPayError
from relaypay.fees.service import create_fee_schedule_version
from relaypay.identity.models import OrganisationMembership, User
from relaypay.identity.security import Principal, hash_password
from relaypay.ledger.models import Journal
from relaypay.operations.service import OperationsPage, get_journal_detail, list_operations_resource
from relaypay.pagination import CursorPosition, encode_cursor
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.main import create_app
from tests.support.db import (
    provider_database_url,
    relaypay_database_url,
    relaypay_factory,
    require_postgres,
)
from tests.support.fake_provider import ACCOUNT_ID, SIGNING_SECRET, FakeProvider
from tests.support.world import World, _api_key_pepper, build_world, create_payment, paid_payment

pytestmark = [pytest.mark.integration]

CURSOR_SECRET = "c5-admin-resources-cursor-secret"
HTTP_PASSWORD = "C5-Synthetic-Admin-Password!"
FIRST_AMOUNT = 100_000
SECOND_AMOUNT = 250_000


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    require_postgres()
    with relaypay_factory("c5-admin-resources", pool_size=8) as session_factory:
        yield session_factory


@dataclass(frozen=True, slots=True)
class Scenario:
    world: World
    other: World
    captured: tuple[str, str]  # payment public ids captured in `world`
    created: str  # payment in `world` that was never authorised
    foreign_payment: str  # captured in `other`, so its journal is foreign to `world`


@pytest.fixture
def scenario(factory: sessionmaker[Session]) -> Scenario:
    world = build_world(factory, label="c5-main")
    other = build_world(factory, label="c5-other")
    provider = FakeProvider()
    first = paid_payment(factory, world, provider, amount=FIRST_AMOUNT).payment_id
    second = paid_payment(factory, world, provider, amount=SECOND_AMOUNT).payment_id
    created = create_payment(factory, world)
    foreign = paid_payment(factory, other, provider).payment_id
    return Scenario(
        world=world,
        other=other,
        captured=(first, second),
        created=created,
        foreign_payment=foreign,
    )


def _page(
    factory: sessionmaker[Session],
    principal: Principal,
    environment: str,
    resource: str,
    *,
    limit: int = 25,
    after: str | None = None,
    status: str | None = None,
) -> OperationsPage:
    with factory() as session, session.begin():
        return list_operations_resource(
            session,
            principal=principal,
            environment_public_id=environment,
            resource=resource,
            limit=limit,
            after=after,
            cursor_secret=CURSOR_SECRET,
            status=status,
        )


def _walk(
    factory: sessionmaker[Session],
    principal: Principal,
    environment: str,
    resource: str,
    *,
    limit: int,
    status: str | None = None,
) -> tuple[list[str], int]:
    """Follow every cursor; return the ids in order and how many pages were read."""
    ids: list[str] = []
    pages = 0
    after: str | None = None
    while True:
        page = _page(
            factory, principal, environment, resource, limit=limit, after=after, status=status
        )
        pages += 1
        ids.extend(str(row["id"]) for row in page.data)
        if page.next_cursor is None:
            return ids, pages
        after = page.next_cursor


def _detail(
    factory: sessionmaker[Session], principal: Principal, environment: str, journal_id: str
) -> dict[str, object]:
    with factory() as session, session.begin():
        return get_journal_detail(
            session,
            principal=principal,
            environment_public_id=environment,
            journal_public_id=journal_id,
        )


def test_payments_status_filter_is_cursor_paged_and_tenant_scoped(
    factory: sessionmaker[Session], scenario: Scenario
) -> None:
    world = scenario.world
    env = world.environment_public_id
    everything, pages = _walk(factory, world.admin_principal, env, "payments", limit=1)
    assert pages == 3
    assert sorted(everything) == sorted([*scenario.captured, scenario.created])
    assert scenario.foreign_payment not in everything

    captured, captured_pages = _walk(
        factory, world.admin_principal, env, "payments", limit=1, status="captured"
    )
    assert captured_pages == 2
    assert sorted(captured) == sorted(scenario.captured)

    created = _page(factory, world.admin_principal, env, "payments", status="created")
    assert created.next_cursor is None
    assert [row["id"] for row in created.data] == [scenario.created]
    assert created.data[0]["status"] == "created"
    assert created.data[0]["amount"] == 100_000
    assert created.data[0]["merchantReference"]


def test_payments_status_filter_rejects_unknown_and_unsupported_filters(
    factory: sessionmaker[Session], scenario: Scenario
) -> None:
    world = scenario.world
    env = world.environment_public_id
    with pytest.raises(RelayPayError) as unknown:
        _page(factory, world.admin_principal, env, "payments", status="paid")
    assert unknown.value.code == "VALIDATION_ERROR"
    assert unknown.value.http_status == 422

    with pytest.raises(RelayPayError) as unsupported:
        _page(factory, world.admin_principal, env, "journals", status="captured")
    assert unsupported.value.code == "VALIDATION_ERROR"
    assert unsupported.value.http_status == 422


def test_status_cursors_are_bound_to_their_filter(
    factory: sessionmaker[Session], scenario: Scenario
) -> None:
    world = scenario.world
    env = world.environment_public_id
    filtered = _page(factory, world.admin_principal, env, "payments", limit=1, status="captured")
    assert filtered.next_cursor is not None
    with pytest.raises(RelayPayError) as unfiltered:
        _page(factory, world.admin_principal, env, "payments", after=filtered.next_cursor)
    assert unfiltered.value.code == "INVALID_CURSOR"

    plain = _page(factory, world.admin_principal, env, "payments", limit=1)
    assert plain.next_cursor is not None
    with pytest.raises(RelayPayError) as refiltered:
        _page(
            factory,
            world.admin_principal,
            env,
            "payments",
            after=plain.next_cursor,
            status="captured",
        )
    assert refiltered.value.code == "INVALID_CURSOR"


def test_a_journal_cursor_cannot_page_the_payments_list(
    factory: sessionmaker[Session], scenario: Scenario
) -> None:
    world = scenario.world
    env = world.environment_public_id
    forged = encode_cursor(
        CursorPosition(created_at=datetime.now(UTC), identifier=str(uuid.uuid4())),
        filters={
            "organisationId": world.admin_principal.organisation_public_id,
            "environmentId": env,
            "resource": "journals",
        },
        secret=CURSOR_SECRET,
    )
    with pytest.raises(RelayPayError) as wrong_resource:
        _page(factory, world.admin_principal, env, "payments", after=forged)
    assert wrong_resource.value.code == "INVALID_CURSOR"


def test_journal_list_pages_every_row_and_excludes_other_tenants(
    factory: sessionmaker[Session], scenario: Scenario
) -> None:
    world = scenario.world
    env = world.environment_public_id
    with factory() as session, session.begin():
        expected = set(
            session.scalars(
                select(Journal.public_id).where(
                    Journal.organisation_id == world.organisation_id,
                    Journal.environment_id == world.environment_id,
                )
            )
        )
    assert len(expected) == 2  # one CAPTURE journal per captured payment

    listed, pages = _walk(factory, world.admin_principal, env, "journals", limit=1)
    assert pages == 2
    assert len(listed) == len(expected)
    assert set(listed) == expected

    page = _page(factory, world.admin_principal, env, "journals")
    assert {row["journalType"] for row in page.data} == {"CAPTURE"}
    assert {row["currency"] for row in page.data} == {"INR"}
    assert all(row["postedAt"] for row in page.data)

    other_env = scenario.other.environment_public_id
    foreign, _ = _walk(factory, scenario.other.admin_principal, other_env, "journals", limit=25)
    assert len(foreign) == 1
    assert not set(foreign) & expected

    with pytest.raises(RelayPayError) as cross_environment:
        _page(factory, scenario.other.admin_principal, env, "journals")
    assert cross_environment.value.code == "RESOURCE_NOT_FOUND"


def test_journal_detail_balances_and_hides_foreign_journals(
    factory: sessionmaker[Session], scenario: Scenario
) -> None:
    world = scenario.world
    env = world.environment_public_id
    journal_ids, _ = _walk(factory, world.admin_principal, env, "journals", limit=25)

    debit_totals: list[int] = []
    for journal_id in journal_ids:
        detail = _detail(factory, world.admin_principal, env, journal_id)
        postings = detail["postings"]
        assert isinstance(postings, list)
        assert detail["id"] == journal_id
        assert detail["balanced"] is True
        assert len(postings) >= 2
        debit = sum(p["amount"] for p in postings if p["side"] == "DEBIT")
        credit = sum(p["amount"] for p in postings if p["side"] == "CREDIT")
        assert debit == detail["debitTotal"]
        assert credit == detail["creditTotal"]
        assert debit == credit
        assert all(p["accountCode"] and p["accountName"] for p in postings)
        debit_totals.append(int(debit))
    assert sorted(debit_totals) == [FIRST_AMOUNT, SECOND_AMOUNT]

    with factory() as session, session.begin():
        foreign_journal = session.scalar(
            select(Journal.public_id).where(
                Journal.organisation_id == scenario.other.organisation_id
            )
        )
    assert foreign_journal is not None

    not_visible = (
        (world.admin_principal, foreign_journal),
        (world.admin_principal, "jrn_00000000000000000000000000000000"),
        (scenario.other.admin_principal, journal_ids[0]),
    )
    for principal, journal_id in not_visible:
        with pytest.raises(RelayPayError) as missing:
            _detail(factory, principal, env, journal_id)
        assert missing.value.http_status == 404
        assert missing.value.code == "RESOURCE_NOT_FOUND"


def _settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RELAYPAY_DATABASE_URL=relaypay_database_url(),
        PROVIDER_DATABASE_URL=provider_database_url(),
        RECEIVER_DATABASE_URL=relaypay_database_url(),
        SESSION_SECRET="c5-session-secret-at-least-32-bytes-long",
        CSRF_SECRET="c5-csrf-secret-at-least-32-bytes-long",
        API_KEY_PEPPER=_api_key_pepper(),
        IDEMPOTENCY_KEY_PEPPER="c5-idempotency-pepper",
        WEBHOOK_SECRET_ENCRYPTION_KEY="unused-in-c5-tests",
        PROVIDER_ACCOUNT_ID=ACCOUNT_ID,
        PROVIDER_SIGNING_SECRET=SIGNING_SECRET,
        PROVIDER_CONTROL_SECRET="c5-provider-control",
        RECEIVER_WEBHOOK_SECRET="c5-receiver-webhook",
    )


def test_admin_http_routes_need_a_session_and_expose_the_status_filter(
    factory: sessionmaker[Session], scenario: Scenario
) -> None:
    world = scenario.world
    env = world.environment_public_id
    with factory() as session, session.begin():
        admin = session.scalar(
            select(User)
            .join(OrganisationMembership, OrganisationMembership.user_id == User.id)
            .where(OrganisationMembership.organisation_id == world.organisation_id)
        )
        assert admin is not None
        admin.password_hash = hash_password(HTTP_PASSWORD)
        email = admin.email_normalized
        foreign_journal = session.scalar(
            select(Journal.public_id).where(
                Journal.organisation_id == scenario.other.organisation_id
            )
        )
    assert foreign_journal is not None

    base = f"/api/admin/v1/environments/{env}"
    with TestClient(create_app(_settings(), provider_transport=FakeProvider())) as client:
        assert client.get(f"{base}/operations/payments").status_code == 401

        login = client.post("/api/session/login", json={"email": email, "password": HTTP_PASSWORD})
        assert login.status_code == 200

        listed = client.get(
            f"{base}/operations/payments", params={"status": "captured", "limit": 1}
        )
        assert listed.status_code == 200, listed.text
        body = listed.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["status"] == "captured"
        assert body["nextCursor"] is not None

        bad_status = client.get(f"{base}/operations/payments", params={"status": "paid"})
        assert bad_status.status_code == 422
        assert bad_status.json()["error"]["code"] == "VALIDATION_ERROR"

        journals = client.get(f"{base}/operations/journals", params={"limit": 25}).json()["data"]
        assert len(journals) == 2
        detail = client.get(f"{base}/journals/{journals[0]['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["balanced"] is True
        assert detail.json()["debitTotal"] == detail.json()["creditTotal"]

        missing = client.get(f"{base}/journals/{foreign_journal}")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_fee_schedules_list_is_tenant_scoped(
    factory: sessionmaker[Session], scenario: Scenario
) -> None:
    effective = datetime(2026, 1, 1, tzinfo=UTC)
    with factory() as session, session.begin():
        for world, mdr in ((scenario.world, 200), (scenario.other, 999)):
            for rate in (mdr, mdr + 10):
                create_fee_schedule_version(
                    session,
                    merchant_account_id=world.merchant_account_id,
                    effective_from=effective,
                    mdr_bps=rate,
                    fixed_paise=0,
                )
    world = scenario.world
    ids, _ = _walk(
        factory, world.admin_principal, world.environment_public_id, "fee-schedules", limit=1
    )
    page = _page(
        factory, world.admin_principal, world.environment_public_id, "fee-schedules", limit=10
    )
    assert len(ids) == len(set(ids)) == 2
    assert sorted(row["version"] for row in page.data) == [1, 2]
    assert {row["mdrBps"] for row in page.data} == {200, 210}
    assert {row["merchantAccountId"] for row in page.data} == {world.merchant_public_id}
