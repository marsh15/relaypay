"""Serialise the payout coverage check per merchant so the constraint guard is safe alone.

Revision ID: 0030_coverage_check_lock
Revises: 0029_balance_version
Create Date: 2026-11-04

The deferred coverage trigger (0020) is a backstop for writers that hold the merchant-account row
lock. The constraint-guard payout strategy holds no lock and relies on the trigger alone, and then
two concurrent commits each evaluated the sums before the other's reservation was visible: an 80-way
race reserved 42 payouts against funds for 40. The trigger now takes a transaction-scoped advisory
lock on the merchant before it sums, so concurrent commits check one after another and the later
one sees the earlier one's reservation. The trigger definitions are unchanged.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_coverage_check_lock"
down_revision: str | None = "0029_balance_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCKED_FUNCTION = """
CREATE OR REPLACE FUNCTION relaypay_validate_payout_coverage() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          target_merchant uuid;
          available_account uuid;
          available bigint;
          reserved bigint;
        BEGIN
          -- Every lookup carries organisation_id (and environment_id) so it uses the tenant-led
          -- indexes; without them this per-row check seq-scans ledger_accounts and postings.
          IF TG_TABLE_NAME = 'payout_reservation_history' THEN
            target_merchant := NEW.merchant_account_id;
            SELECT id INTO available_account
            FROM ledger_accounts
            WHERE organisation_id = NEW.organisation_id
              AND environment_id = NEW.environment_id
              AND merchant_account_id = target_merchant
              AND code = 'AVAILABLE_PAYABLE_LIABILITY';
          ELSE
            IF NEW.side <> 'DEBIT' THEN
              RETURN NULL;
            END IF;
            SELECT merchant_account_id, id INTO target_merchant, available_account
            FROM ledger_accounts
            WHERE id = NEW.account_id AND code = 'AVAILABLE_PAYABLE_LIABILITY';
            IF target_merchant IS NULL THEN
              RETURN NULL;
            END IF;
          END IF;

          -- Serialise the check per merchant: the lock is held to the end of this commit, and the
          -- sums below are read with a fresh snapshot, so a concurrent commit that already added
          -- its reservation is seen. Without it two commits can each pass on stale sums.
          PERFORM pg_advisory_xact_lock(hashtextextended(target_merchant::text, 0));

          SELECT COALESCE(sum(CASE WHEN side = 'CREDIT' THEN amount ELSE -amount END), 0)
          INTO available
          FROM postings
          WHERE organisation_id = NEW.organisation_id AND account_id = available_account;

          SELECT COALESCE(sum(amount_delta), 0) INTO reserved
          FROM payout_reservation_history
          WHERE merchant_account_id = target_merchant;

          IF reserved > available THEN
            RAISE EXCEPTION
              'merchant account % payout reservations (%) exceed available payable (%)',
              target_merchant, reserved, available
              USING ERRCODE = '23514';
          END IF;
          RETURN NULL;
        END;
        $$;
"""

ORIGINAL_FUNCTION = """
CREATE OR REPLACE FUNCTION relaypay_validate_payout_coverage() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          target_merchant uuid;
          available_account uuid;
          available bigint;
          reserved bigint;
        BEGIN
          -- Every lookup carries organisation_id (and environment_id) so it uses the tenant-led
          -- indexes; without them this per-row check seq-scans ledger_accounts and postings.
          IF TG_TABLE_NAME = 'payout_reservation_history' THEN
            target_merchant := NEW.merchant_account_id;
            SELECT id INTO available_account
            FROM ledger_accounts
            WHERE organisation_id = NEW.organisation_id
              AND environment_id = NEW.environment_id
              AND merchant_account_id = target_merchant
              AND code = 'AVAILABLE_PAYABLE_LIABILITY';
          ELSE
            IF NEW.side <> 'DEBIT' THEN
              RETURN NULL;
            END IF;
            SELECT merchant_account_id, id INTO target_merchant, available_account
            FROM ledger_accounts
            WHERE id = NEW.account_id AND code = 'AVAILABLE_PAYABLE_LIABILITY';
            IF target_merchant IS NULL THEN
              RETURN NULL;
            END IF;
          END IF;

          SELECT COALESCE(sum(CASE WHEN side = 'CREDIT' THEN amount ELSE -amount END), 0)
          INTO available
          FROM postings
          WHERE organisation_id = NEW.organisation_id AND account_id = available_account;

          SELECT COALESCE(sum(amount_delta), 0) INTO reserved
          FROM payout_reservation_history
          WHERE merchant_account_id = target_merchant;

          IF reserved > available THEN
            RAISE EXCEPTION
              'merchant account % payout reservations (%) exceed available payable (%)',
              target_merchant, reserved, available
              USING ERRCODE = '23514';
          END IF;
          RETURN NULL;
        END;
        $$;
"""


def upgrade() -> None:
    op.execute(LOCKED_FUNCTION)


def downgrade() -> None:
    op.execute(ORIGINAL_FUNCTION)
