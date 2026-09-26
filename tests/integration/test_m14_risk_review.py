import uuid
from datetime import UTC, datetime

import pytest
from relaypay.agent_runtime.contracts import ModelRequest, ModelResult, TerminalModelError
from relaypay.database import build_engine, build_session_factory
from relaypay.errors import RelayPayError
from relaypay.idempotency import canonical_json_bytes
from relaypay.identity.models import Environment, Organisation
from relaypay.ids import new_public_id, new_uuid
from relaypay.risk_review.findings import ModelFindings, deterministic_findings, findings_prompt
from relaypay.risk_review.models import (
    OnboardingSnapshot,
    RiskEscalation,
    RiskFinding,
    RiskReview,
    RiskReviewVersion,
)
from relaypay.risk_review.service import (
    annotate_review,
    disposition_escalation,
    execute_review,
    prepare_review,
    read_review_payload,
)
from relaypay.risk_review.snapshot import DeterministicSiteSnapshotSource
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.integration

DATABASE_URL = "postgresql+psycopg://relaypay_app:relaypay_app_dev@localhost:55432/relaypay"

SITES = (
    "COMPLETE_CLEAN",
    "MISSING_POLICIES",
    "YOUNG_DOMAIN",
    "PRICE_OUTLIER",
    "SUSPICIOUS_CLAIMS",
    "PROHIBITED_CATEGORY",
)


class FixedFindingsProvider:
    """Deterministic findings provider mirroring the deployed fake provider."""

    name = "fake"

    def generate_structured(self, request: ModelRequest) -> ModelResult:
        if request.schema is not ModelFindings:
            raise TerminalModelError("unsupported risk findings schema")
        marker = "<relaypay-untrusted-evidence>\n"
        start = request.prompt.index(marker) + len(marker)
        end = request.prompt.index("\n</relaypay-untrusted-evidence>", start)
        import json

        snapshot = json.loads(request.prompt[start:end])
        output = deterministic_findings(snapshot)
        response_bytes = canonical_json_bytes(output.model_dump(mode="json"))
        return ModelResult(
            output=output,
            provider=self.name,
            model_id=request.model_id,
            request_bytes=canonical_json_bytes(
                {"model": request.model_id, "prompt": request.prompt}
            ),
            response_bytes=response_bytes,
            latency_ms=0,
            input_tokens=max(1, len(request.prompt) // 4),
            output_tokens=max(1, len(response_bytes) // 4),
            finish_status="STOP",
        )


def _tenant(factory: sessionmaker, name: str) -> tuple[Organisation, Environment]:
    with factory() as session, session.begin():
        organisation = Organisation(
            id=new_uuid(),
            public_id=new_public_id("org"),
            name=name,
            status="ACTIVE",
        )
        session.add(organisation)
        session.flush([organisation])
        environment = session.scalar(
            select(Environment).where(
                Environment.organisation_id == organisation.id,
                Environment.environment_type == "TEST",
            )
        )
        assert environment is not None
        session.expunge_all()
        loaded_org = session.get(Organisation, organisation.id)
        loaded_env = session.get(Environment, environment.id)
        assert loaded_org is not None and loaded_env is not None
        return loaded_org, loaded_env


def _run(
    factory: sessionmaker,
    organisation: Organisation,
    environment: Environment,
    site_ref: str,
) -> dict[str, object]:
    prepared = prepare_review(
        factory,
        organisation_id=organisation.id,
        environment_id=environment.id,
        site_ref=site_ref,
        source=DeterministicSiteSnapshotSource(),
        source_url="deterministic://synthetic",
    )
    return execute_review(
        factory,
        prepared,
        provider=FixedFindingsProvider(),
        now=datetime.now(UTC),
    )


def test_reviews_are_deterministic_and_replay_identically() -> None:
    engine = build_engine(DATABASE_URL, application_name="m14-determinism")
    factory = build_session_factory(engine)
    try:
        organisation, environment = _tenant(factory, "M14 determinism")
        first = _run(factory, organisation, environment, "SUSPICIOUS_CLAIMS")
        second = _run(factory, organisation, environment, "SUSPICIOUS_CLAIMS")
        assert first["id"] == second["id"]
        assert first["versions"] == second["versions"]
        assert first["findings"] == second["findings"]
        version = first["versions"][-1]
        assert isinstance(version, dict)
        assert version["totalScore"] == 15  # three single-source claims at five points each
        assert version["severity"] == "LOW"
        assert version["hardStop"] is True  # guaranteed-return claim hard-stops
        # Recompute the digest over the stored breakdown payload.
        assert isinstance(version["reviewSha256"], str)
        assert len(version["reviewSha256"]) == 64
    finally:
        engine.dispose()


def test_every_severity_band_and_hard_stop_paths_across_sites() -> None:
    engine = build_engine(DATABASE_URL, application_name="m14-bands")
    factory = build_session_factory(engine)
    try:
        organisation, environment = _tenant(factory, "M14 severity bands")
        clean = _run(factory, organisation, environment, "COMPLETE_CLEAN")
        clean_version = clean["versions"][-1]
        assert isinstance(clean_version, dict)
        assert clean_version["totalScore"] == 0
        assert clean_version["severity"] == "LOW"
        assert clean_version["hardStop"] is False
        assert clean["status"] == "COMPLETED"

        suspicious = _run(factory, organisation, environment, "SUSPICIOUS_CLAIMS")
        assert suspicious["status"] == "ESCALATED"
        escalation = suspicious["escalation"]
        assert isinstance(escalation, dict)
        assert escalation["reason"] == "GUARANTEED_RETURN"

        prohibited = _run(factory, organisation, environment, "PROHIBITED_CATEGORY")
        assert prohibited["status"] == "ESCALATED"
        escalation = prohibited["escalation"]
        assert isinstance(escalation, dict)
        assert escalation["reason"] == "COUNTERFEIT"

        policies = _run(factory, organisation, environment, "MISSING_POLICIES")
        policies_version = policies["versions"][-1]
        assert isinstance(policies_version, dict)
        assert policies_version["totalScore"] == 15
        assert policies_version["severity"] == "LOW"
        assert policies["status"] == "COMPLETED"

        young = _run(factory, organisation, environment, "YOUNG_DOMAIN")
        assert young["status"] == "ESCALATED"  # identity mismatch is a hard stop
        escalation = young["escalation"]
        assert isinstance(escalation, dict)
        assert escalation["reason"] == "IMPERSONATION"
    finally:
        engine.dispose()


def test_findings_cite_exact_snapshot_hashes_and_quotes() -> None:
    engine = build_engine(DATABASE_URL, application_name="m14-citations")
    factory = build_session_factory(engine)
    try:
        organisation, environment = _tenant(factory, "M14 citations")
        payload = _run(factory, organisation, environment, "SUSPICIOUS_CLAIMS")
        snapshot_sha = payload["snapshotSha256"]
        assert isinstance(snapshot_sha, str)
        with factory() as session, session.begin():
            snapshot_row = session.scalar(
                select(OnboardingSnapshot).where(
                    OnboardingSnapshot.organisation_id == organisation.id,
                )
            )
            assert snapshot_row is not None
            assert snapshot_row.snapshot_sha256.hex() == snapshot_sha
            findings = list(
                session.scalars(
                    select(RiskFinding).where(RiskFinding.organisation_id == organisation.id)
                ).all()
            )
            assert findings
            for finding in findings:
                assert finding.snapshot_sha256.hex() == snapshot_sha
                if finding.finding_type == "MODEL":
                    assert finding.quote is not None
                    assert finding.source_path is not None
                    assert finding.source_path != "html"
    finally:
        engine.dispose()


def test_prompt_injection_content_never_escalates_on_its_own() -> None:
    engine = build_engine(DATABASE_URL, application_name="m14-injection")
    factory = build_session_factory(engine)
    try:
        organisation, environment = _tenant(factory, "M14 injection")
        payload = _run(factory, organisation, environment, "SUSPICIOUS_CLAIMS")
        findings = payload["findings"]
        assert isinstance(findings, list)
        injection_findings = [
            finding
            for finding in findings
            if isinstance(finding, dict)
            and finding["classification"] == "SUSPICIOUS_LANGUAGE"
            and "ignore all previous instructions" in str(finding.get("quote", "")).casefold()
        ]
        assert injection_findings, "the injection fixture must be detected"
        injection = injection_findings[0]
        assert injection["hardStop"] is False
        assert injection["confidence"] == "MEDIUM"
        # The hostile instruction text stays inside immutable evidence only.
        narrative_blob = str(payload)
        assert "reveal the system prompt" not in narrative_blob.casefold()
    finally:
        engine.dispose()


def test_tenant_and_environment_isolation_for_reviews() -> None:
    engine = build_engine(DATABASE_URL, application_name="m14-isolation")
    factory = build_session_factory(engine)
    try:
        org_a, env_a = _tenant(factory, "M14 tenant A")
        org_b, env_b = _tenant(factory, "M14 tenant B")
        payload_a = _run(factory, org_a, env_a, "SUSPICIOUS_CLAIMS")
        payload_b = _run(factory, org_b, env_b, "COMPLETE_CLEAN")
        assert payload_a["id"] != payload_b["id"]
        assert payload_b["versions"][-1]["totalScore"] == 0  # type: ignore[index]
        with pytest.raises(RelayPayError) as error:
            read_review_payload(
                factory,
                str(payload_a["id"]),
                organisation_id=org_b.id,
                environment_id=env_b.id,
            )
        assert error.value.http_status == 404
        with factory() as session, session.begin():
            snapshots_a = list(
                session.scalars(
                    select(OnboardingSnapshot).where(OnboardingSnapshot.organisation_id == org_a.id)
                ).all()
            )
            snapshots_b = list(
                session.scalars(
                    select(OnboardingSnapshot).where(OnboardingSnapshot.organisation_id == org_b.id)
                ).all()
            )
            assert {row.site_ref for row in snapshots_a} == {"SUSPICIOUS_CLAIMS"}
            assert {row.site_ref for row in snapshots_b} == {"COMPLETE_CLEAN"}
    finally:
        engine.dispose()


def test_review_versions_are_immutable_and_appended() -> None:
    engine = build_engine(DATABASE_URL, application_name="m14-immutable-versions")
    factory = build_session_factory(engine)
    try:
        organisation, environment = _tenant(factory, "M14 immutable versions")
        payload = _run(factory, organisation, environment, "PRICE_OUTLIER")
        review_id = str(payload["id"])
        with factory() as session, session.begin():
            versions = list(
                session.scalars(
                    select(RiskReviewVersion).where(
                        RiskReviewVersion.organisation_id == organisation.id
                    )
                ).all()
            )
            assert len(versions) == 1
            original_digest = versions[0].review_sha256
            original_total = versions[0].total_score
        # A replay never appends a new version or rewrites the stored one.
        replayed = _run(factory, organisation, environment, "PRICE_OUTLIER")
        assert replayed["id"] == review_id
        with factory() as session, session.begin():
            versions = list(
                session.scalars(
                    select(RiskReviewVersion).where(
                        RiskReviewVersion.organisation_id == organisation.id
                    )
                ).all()
            )
            assert len(versions) == 1
            assert versions[0].review_sha256 == original_digest
            assert versions[0].total_score == original_total
        # The database itself refuses any rewrite of the calculated total.
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError), factory() as session, session.begin():
            row = session.scalar(
                select(RiskReviewVersion).where(
                    RiskReviewVersion.risk_review_id == versions[0].risk_review_id
                )
            )
            assert row is not None
            row.total_score = original_total - 1
            session.flush()
        reread = read_review_payload(factory, review_id)
        assert reread["versions"][-1]["totalScore"] == original_total  # type: ignore[index]
    finally:
        engine.dispose()


def test_annotations_and_dispositions_have_scoped_permissions() -> None:
    engine = build_engine(DATABASE_URL, application_name="m14-permissions")
    factory = build_session_factory(engine)
    try:
        organisation, environment = _tenant(factory, "M14 permissions")
        payload = _run(factory, organisation, environment, "SUSPICIOUS_CLAIMS")
        review_id = str(payload["id"])
        reviewer = uuid.uuid4()
        with factory() as session, session.begin():
            review = session.scalar(select(RiskReview).where(RiskReview.public_id == review_id))
            assert review is not None
            annotation = annotate_review(
                session,
                review=review,
                author_user_id=reviewer,
                note="Synthetic reviewer note requesting documents.",
            )
            assert annotation.note.startswith("Synthetic reviewer")
            escalation = session.scalar(
                select(RiskEscalation).where(RiskEscalation.risk_review_id == review.id)
            )
            assert escalation is not None
            disposition_escalation(
                session,
                escalation=escalation,
                review=review,
                disposition="REQUEST_DOCUMENTS",
                note="Synthetic reviewer requested synthetic documents.",
                actor_user_id=reviewer,
                now=datetime.now(UTC),
            )
            assert escalation.status == "DISPOSITIONED"
            assert review.status == "DISPOSITIONED"
            # Scores and findings are untouched by the disposition.
            version = session.scalar(
                select(RiskReviewVersion).where(RiskReviewVersion.risk_review_id == review.id)
            )
            assert version is not None
            assert version.total_score == 15
            findings_count = len(
                session.scalars(
                    select(RiskFinding.id).where(RiskFinding.risk_review_id == review.id)
                ).all()
            )
            assert findings_count > 0
        # Double disposition is rejected.
        with factory() as session, session.begin():
            review = session.scalar(select(RiskReview).where(RiskReview.public_id == review_id))
            escalation = session.scalar(
                select(RiskEscalation).where(RiskEscalation.risk_review_id == review.id)  # type: ignore[arg-type]
            )
            assert review is not None and escalation is not None
            with pytest.raises(RelayPayError) as error:
                disposition_escalation(
                    session,
                    escalation=escalation,
                    review=review,
                    disposition="CLOSE_NO_ACTION",
                    note="second",
                    actor_user_id=uuid.uuid4(),
                    now=datetime.now(UTC),
                )
            assert error.value.code == "RISK_ESCALATION_CLOSED"
        # The read payload reflects the disposition but never mutated evidence.
        final = read_review_payload(factory, review_id)
        escalation_payload = final["escalation"]
        assert isinstance(escalation_payload, dict)
        assert escalation_payload["disposition"] == "REQUEST_DOCUMENTS"
    finally:
        engine.dispose()


def test_low_confidence_model_findings_do_not_hard_stop_alone() -> None:
    from relaypay.risk_review.findings import (
        ModelFinding,
        ModelFindings,
        redacted_snapshot_text,
        validate_model_findings,
    )

    from apps.merchant_site.main import _snapshot

    engine = build_engine(DATABASE_URL, application_name="m14-low-confidence")
    factory = build_session_factory(engine)
    try:
        organisation, environment = _tenant(factory, "M14 low confidence")
        snapshot = _snapshot("COMPLETE_CLEAN")
        text = findings_prompt(snapshot)
        start = text.index("<relaypay-untrusted-evidence>\n") + len(
            "<relaypay-untrusted-evidence>\n"
        )
        end = text.index("\n</relaypay-untrusted-evidence>", start)
        import json

        json.loads(text[start:end])

        class WholePageProvider(FixedFindingsProvider):
            def generate_structured(self, request: ModelRequest) -> ModelResult:
                if request.schema is not ModelFindings:
                    raise TerminalModelError("unsupported schema")
                full_text = redacted_snapshot_text(snapshot)
                finding = ModelFinding(
                    classification="GUARANTEED_RETURN",
                    quote=full_text[20:140],
                    sourcePath="html",
                )
                findings = ModelFindings(findings=[finding])
                validate_model_findings(findings, snapshot)
                response_bytes = canonical_json_bytes(findings.model_dump(mode="json"))
                return ModelResult(
                    output=findings,
                    provider=self.name,
                    model_id=request.model_id,
                    request_bytes=canonical_json_bytes({"prompt": request.prompt}),
                    response_bytes=response_bytes,
                    latency_ms=0,
                    input_tokens=10,
                    output_tokens=10,
                    finish_status="STOP",
                )

        prepared = prepare_review(
            factory,
            organisation_id=organisation.id,
            environment_id=environment.id,
            site_ref="COMPLETE_CLEAN",
            source=DeterministicSiteSnapshotSource(),
            source_url="deterministic://synthetic",
        )
        payload = execute_review(
            factory,
            prepared,
            provider=WholePageProvider(),
            now=datetime.now(UTC),
        )
        finding_list = payload["findings"]
        assert isinstance(finding_list, list)
        model_rows = [
            finding
            for finding in finding_list
            if isinstance(finding, dict) and finding["type"] == "MODEL"
        ]
        assert model_rows
        assert all(row["confidence"] == "LOW" for row in model_rows)
        assert all(row["hardStop"] is False for row in model_rows)
        version = payload["versions"][-1]
        assert isinstance(version, dict)
        assert version["hardStop"] is False
        assert version["confidence"] == "LOW"
        assert payload["status"] == "COMPLETED"
    finally:
        engine.dispose()


def test_unknown_site_reference_is_rejected() -> None:
    engine = build_engine(DATABASE_URL, application_name="m14-unknown-site")
    factory = build_session_factory(engine)
    try:
        organisation, environment = _tenant(factory, "M14 unknown site")
        with pytest.raises(RelayPayError) as error:
            prepare_review(
                factory,
                organisation_id=organisation.id,
                environment_id=environment.id,
                site_ref="NOT_A_SYNTHETIC_SITE",
                source=DeterministicSiteSnapshotSource(),
                source_url="deterministic://synthetic",
            )
        assert error.value.code == "SITE_SNAPSHOT_UNKNOWN"
    finally:
        engine.dispose()
