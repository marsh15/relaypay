"""Allow a single capture of up to the authorized amount (partial capture).

Revision ID: 0022_partial_capture
Revises: 0021_postings_balance_index
Create Date: 2026-10-20

Until now ``relaypay_validate_capture_command`` (from 0002) required ``capture.amount`` to equal
the payment amount. v2.0 (decision D6) keeps one capture per payment but lets it be any positive
amount up to the authorization amount; the remainder is released, and nothing is posted for it,
because authorization holds never reach the ledger. The trigger now checks the capture against
its authorization row, which is the amount the provider actually holds. Refund caps already use
``capture.amount``, so they follow automatically. Existing captures all equal their
authorization amount, so no row changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_partial_capture"
down_revision: str | None = "0021_postings_balance_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION relaypay_validate_capture_command() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          payment_amount bigint;
          authorization_status text;
        BEGIN
          SELECT amount INTO payment_amount FROM payment_intents
            WHERE id = NEW.payment_intent_id AND organisation_id = NEW.organisation_id;
          SELECT status INTO authorization_status FROM authorizations
            WHERE id = NEW.authorization_id AND organisation_id = NEW.organisation_id;
          IF NEW.amount <> payment_amount THEN
            RAISE EXCEPTION 'capture % must equal the full payment amount', NEW.id
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
