"""Merchant events v2: payout parents, expiry, void and failure event types.

Revision ID: 0031_merchant_events_v2
Revises: 0030_coverage_check_lock
Create Date: 2026-11-19

A merchant event used to need a payment and a provider operation. Payout success has neither, and
expiry is local (no provider operation), so the parents become alternatives. Exactly one shape is
allowed, and a CHECK enforces it:

* operation event: ``payment_intent_id`` and ``provider_operation_id``;
* payout event: ``payout_id`` alone;
* expiry event: ``payment_intent_id`` alone, only for ``payment.expired.v1``.

The immutability trigger from 0003 is untouched. Existing rows already satisfy the operation shape.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_merchant_events_v2"
down_revision: str | None = "0030_coverage_check_lock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = ("payment.authorized.v1", "payment.captured.v1", "refund.succeeded.v1")
NEW_TYPES = (
    "payment.authorized.v1",
    "payment.captured.v1",
    "payment.failed.v1",
    "payment.cancelled.v1",
    "payment.expired.v1",
    "refund.succeeded.v1",
    "payout.succeeded.v1",
)


def _in_list(types: Sequence[str]) -> str:
    return ", ".join(f"'{t}'" for t in types)


def _array(types: Sequence[str]) -> str:
    return f"ARRAY[{_in_list(types)}]::varchar[]"


def upgrade() -> None:
    op.alter_column("merchant_events", "payment_intent_id", nullable=True)
    op.alter_column("merchant_events", "provider_operation_id", nullable=True)
    op.add_column("merchant_events", sa.Column("payout_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "merchant_events_payout_id_scope_fkey",
        "merchant_events",
        "payouts",
        ["organisation_id", "environment_id", "payout_id"],
        ["organisation_id", "environment_id", "id"],
    )

    op.drop_constraint("merchant_events_event_type_check", "merchant_events", type_="check")
    op.create_check_constraint(
        "merchant_events_event_type_check",
        "merchant_events",
        f"event_type IN ({_in_list(NEW_TYPES)})",
    )
    op.create_check_constraint(
        "merchant_events_parent_check",
        "merchant_events",
        "(payment_intent_id IS NOT NULL AND provider_operation_id IS NOT NULL "
        "AND payout_id IS NULL AND event_type <> 'payout.succeeded.v1') OR "
        "(payment_intent_id IS NULL AND provider_operation_id IS NULL "
        "AND payout_id IS NOT NULL AND event_type = 'payout.succeeded.v1') OR "
        "(payment_intent_id IS NOT NULL AND provider_operation_id IS NULL "
        "AND payout_id IS NULL AND event_type = 'payment.expired.v1')",
    )
    # One event per payout and type, and one expiry event per payment. The existing
    # (provider_operation_id, event_type) unique constraint still covers operation events.
    op.create_index(
        "uq_merchant_events_payout_type",
        "merchant_events",
        ["payout_id", "event_type"],
        unique=True,
        postgresql_where=sa.text("payout_id IS NOT NULL"),
    )
    op.create_index(
        "uq_merchant_events_payment_expired",
        "merchant_events",
        ["payment_intent_id", "event_type"],
        unique=True,
        postgresql_where=sa.text("provider_operation_id IS NULL AND payout_id IS NULL"),
    )

    op.drop_constraint(
        "webhook_endpoint_versions_subscribed_event_types_check",
        "webhook_endpoint_versions",
        type_="check",
    )
    op.create_check_constraint(
        "webhook_endpoint_versions_subscribed_event_types_check",
        "webhook_endpoint_versions",
        f"subscribed_event_types <@ {_array(NEW_TYPES)}",
    )


def downgrade() -> None:
    # Events of the new types (and payout or expiry parents) cannot exist under the old schema.
    # They are immutable evidence with recipients and deliveries hanging off them, so this
    # downgrade refuses rather than destroying them. Archive or remove them deliberately first.
    new_only = f"event_type NOT IN ({_in_list(OLD_TYPES)})"
    remaining = op.get_bind().scalar(
        sa.text(f"SELECT count(*) FROM merchant_events WHERE {new_only}")  # noqa: S608
    )
    if remaining:
        raise RuntimeError(
            f"{remaining} merchant events of v2 types exist; they are immutable evidence and "
            "the 0031 downgrade will not delete them"
        )
    op.drop_constraint(
        "webhook_endpoint_versions_subscribed_event_types_check",
        "webhook_endpoint_versions",
        type_="check",
    )
    op.create_check_constraint(
        "webhook_endpoint_versions_subscribed_event_types_check",
        "webhook_endpoint_versions",
        f"subscribed_event_types <@ {_array(OLD_TYPES)}",
    )
    op.drop_index("uq_merchant_events_payment_expired", table_name="merchant_events")
    op.drop_index("uq_merchant_events_payout_type", table_name="merchant_events")
    op.drop_constraint("merchant_events_parent_check", "merchant_events", type_="check")
    op.drop_constraint("merchant_events_event_type_check", "merchant_events", type_="check")
    op.create_check_constraint(
        "merchant_events_event_type_check",
        "merchant_events",
        f"event_type IN ({_in_list(OLD_TYPES)})",
    )
    op.drop_constraint(
        "merchant_events_payout_id_scope_fkey", "merchant_events", type_="foreignkey"
    )
    op.drop_column("merchant_events", "payout_id")
    op.alter_column("merchant_events", "provider_operation_id", nullable=False)
    op.alter_column("merchant_events", "payment_intent_id", nullable=False)
