import uuid

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from relaypay.errors import not_found
from relaypay.identity.models import Environment, Organisation
from relaypay.ids import new_public_id, new_uuid

_DEFAULTS_INSTALLED = False


def resolve_environment_id(
    session: Session,
    *,
    organisation_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> uuid.UUID:
    query = select(Environment.id).where(
        Environment.organisation_id == organisation_id,
        Environment.status == "ACTIVE",
    )
    if environment_id is None:
        query = query.where(Environment.environment_type == "TEST")
    else:
        query = query.where(Environment.id == environment_id)
    resolved = session.scalar(query)
    if resolved is None:
        raise not_found("Environment")
    return resolved


def install_environment_defaults() -> None:
    """Keep pre-environment internal callers compatible while enforcing DB isolation."""
    global _DEFAULTS_INSTALLED
    if _DEFAULTS_INSTALLED:
        return
    event.listen(Session, "before_flush", _assign_default_environments)
    event.listen(Session, "after_flush_postexec", _restore_deferred_tenant_rows)
    _DEFAULTS_INSTALLED = True


def _assign_default_environments(
    session: Session, _flush_context: object, _instances: object
) -> None:
    test_ids: dict[uuid.UUID, uuid.UUID] = {}
    new_organisation_ids: set[uuid.UUID] = set()
    pending_environments = {
        (item.organisation_id, item.environment_type): item
        for item in session.new
        if isinstance(item, Environment)
    }
    for item in list(session.new):
        if not isinstance(item, Organisation):
            continue
        if item.id is None:
            item.id = new_uuid()
        new_organisation_ids.add(item.id)
        pending_test = pending_environments.get((item.id, "TEST"))
        test_id = pending_test.id if pending_test is not None else new_uuid()
        test_ids[item.id] = test_id
        if pending_test is None:
            session.add(
                Environment(
                    id=test_id,
                    public_id=new_public_id("env"),
                    organisation=item,
                    name="Test",
                    environment_type="TEST",
                    status="ACTIVE",
                )
            )
        if (item.id, "LIVE_LIKE") not in pending_environments:
            session.add(
                Environment(
                    public_id=new_public_id("env"),
                    organisation=item,
                    name="Live-like",
                    environment_type="LIVE_LIKE",
                    status="ACTIVE",
                )
            )

    candidates: list[object] = []
    missing_organisations: set[uuid.UUID] = set()
    for item in list(session.new):
        mapper = inspect(type(item), raiseerr=False)
        if mapper is None or "environment_id" not in mapper.columns:
            continue
        column = mapper.columns["environment_id"]
        if column.nullable or getattr(item, "environment_id", None) is not None:
            continue
        organisation_id = getattr(item, "organisation_id", None)
        if organisation_id is None:
            continue
        candidates.append(item)
        if getattr(item, "id", None) is None:
            setattr(item, "id", new_uuid())  # noqa: B010
        if organisation_id not in test_ids:
            missing_organisations.add(organisation_id)

    if missing_organisations:
        rows = session.connection().execute(
            select(Environment.organisation_id, Environment.id).where(
                Environment.organisation_id.in_(missing_organisations),
                Environment.environment_type == "TEST",
            )
        )
        test_ids.update(dict(rows.tuples().all()))

    for item in candidates:
        organisation_id = getattr(item, "organisation_id")  # noqa: B009
        environment_id = test_ids.get(organisation_id)
        if environment_id is not None:
            setattr(item, "environment_id", environment_id)  # noqa: B010
        if organisation_id in new_organisation_ids:
            session.expunge(item)
            session.info.setdefault("relaypay_deferred_tenant_rows", []).append(item)


def _restore_deferred_tenant_rows(session: Session, _flush_context: object) -> None:
    deferred = session.info.pop("relaypay_deferred_tenant_rows", [])
    if deferred:
        session.add_all(deferred)
