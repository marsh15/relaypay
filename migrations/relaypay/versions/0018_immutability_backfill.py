"""Backfill row-immutability triggers onto the tables created by migrations 0012 to 0017.

Revision ID: 0018_immutability_backfill
Revises: 0017_merchant_risk_review
Create Date: 2026-10-19

Migrations 0001 to 0011 protect evidence tables with BEFORE UPDATE OR DELETE triggers. The
tables added in 0012 to 0017 relied on application convention only. Each table was classified
by grepping ``packages/`` and ``apps/`` for every UPDATE, DELETE, upsert and attribute-assignment
path on its model.

    A  never mutated after INSERT: full-row trigger, ``relaypay_prevent_evidence_mutation()``
       from 0001 (SQLSTATE 55000 on any UPDATE or DELETE).
    B  mutated only in named columns: ``relaypay_prevent_frozen_column_mutation(<columns>)``
       (new here, same idea as the column-scoped ``inbound_payload_immutable`` in 0011). Only
       the listed columns may change; every other column is frozen and DELETE is rejected.
    C  mutable workflow state (leases, status machines, upserts, retention deletes): no trigger.

| Table                       | Migration | Class | Why / mutable columns                          |
|-----------------------------|-----------|-------|------------------------------------------------|
| request_logs                | 0012      | C     | retention prune DELETEs oldest rows            |
| usage_rollups               | 0012      | C     | hourly upsert increments counters              |
| pricing_versions            | 0013      | A     | insert only                                    |
| business_event_outbox       | 0013      | C     | lease_token, attempts, published_at (publisher)|
| consumed_business_events    | 0013      | A     | insert only (consumer dedupe)                  |
| evaluation_datasets         | 0013      | A     | insert only                                    |
| prompt_versions             | 0013      | A     | insert only                                    |
| workflow_definitions        | 0013      | B     | status (ACTIVE -> RETIRED)                     |
| evaluation_runs             | 0013      | B     | status, result, result_sha256, completed_at    |
| workflow_runs               | 0013      | C     | status, token/cost counters                    |
| workflow_artifacts          | 0013      | A     | insert only (versioned)                        |
| workflow_steps              | 0013      | C     | lease and retry columns                        |
| approval_requests           | 0013      | C     | status (PENDING -> APPROVED/INVALIDATED)       |
| model_invocations           | 0013      | A     | insert only                                    |
| tool_invocations            | 0013      | A     | insert only                                    |
| workflow_dead_letters       | 0013      | B     | replay_count, last_replayed_at                 |
| approval_decisions          | 0013      | A     | insert only                                    |
| dispute_cases               | 0014      | C     | status machine                                 |
| dispute_draft_versions      | 0014      | A     | insert only (versioned)                        |
| dispute_package_versions    | 0014      | B     | status (FROZEN/APPROVED/INVALIDATED/SUBMITTED) |
| dispute_submission_attempts | 0014      | C     | status, response_code, completed_at            |
| subscriptions               | 0015      | C     | status                                         |
| subscription_invoices       | 0015      | C     | status, paid_at                                |
| recurring_payment_attempts  | 0015      | A     | insert only                                    |
| recovery_policy_versions    | 0015      | B     | status (ACTIVE -> RETIRED)                     |
| recovery_cases              | 0015      | C     | status, counters, terminated_at                |
| scheduled_recovery_actions  | 0015      | C     | status, executed_at, response_code             |
| communication_records       | 0015      | B     | status, response_code, response_sha256         |
| recovery_opt_outs           | 0015      | A     | insert only                                    |
| settlement_policies         | 0016      | B     | status (ACTIVE -> RETIRED)                     |
| settlement_forecasts        | 0016      | A     | insert only (sequenced)                        |
| settlement_forecast_items   | 0016      | A     | insert only                                    |
| settlement_questions        | 0016      | C     | status, answer, answered_at                    |
| onboarding_snapshots        | 0017      | A     | insert only (content-addressed)                |
| risk_reviews                | 0017      | C     | status                                         |
| risk_review_versions        | 0017      | A     | insert only (versioned)                        |
| risk_findings               | 0017      | A     | insert only                                    |
| risk_escalations            | 0017      | C     | status, disposition columns                    |
| risk_annotations            | 0017      | A     | insert only                                    |

TRUNCATE does not fire row triggers, so ``scripts/reset_sandbox.py`` keeps working. Downgrade
drops exactly the triggers added here plus the new column-scoped function; the 0001 function
is left in place.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_immutability_backfill"
down_revision: str | None = "0017_merchant_risk_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Class A: never mutated after INSERT.
FULL_IMMUTABLE_TABLES: tuple[str, ...] = (
    "pricing_versions",
    "consumed_business_events",
    "evaluation_datasets",
    "prompt_versions",
    "workflow_artifacts",
    "model_invocations",
    "tool_invocations",
    "approval_decisions",
    "dispute_draft_versions",
    "recurring_payment_attempts",
    "recovery_opt_outs",
    "settlement_forecasts",
    "settlement_forecast_items",
    "onboarding_snapshots",
    "risk_review_versions",
    "risk_findings",
    "risk_annotations",
)

# Class B: table -> the only columns an UPDATE may change.
COLUMN_SCOPED_TABLES: dict[str, tuple[str, ...]] = {
    "workflow_definitions": ("status",),
    "evaluation_runs": ("status", "result", "result_sha256", "completed_at"),
    "workflow_dead_letters": ("replay_count", "last_replayed_at"),
    "dispute_package_versions": ("status",),
    "recovery_policy_versions": ("status",),
    "communication_records": ("status", "response_code", "response_sha256"),
    "settlement_policies": ("status",),
}

PROTECTED_TABLES: tuple[str, ...] = (*FULL_IMMUTABLE_TABLES, *COLUMN_SCOPED_TABLES)


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION relaypay_prevent_frozen_column_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION '% rows are immutable', TG_TABLE_NAME USING ERRCODE = '55000';
          END IF;
          IF (to_jsonb(NEW) - TG_ARGV) IS DISTINCT FROM (to_jsonb(OLD) - TG_ARGV) THEN
            RAISE EXCEPTION '% rows are immutable outside %', TG_TABLE_NAME, TG_ARGV
              USING ERRCODE = '55000';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    for table in FULL_IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable
              BEFORE UPDATE OR DELETE ON {table}
              FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_evidence_mutation()
            """
        )
    for table, columns in COLUMN_SCOPED_TABLES.items():
        arguments = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            f"""
            CREATE TRIGGER {table}_frozen_columns
              BEFORE UPDATE OR DELETE ON {table}
              FOR EACH ROW EXECUTE FUNCTION relaypay_prevent_frozen_column_mutation({arguments})
            """
        )


def downgrade() -> None:
    for table in reversed(FULL_IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER {table}_immutable ON {table}")
    for table in reversed(COLUMN_SCOPED_TABLES):
        op.execute(f"DROP TRIGGER {table}_frozen_columns ON {table}")
    op.execute("DROP FUNCTION relaypay_prevent_frozen_column_mutation()")
