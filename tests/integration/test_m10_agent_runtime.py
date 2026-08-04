import hashlib
import os
from datetime import UTC, datetime, timedelta

import pytest
from relaypay.agent_runtime.events import append_business_event, publish_one, record_consumption
from relaypay.agent_runtime.models import BusinessEventOutbox
from relaypay.agent_runtime.workflows import (
    activate_definition,
    claim_step,
    complete_step,
    start_run,
)
from relaypay.database import build_engine, build_session_factory
from relaypay.identity.models import Environment, Organisation
from relaypay.identity.security import Principal
from relaypay.ids import new_public_id, new_uuid
from sqlalchemy import select

pytestmark = pytest.mark.integration


class RecordingPublisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.values: list[bytes] = []

    def publish(self, *, topic: str, key: bytes, value: bytes) -> None:
        assert topic.endswith(".v1")
        assert key
        if self.fail:
            raise ConnectionError("synthetic broker outage")
        self.values.append(value)


def test_broker_loss_dedupe_lease_reclaim_version_pin_and_budget_gate() -> None:
    database_url = os.getenv(
        "RELAYPAY_DATABASE_URL",
        "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
    )
    engine = build_engine(database_url, application_name="m10-agent-runtime")
    factory = build_session_factory(engine)
    organisation_id, environment_id = new_uuid(), new_uuid()
    organisation_public_id, environment_public_id = new_public_id("org"), new_public_id("env")
    principal = Principal(
        kind="API_KEY",
        organisation_id=organisation_id,
        organisation_public_id=organisation_public_id,
        environment_id=environment_id,
        environment_public_id=environment_public_id,
        display_name="agent-test",
        scopes=frozenset({"technical:read", "workflows:write"}),
    )
    try:
        with factory() as session, session.begin():
            session.add(
                Organisation(
                    id=organisation_id,
                    public_id=organisation_public_id,
                    name="Agent runtime proof",
                    status="ACTIVE",
                )
            )
            session.add(
                Environment(
                    id=environment_id,
                    public_id=environment_public_id,
                    organisation_id=organisation_id,
                    name="Test",
                    environment_type="TEST",
                    status="ACTIVE",
                )
            )
            definition = activate_definition(
                session,
                principal=principal,
                name="proof",
                definition={"steps": [{"key": "model", "kind": "MODEL"}]},
            )
            session.flush()
            pinned_definition_id = definition.id
            run = start_run(
                session,
                principal=principal,
                definition_public_id=definition.public_id,
                route="POST:/proof",
                idempotency_key="same-command",
                token_budget=10,
                cost_budget_usd_micros=100,
            )
            duplicate = start_run(
                session,
                principal=principal,
                definition_public_id=definition.public_id,
                route="POST:/proof",
                idempotency_key="same-command",
                token_budget=10,
                cost_budget_usd_micros=100,
            )
            assert duplicate.id == run.id
            append_business_event(
                session,
                organisation_id=organisation_id,
                organisation_public_id=organisation_public_id,
                environment_id=environment_id,
                environment_public_id=environment_public_id,
                event_type="workflow.started",
                resource_type="workflowRun",
                resource_id=run.public_id,
                payload={"runId": run.public_id},
            )

        now = datetime.now(UTC)
        with pytest.raises(ConnectionError):
            publish_one(factory, RecordingPublisher(fail=True), now=now)
        with factory() as session, session.begin():
            event = session.scalar(select(BusinessEventOutbox))
            assert event is not None and event.published_at is None and event.lease_token is None
            event.next_attempt_at = now
        publisher = RecordingPublisher()
        assert publish_one(factory, publisher, now=now)
        assert len(publisher.values) == 1

        with factory() as session, session.begin():
            consumed_event_id = new_public_id("bev")
            assert record_consumption(
                session,
                organisation_id=organisation_id,
                environment_id=environment_id,
                consumer_name="proof-consumer",
                event_id=consumed_event_id,
                payload_sha256=hashlib.sha256(b"payload").digest(),
            )
            assert not record_consumption(
                session,
                organisation_id=organisation_id,
                environment_id=environment_id,
                consumer_name="proof-consumer",
                event_id=consumed_event_id,
                payload_sha256=hashlib.sha256(b"payload").digest(),
            )

        with factory() as session, session.begin():
            first_lease = claim_step(session, now=now, lease_seconds=1)
            assert first_lease is not None
        with factory() as session, session.begin():
            reclaimed = claim_step(session, now=now + timedelta(seconds=2))
            assert reclaimed is not None and reclaimed.lease_token != first_lease.lease_token
            step = complete_step(
                session,
                lease=reclaimed,
                output={"answer": "bounded"},
                tokens_used=11,
                cost_usd_micros=1,
                now=now,
            )
            assert step.status == "REQUIRES_REVIEW"
            assert step.safe_error_code == "MODEL_BUDGET_EXHAUSTED"
            assert step.workflow_run_id == run.id
            assert run.workflow_definition_id == pinned_definition_id
    finally:
        engine.dispose()
