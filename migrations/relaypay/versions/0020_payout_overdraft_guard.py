"""Database backstop: payout reservations never exceed a merchant's available payable.

Revision ID: 0020_payout_overdraft_guard
Revises: 0019_payment_state_machine
Create Date: 2026-10-20

Every writer that moves AVAILABLE_PAYABLE or payout reservations (create_payout, retry,
run_settlement, refund finalization) takes the merchant-account row lock and checks balances in
application code. Bug F1 showed what happens when one of those checks is wrong: a refund drained
funds already reserved for a payout and AVAILABLE ended at -100000 paise. This constraint
trigger makes the invariant a database fact, checked at commit for the affected merchant:

    sum(payout_reservation_history.amount_delta)
        <= sum(AVAILABLE_PAYABLE_LIABILITY credits - debits)

Because reservations are never negative, this also forbids a negative AVAILABLE balance. It
fires after a reservation row is inserted and after a DEBIT posting to an AVAILABLE_PAYABLE
account. Credits and reservation releases can only help. It is DEFERRABLE INITIALLY DEFERRED,
so a transaction may pass through intermediate states, for example a payout consuming its
reservation and debiting AVAILABLE in either order. The constraint-guard payout strategy (M19)
relies on its SQLSTATE 23514 instead of an application lock.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_payout_overdraft_guard"
down_revision: str | None = "0019_payment_state_machine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION relaypay_validate_payout_coverage() RETURNS trigger
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

        CREATE CONSTRAINT TRIGGER payout_reservations_covered_at_commit
          AFTER INSERT ON payout_reservation_history DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION relaypay_validate_payout_coverage();
        CREATE CONSTRAINT TRIGGER available_debits_covered_at_commit
          AFTER INSERT ON postings DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION relaypay_validate_payout_coverage();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER available_debits_covered_at_commit ON postings;
        DROP TRIGGER payout_reservations_covered_at_commit ON payout_reservation_history;
        DROP FUNCTION relaypay_validate_payout_coverage();
        """
    )
