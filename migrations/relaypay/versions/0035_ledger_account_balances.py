"""Stored running totals for merchant ledger accounts, kept by a posting trigger (ADR-014).

Revision ID: 0035_ledger_account_balances
Revises: 0034_pending_action
Create Date: 2026-11-20

A merchant balance used to be ``SUM`` over the account's postings on every read: 320 ms for an
account holding most of one million postings (docs/evidence/v2.0.0-m19-plans.md).
``ledger_account_balances`` holds, per merchant-owned ledger account, the credit total, the debit
total and the posting count. The ledger stays the source of truth and the row is a cache of it:

* ``ledger_account_balances_open`` (AFTER INSERT on ``ledger_accounts``) creates a zero row for
  every merchant-owned account in the same transaction as the account.
* ``ledger_account_balances_apply`` (AFTER INSERT on ``postings``, not deferred) adds each posting
  to its account's row in the same statement as the posting, so the two commit or roll back
  together. A posting to a merchant-owned account with no row is refused.
* ``ledger_account_balances_guard`` refuses any INSERT, UPDATE or DELETE that does not come from
  those triggers (``pg_trigger_depth() < 2``), whoever runs it. The app role also loses INSERT,
  UPDATE, DELETE and TRUNCATE on the table; the two maintenance functions are SECURITY DEFINER
  with ``search_path`` pinned to this schema.
* ``relaypay_ledger_balance_drift()`` lists every account whose stored row disagrees with its
  postings (or is missing, or should not exist). It must return no rows; the money invariant
  STORED_BALANCE_DRIFT checks the same property on a snapshot.

Platform accounts (``merchant_account_id IS NULL``: provider clearing, fee revenue, GST) get no
row. Every capture in an environment posts to them, so a row lock there would queue all captures
behind one another, and capture and refund lock them in opposite orders. Nothing reads their
balance on a money path; ``recompute_balance`` still sums them.

The backfill is one ``INSERT ... SELECT`` over the existing postings with inserts to
``postings`` and ``ledger_accounts`` blocked for the rest of the migration, then an independent
check: ``relaypay_ledger_balance_drift()`` must be empty and the grand totals must equal the
merchant postings' totals, or the migration raises and nothing is committed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_ledger_account_balances"
down_revision: str | None = "0034_pending_action"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRIFT_FUNCTION = """
CREATE FUNCTION relaypay_ledger_balance_drift()
RETURNS TABLE (
  drifted_account_id uuid,
  stored_credit_total bigint,
  stored_debit_total bigint,
  stored_posting_count bigint,
  ledger_credit_total bigint,
  ledger_debit_total bigint,
  ledger_posting_count bigint
)
LANGUAGE sql STABLE AS $$
  WITH ledger AS (
    SELECT p.account_id,
           COALESCE(sum(p.amount) FILTER (WHERE p.side = 'CREDIT'), 0)::bigint AS credit_total,
           COALESCE(sum(p.amount) FILTER (WHERE p.side = 'DEBIT'), 0)::bigint AS debit_total,
           count(*) AS posting_count
      FROM postings p
     GROUP BY p.account_id
  )
  SELECT a.id, b.credit_total, b.debit_total, b.posting_count,
         COALESCE(l.credit_total, 0), COALESCE(l.debit_total, 0), COALESCE(l.posting_count, 0)
    FROM ledger_accounts a
    LEFT JOIN ledger_account_balances b ON b.account_id = a.id
    LEFT JOIN ledger l ON l.account_id = a.id
   WHERE (a.merchant_account_id IS NOT NULL OR b.account_id IS NOT NULL)
     AND (b.account_id IS NULL
          OR a.merchant_account_id IS NULL
          OR b.credit_total <> COALESCE(l.credit_total, 0)
          OR b.debit_total <> COALESCE(l.debit_total, 0)
          OR b.posting_count <> COALESCE(l.posting_count, 0))
$$;
"""


# SECURITY DEFINER lets the app role's posting insert update a table it cannot write;
# SEARCH_PATH_PINNED becomes ``SET search_path = <migrated schema>, pg_temp`` so a caller's
# search_path cannot redirect the definer's writes.
MAINTENANCE_FUNCTIONS = """
CREATE FUNCTION relaypay_open_ledger_account_balance() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SEARCH_PATH_PINNED AS $$
BEGIN
  INSERT INTO ledger_account_balances (account_id, organisation_id, environment_id)
  VALUES (NEW.id, NEW.organisation_id, NEW.environment_id);
  RETURN NULL;
END;
$$;

CREATE FUNCTION relaypay_apply_posting_to_balance() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SEARCH_PATH_PINNED AS $$
BEGIN
  -- The row lock taken here is held to commit: concurrent postings to one merchant account
  -- serialise from this statement on. Under READ COMMITTED a waiter re-reads the committed row,
  -- so no increment is lost.
  UPDATE ledger_account_balances
     SET credit_total = credit_total + CASE WHEN NEW.side = 'CREDIT' THEN NEW.amount ELSE 0 END,
         debit_total = debit_total + CASE WHEN NEW.side = 'DEBIT' THEN NEW.amount ELSE 0 END,
         posting_count = posting_count + 1
   WHERE account_id = NEW.account_id;
  IF NOT FOUND AND EXISTS (
    SELECT 1 FROM ledger_accounts
     WHERE id = NEW.account_id AND merchant_account_id IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'merchant ledger account % has no stored balance row', NEW.account_id
      USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END;
$$;

CREATE FUNCTION relaypay_guard_ledger_account_balance() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Depth 1 is a statement run directly against this table; the maintenance triggers run at
  -- depth 2 or more. Only they may write it.
  IF pg_trigger_depth() < 2 THEN
    RAISE EXCEPTION 'ledger_account_balances rows are maintained only by the posting trigger'
      USING ERRCODE = '55000';
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;
"""


def _maintenance_functions(schema: str) -> str:
    quoted = '"' + schema.replace('"', '""') + '"'
    return MAINTENANCE_FUNCTIONS.replace(
        "SEARCH_PATH_PINNED", f"SET search_path = {quoted}, pg_temp"
    )


TRIGGERS = """
CREATE TRIGGER ledger_account_balances_open
  AFTER INSERT ON ledger_accounts
  FOR EACH ROW WHEN (NEW.merchant_account_id IS NOT NULL)
  EXECUTE FUNCTION relaypay_open_ledger_account_balance();
CREATE TRIGGER ledger_account_balances_apply
  AFTER INSERT ON postings
  FOR EACH ROW EXECUTE FUNCTION relaypay_apply_posting_to_balance();
"""

BACKFILL = """
INSERT INTO ledger_account_balances
  (account_id, organisation_id, environment_id, credit_total, debit_total, posting_count)
SELECT a.id, a.organisation_id, a.environment_id,
       COALESCE(sum(p.amount) FILTER (WHERE p.side = 'CREDIT'), 0),
       COALESCE(sum(p.amount) FILTER (WHERE p.side = 'DEBIT'), 0),
       count(p.id)
  FROM ledger_accounts a
  LEFT JOIN postings p ON p.account_id = a.id
 WHERE a.merchant_account_id IS NOT NULL
 GROUP BY a.id, a.organisation_id, a.environment_id
"""

VERIFY_BACKFILL = """
DO $$
DECLARE
  drifted bigint;
  stored record;
  ledger record;
BEGIN
  SELECT count(*) INTO drifted FROM relaypay_ledger_balance_drift();
  IF drifted <> 0 THEN
    RAISE EXCEPTION 'ledger_account_balances backfill disagrees with postings for % accounts',
      drifted USING ERRCODE = '23514';
  END IF;
  SELECT COALESCE(sum(credit_total), 0) AS credit, COALESCE(sum(debit_total), 0) AS debit,
         COALESCE(sum(posting_count), 0) AS n, count(*) AS accounts
    INTO stored FROM ledger_account_balances;
  SELECT COALESCE(sum(p.amount) FILTER (WHERE p.side = 'CREDIT'), 0) AS credit,
         COALESCE(sum(p.amount) FILTER (WHERE p.side = 'DEBIT'), 0) AS debit,
         count(p.id) AS n,
         (SELECT count(*) FROM ledger_accounts WHERE merchant_account_id IS NOT NULL) AS accounts
    INTO ledger
    FROM postings p JOIN ledger_accounts a ON a.id = p.account_id
   WHERE a.merchant_account_id IS NOT NULL;
  IF (stored.credit, stored.debit, stored.n, stored.accounts)
     IS DISTINCT FROM (ledger.credit, ledger.debit, ledger.n, ledger.accounts) THEN
    RAISE EXCEPTION 'ledger_account_balances backfill totals % differ from postings %',
      stored, ledger USING ERRCODE = '23514';
  END IF;
END;
$$;
"""

GUARD_TRIGGER = """
CREATE TRIGGER ledger_account_balances_guard
  BEFORE INSERT OR UPDATE OR DELETE ON ledger_account_balances
  FOR EACH ROW EXECUTE FUNCTION relaypay_guard_ledger_account_balance();
"""

# Default privileges grant the app role DML on every new table; take back everything but SELECT
# from every role other than the owner, whatever it is called.
REVOKE_WRITES = """
DO $$
DECLARE
  grantee_name text;
BEGIN
  REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ledger_account_balances FROM PUBLIC;
  FOR grantee_name IN
    SELECT DISTINCT acl.grantee::regrole::text
      FROM pg_class c, aclexplode(c.relacl) acl
     WHERE c.oid = 'ledger_account_balances'::regclass
       AND acl.grantee <> 0
       AND acl.grantee <> c.relowner
  LOOP
    EXECUTE format(
      'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ledger_account_balances FROM %s', grantee_name
    );
  END LOOP;
END;
$$;
"""


def upgrade() -> None:
    schema = op.get_bind().scalar(sa.text("SELECT current_schema()"))
    op.create_table(
        "ledger_account_balances",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("organisation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("credit_total", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("debit_total", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("posting_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("credit_total >= 0"),
        sa.CheckConstraint("debit_total >= 0"),
        sa.CheckConstraint("posting_count >= 0"),
        sa.ForeignKeyConstraint(
            ["organisation_id", "environment_id", "account_id"],
            [
                "ledger_accounts.organisation_id",
                "ledger_accounts.environment_id",
                "ledger_accounts.id",
            ],
        ),
        sa.PrimaryKeyConstraint("account_id"),
    )
    # Block new postings and accounts until this transaction commits, so the backfill and the
    # triggers see the same ledger and no posting falls between them.
    op.execute("LOCK TABLE ledger_accounts, postings IN SHARE ROW EXCLUSIVE MODE")
    op.execute(DRIFT_FUNCTION)
    op.execute(_maintenance_functions(str(schema)))
    op.execute(TRIGGERS)
    op.execute(BACKFILL)
    op.execute(VERIFY_BACKFILL)
    op.execute(GUARD_TRIGGER)
    op.execute(REVOKE_WRITES)


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER ledger_account_balances_apply ON postings;
        DROP TRIGGER ledger_account_balances_open ON ledger_accounts;
        DROP TABLE ledger_account_balances;
        DROP FUNCTION relaypay_guard_ledger_account_balance();
        DROP FUNCTION relaypay_apply_posting_to_balance();
        DROP FUNCTION relaypay_open_ledger_account_balance();
        DROP FUNCTION relaypay_ledger_balance_drift();
        """
    )
