from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from relaypay.errors import RelayPayError, not_found
from relaypay.identity.models import Environment
from relaypay.identity.security import Principal
from relaypay.pagination import CursorPosition, decode_cursor, encode_cursor


@dataclass(frozen=True, slots=True)
class OperationsPage:
    data: list[dict[str, object]]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class ResourceQuery:
    source: str
    fields: str


_RESOURCES: dict[str, ResourceQuery] = {
    "merchant-accounts": ResourceQuery(
        "merchant_accounts",
        'public_id AS "id", reference, name, currency, status, is_default AS "isDefault"',
    ),
    "settlements": ResourceQuery(
        "settlement_runs",
        'public_id AS "id", status, settled_amount AS "settledAmount", '
        'completed_at AS "completedAt"',
    ),
    "beneficiaries": ResourceQuery(
        "beneficiaries",
        'public_id AS "id", reference, display_name AS "displayName", '
        "concat('••••', right(bank_account_reference, 4)) AS \"bankAccountReference\", "
        "currency, status",
    ),
    "payouts": ResourceQuery(
        "payouts",
        'public_id AS "id", amount, currency, status, failure_code AS "failureCode", '
        'review_reason AS "reviewReason"',
    ),
    "connectors": ResourceQuery(
        "connectors",
        'public_id AS "id", reference, kind, status, circuit_state AS "circuitState", '
        'consecutive_failures AS "consecutiveFailures"',
    ),
    "inbound-webhooks": ResourceQuery(
        "inbound_webhook_events",
        'public_id AS "id", provider_event_id AS "providerEventId", status, '
        'attempt_count AS "attemptCount", encode(payload_sha256, \'hex\') AS "payloadSha256"',
    ),
    "outbound-webhooks": ResourceQuery(
        "webhook_deliveries",
        'public_id AS "id", status, attempt_count AS "attemptCount", '
        'next_attempt_at AS "nextAttemptAt", delivered_at AS "deliveredAt"',
    ),
    "dead-letters": ResourceQuery(
        "webhook_deliveries",
        'public_id AS "id", status, attempt_count AS "attemptCount", '
        "dead_lettered_at AS \"deadLetteredAt\", 'OUTBOUND_WEBHOOK' AS kind",
    ),
    "reconciliation": ResourceQuery(
        "reconciliation_mismatches",
        'public_id AS "id", mismatch_type AS "type", workflow_status AS status, '
        'acknowledgement_note AS "acknowledgementNote", resolution_note AS "resolutionNote"',
    ),
    "api-keys": ResourceQuery(
        "api_keys",
        'public_id AS "id", name, scopes, status, revoked_at AS "revokedAt"',
    ),
    "audit-logs": ResourceQuery(
        "audit_records",
        'public_id AS "id", actor_type AS "actorType", action, target_type AS "targetType", '
        'target_id AS "targetId", details',
    ),
    "usage": ResourceQuery(
        "request_logs",
        'request_id AS "id", method, route, status_code AS "statusCode", '
        'duration_ms AS "durationMs"',
    ),
    "operational-metrics": ResourceQuery(
        "request_logs",
        'request_id AS "id", method, route, status_code AS "statusCode", '
        'duration_ms AS "durationMs"',
    ),
}


def resource_names() -> tuple[str, ...]:
    return tuple(_RESOURCES)


def _json_value(value: Any) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def list_operations_resource(
    session: Session,
    *,
    principal: Principal,
    environment_public_id: str,
    resource: str,
    limit: int,
    after: str | None,
    cursor_secret: str,
) -> OperationsPage:
    definition = _RESOURCES.get(resource)
    if definition is None:
        raise RelayPayError(
            code="RESOURCE_NOT_SUPPORTED",
            message="The requested operations resource is not supported",
            http_status=404,
        )
    environment = (
        session.query(Environment)
        .filter_by(
            organisation_id=principal.organisation_id,
            public_id=environment_public_id,
            status="ACTIVE",
        )
        .one_or_none()
    )
    if environment is None:
        raise not_found("Environment")

    filters: dict[str, object] = {
        "organisationId": principal.organisation_public_id,
        "environmentId": environment_public_id,
        "resource": resource,
    }
    position = (
        decode_cursor(after, filters=filters, secret=cursor_secret) if after is not None else None
    )
    cursor_clause = ""
    parameters: dict[str, object] = {
        "organisation_id": principal.organisation_id,
        "environment_id": environment.id,
        "limit": limit + 1,
    }
    if position is not None:
        cursor_clause = "AND (created_at, id) < (:cursor_created_at, CAST(:cursor_id AS uuid))"
        parameters.update(
            cursor_created_at=position.created_at,
            cursor_id=position.identifier,
        )
    dead_letter_clause = "AND status = 'DEAD_LETTER'" if resource == "dead-letters" else ""
    statement = text(
        f'SELECT {definition.fields}, created_at AS "createdAt", id AS "cursorId" '  # noqa: S608
        f"FROM {definition.source} WHERE organisation_id = :organisation_id "
        f"AND environment_id = :environment_id {dead_letter_clause} {cursor_clause} "
        "ORDER BY created_at DESC, id DESC LIMIT :limit"
    )
    rows = list(session.execute(statement, parameters).mappings())
    page_rows = rows[:limit]
    data = [
        {str(key): _json_value(value) for key, value in row.items() if key != "cursorId"}
        for row in page_rows
    ]
    next_cursor = None
    if len(rows) > limit and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(
            CursorPosition(
                created_at=last["createdAt"],
                identifier=str(last["cursorId"]),
            ),
            filters=filters,
            secret=cursor_secret,
        )
    return OperationsPage(data=data, next_cursor=next_cursor)
