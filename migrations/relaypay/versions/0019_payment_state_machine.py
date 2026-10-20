"""Persisted payment state machine: status column, history, transition trigger.

Revision ID: 0019_payment_state_machine
Revises: 0018_immutability_backfill
Create Date: 2026-10-19

Order of operations (each step relies on the one before):

1. ``payment_intents.status`` is added nullable and ``status_version`` NOT NULL DEFAULT 0.
2. ``status`` is backfilled from the child rows (see ``BACKFILL_SQL``). No trigger guards
   ``payment_intents`` yet, and none of the evidence tables is read for write: the backfill
   reads ``authorizations``, ``captures`` and ``refunds`` and updates only ``payment_intents``.
3. ``status`` becomes NOT NULL with a CHECK on the nine enum values.
4. ``payment_status_history`` is created and receives one ``BACKFILL_V2`` row per payment
   (from NULL, version 0), then the 0001 ``relaypay_prevent_evidence_mutation()`` trigger
   makes it immutable. A payment created later gets its own version-0 row from the service.
5. ``relaypay_enforce_payment_status_transition()`` plus a BEFORE UPDATE OF status trigger
   reject (old, new) pairs outside the transition table, which is inlined as SQL VALUES from
   ``ALLOWED_TRANSITIONS``. The SQLSTATE is ``RP001`` (distinct from the 55000 used for
   immutability and 23514 for CHECKs). ``tests/unit/test_transitions.py`` fails if the table
   here differs from ``relaypay.payments.transitions.ALLOWED``.
6. An index serves the M16 payments list: (organisation_id, environment_id, status,
   created_at DESC, id).

Backfill rules, first match wins (a payment has at most one authorization and one capture):

* refunded            capture SUCCEEDED and SUCCEEDED refunds sum to the capture amount
* partially_refunded  capture SUCCEEDED and any SUCCEEDED refund
* captured            capture SUCCEEDED
* failed              authorization FAILED, or capture FAILED (a declined capture is terminal)
* authorised          authorization SUCCEEDED
* created             otherwise (including PROCESSING / REQUIRES_REVIEW children)

``payment_intents`` has no immutability trigger (verified in ``verify_m16_upgrade``); only its
two new columns change, which the proof script confirms by checksumming every row minus them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_payment_state_machine"
down_revision: str | None = "0018_immutability_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQLSTATE = "RP001"

STATUSES: tuple[str, ...] = (
    "created",
    "requires_action",
    "authorised",
    "captured",
    "partially_refunded",
    "refunded",
    "failed",
    "expired",
    "cancelled",
)

# Copied by hand from relaypay.payments.transitions.ALLOWED so this revision never changes
# when application code does; the drift test keeps the two equal.
ALLOWED_TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("created", "requires_action"),
    ("created", "authorised"),
    ("created", "failed"),
    ("created", "cancelled"),
    ("requires_action", "authorised"),
    ("requires_action", "failed"),
    ("requires_action", "cancelled"),
    ("requires_action", "expired"),
    ("authorised", "captured"),
    ("authorised", "cancelled"),
    ("authorised", "expired"),
    ("authorised", "failed"),
    ("captured", "partially_refunded"),
    ("captured", "refunded"),
    ("partially_refunded", "partially_refunded"),
    ("partially_refunded", "refunded"),
)

BACKFILL_SQL = """
UPDATE payment_intents AS p
SET status = d.derived
FROM (
  SELECT
    pi.id,
    CASE
      WHEN c.status = 'SUCCEEDED' AND r.total > 0 AND r.total = c.amount THEN 'refunded'
      WHEN c.status = 'SUCCEEDED' AND r.total > 0 THEN 'partially_refunded'
      WHEN c.status = 'SUCCEEDED' THEN 'captured'
      WHEN a.status = 'FAILED' OR c.status = 'FAILED' THEN 'failed'
      WHEN a.status = 'SUCCEEDED' THEN 'authorised'
      ELSE 'created'
    END AS derived
  FROM payment_intents pi
  LEFT JOIN authorizations a ON a.payment_intent_id = pi.id
  LEFT JOIN captures c ON c.payment_intent_id = pi.id
  LEFT JOIN LATERAL (
    SELECT COALESCE(SUM(rf.amount), 0) AS total
    FROM refunds rf
    WHERE rf.payment_intent_id = pi.id AND rf.status = 'SUCCEEDED'
  ) r ON true
) AS d
WHERE d.id = p.id
"""


def _in_list(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def transition_values_sql() -> str:
    return ", ".join(f"('{source}', '{target}')" for source, target in ALLOWED_TRANSITIONS)


def transition_function_sql() -> str:
    # Interpolates only module constants (never user input).
    return f"""
        CREATE FUNCTION relaypay_enforce_payment_status_transition() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1
            FROM (VALUES {transition_values_sql()}) AS allowed (from_status, to_status)
            WHERE allowed.from_status = OLD.status AND allowed.to_status = NEW.status
          ) THEN
            RAISE EXCEPTION 'illegal payment status transition: % -> %', OLD.status, NEW.status
              USING ERRCODE = '{SQLSTATE}';
          END IF;
          RETURN NEW;
        END;
        $$
        """  # noqa: S608


def upgrade() -> None:
    op.add_column("payment_intents", sa.Column("status", sa.String(length=24), nullable=True))
    op.add_column(
        "payment_intents",
        sa.Column("status_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.execute(BACKFILL_SQL)
    op.alter_column("payment_intents", "status", nullable=False)
    op.create_check_constraint(
        "payment_intents_status_check",
        "payment_intents",
        f"status IN ({_in_list(STATUSES)})",
    )

    op.create_table(
        "payment_status_history",
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("payment_intent_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("status_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status_version >= 0"),
        sa.CheckConstraint(
            f"to_status IN ({_in_list(STATUSES)})",
            name="payment_status_history_to_status_check",
        ),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_in_list(STATUSES)})",
            name="payment_status_history_from_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "payment_intent_id"],
            [
                "payment_intents.organisation_id",
                "payment_intents.environment_id",
                "payment_intents.id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_intent_id", "status_version"),
    )
    op.execute(
        """
        INSERT INTO payment_status_history
          (id, organisation_id, environment_id, payment_intent_id,
           from_status, to_status, reason, actor, status_version)
        SELECT gen_random_uuid(), organisation_id, environment_id, id,
               NULL, status, 'BACKFILL_V2', 'MIGRATION', 0
        FROM payment_intents
        """
    )
    op.execute(
        """
        CREATE TRIGGER payment_status_history_immutable
          BEFORE UPDATE OR DELETE ON payment_status_history
          FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_evidence_mutation()
        """
    )

    op.execute(transition_function_sql())
    op.execute(
        """
        CREATE TRIGGER payment_intents_status_transition
          BEFORE UPDATE OF status ON payment_intents
          FOR EACH ROW EXECUTE FUNCTION relaypay_enforce_payment_status_transition()
        """
    )

    op.create_index(
        "ix_payment_intents_scope_status_created_id",
        "payment_intents",
        ["organisation_id", "environment_id", "status", sa.text("created_at DESC"), "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_payment_intents_scope_status_created_id", table_name="payment_intents")
    op.execute("DROP TRIGGER payment_intents_status_transition ON payment_intents")
    op.execute("DROP FUNCTION relaypay_enforce_payment_status_transition()")
    op.drop_table("payment_status_history")
    op.drop_constraint("payment_intents_status_check", "payment_intents", type_="check")
    op.drop_column("payment_intents", "status_version")
    op.drop_column("payment_intents", "status")
