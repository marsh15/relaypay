import uuid

import pytest
from relaypay.config import get_settings
from relaypay.database import build_engine, build_session_factory
from relaypay.observability.models import RequestLog, UsageRollup
from relaypay.observability.service import record_request
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration


def test_request_metadata_is_bounded_and_rolls_up_without_sensitive_material() -> None:
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(),
        application_name="relaypay-m7-observability-test",
    )
    factory = build_session_factory(engine)
    route = f"/m7-proof/{uuid.uuid4().hex}"
    try:
        for index in range(3):
            record_request(
                factory,
                request_id=f"req_m7_{uuid.uuid4().hex}",
                organisation_id=None,
                environment_id=None,
                method="POST",
                route=route,
                status_code=200,
                duration_ms=index + 1,
                retention=2,
            )
        with factory() as session, session.begin():
            logs = list(session.scalars(select(RequestLog).where(RequestLog.route == route)))
            rollup = session.scalar(select(UsageRollup).where(UsageRollup.route == route))
            assert len(logs) == 2
            assert rollup is not None
            assert rollup.request_count == 3
            assert rollup.duration_ms_total == 6
            assert rollup.duration_ms_max == 3
            columns = set(RequestLog.__table__.columns.keys())
            assert not columns.intersection(
                {"authorization", "headers", "cookies", "body", "api_key", "secret"}
            )
            session.execute(delete(RequestLog).where(RequestLog.route == route))
            session.execute(delete(UsageRollup).where(UsageRollup.route == route))
            assert session.scalar(select(func.count()).select_from(RequestLog)) is not None
    finally:
        engine.dispose()
