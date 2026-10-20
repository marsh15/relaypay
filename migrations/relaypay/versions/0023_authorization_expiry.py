"""Authorizations expire: hold duration, expiry time, and a capture guard on dead payments.

Revision ID: 0023_authorization_expiry
Revises: 0022_partial_capture
Create Date: 2026-10-20

A succeeded authorization now holds funds for ``hold_seconds`` (recorded when the authorization
is initiated; ``AUTH_HOLD_SECONDS``, default 7 days). The database derives
``expires_at = authorized_at + hold_seconds`` whenever an authorization becomes SUCCEEDED, so the
CHECK "succeeded if and only if it has an expiry" holds for every writer, not just the finalizer. A
poller task moves an authorised, never-captured payment to ``expired`` once ``expires_at``
passes, and ``initiate_capture`` refuses an expired authorization under the payment lock.

Existing succeeded authorizations are backfilled to ``authorized_at + hold_seconds``. The
capture-command trigger additionally rejects inserting a capture for a payment that is
cancelled, expired or failed. The check runs on INSERT only, because the finalizer's later
UPDATE of an in-flight capture must still succeed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_authorization_expiry"
down_revision: str | None = "0022_partial_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_HOLD_SECONDS = 604_800


def upgrade() -> None:
    op.add_column(
        "authorizations",
        sa.Column(
            "hold_seconds",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_HOLD_SECONDS),
        ),
    )
    op.add_column(
        "authorizations", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE authorizations
        SET expires_at = authorized_at + make_interval(secs => hold_seconds)
        WHERE status = 'SUCCEEDED'
        """
    )
    op.alter_column("authorizations", "hold_seconds", server_default=None)
    op.execute(
        """
        CREATE FUNCTION relaypay_derive_authorization_expiry() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status = 'SUCCEEDED' THEN
            NEW.expires_at := NEW.authorized_at + make_interval(secs => NEW.hold_seconds);
          ELSE
            NEW.expires_at := NULL;
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER authorizations_derive_expiry
          BEFORE INSERT OR UPDATE OF status, authorized_at, hold_seconds ON authorizations
          FOR EACH ROW EXECUTE FUNCTION relaypay_derive_authorization_expiry();
        """
    )
    op.create_check_constraint(
        "authorizations_hold_seconds_check", "authorizations", "hold_seconds > 0"
    )
    op.create_check_constraint(
        "authorizations_expiry_check",
        "authorizations",
        "(status = 'SUCCEEDED') = (expires_at IS NOT NULL)",
    )
    op.create_index(
        "ix_authorizations_expiry",
        "authorizations",
        ["expires_at"],
        postgresql_where=sa.text("status = 'SUCCEEDED'"),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION relaypay_validate_capture_command() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          authorization_amount bigint;
          authorization_status text;
          payment_status text;
        BEGIN
          SELECT amount, status INTO authorization_amount, authorization_status
          FROM authorizations
          WHERE id = NEW.authorization_id AND organisation_id = NEW.organisation_id;
          IF NEW.amount > authorization_amount THEN
            RAISE EXCEPTION 'capture % exceeds the authorized amount', NEW.id
              USING ERRCODE = '23514';
          END IF;
          IF authorization_status <> 'SUCCEEDED' THEN
            RAISE EXCEPTION 'capture % requires a succeeded authorization', NEW.id
              USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'INSERT' THEN
            SELECT status INTO payment_status FROM payment_intents
            WHERE id = NEW.payment_intent_id AND organisation_id = NEW.organisation_id;
            IF payment_status IN ('cancelled', 'expired', 'failed') THEN
              RAISE EXCEPTION 'capture % cannot be created for a % payment', NEW.id, payment_status
                USING ERRCODE = '23514';
            END IF;
          END IF;
          RETURN NULL;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION relaypay_validate_capture_command() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          authorization_amount bigint;
          authorization_status text;
        BEGIN
          SELECT amount, status INTO authorization_amount, authorization_status
          FROM authorizations
          WHERE id = NEW.authorization_id AND organisation_id = NEW.organisation_id;
          IF NEW.amount > authorization_amount THEN
            RAISE EXCEPTION 'capture % exceeds the authorized amount', NEW.id
              USING ERRCODE = '23514';
          END IF;
          IF authorization_status <> 'SUCCEEDED' THEN
            RAISE EXCEPTION 'capture % requires a succeeded authorization', NEW.id
              USING ERRCODE = '23514';
          END IF;
          RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER authorizations_derive_expiry ON authorizations; "
        "DROP FUNCTION relaypay_derive_authorization_expiry();"
    )
    op.drop_index("ix_authorizations_expiry", table_name="authorizations")
    op.drop_constraint("authorizations_expiry_check", "authorizations", type_="check")
    op.drop_constraint("authorizations_hold_seconds_check", "authorizations", type_="check")
    op.drop_column("authorizations", "expires_at")
    op.drop_column("authorizations", "hold_seconds")
