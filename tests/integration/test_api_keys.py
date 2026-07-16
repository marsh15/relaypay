import pytest
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.identity.models import APIKey, Organisation
from relaypay.identity.security import authenticate_api_key, issue_api_key, require_scopes
from relaypay.ids import new_public_id

pytestmark = pytest.mark.integration


def test_api_key_is_prefix_plus_peppered_digest_and_scoped() -> None:
    pepper = "integration-api-key-pepper-at-least-32-bytes"
    issued, digest = issue_api_key(pepper=pepper)
    engine = build_engine(
        "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay",
        application_name="api-key-integration-test",
    )
    factory = build_session_factory(engine)
    with factory() as session, session.begin():
        organisation = Organisation(
            public_id=new_public_id("org"), name="API key tests", status="ACTIVE"
        )
        session.add(organisation)
        session.flush()
        session.add(
            APIKey(
                organisation_id=organisation.id,
                name="Merchant API key",
                public_prefix=issued.public_prefix,
                secret_digest=digest,
                scopes=["payments:read", "payments:write"],
                status="ACTIVE",
            )
        )

    with factory() as session, session.begin():
        principal = authenticate_api_key(session, plaintext=issued.plaintext, pepper=pepper)
        assert principal.kind == "API_KEY"
        assert principal.organisation_public_id.startswith("org_")
        require_scopes(principal, "payments:read")
        with pytest.raises(RelayPayError) as forbidden:
            require_scopes(principal, "admin")
        assert forbidden.value.http_status == 403

    with factory() as session, session.begin(), pytest.raises(RelayPayError) as invalid:
        authenticate_api_key(
            session, plaintext=f"{issued.public_prefix}.wrong-secret", pepper=pepper
        )
    assert invalid.value.http_status == 401
    assert issued.plaintext.encode("utf-8") != digest
    engine.dispose()
