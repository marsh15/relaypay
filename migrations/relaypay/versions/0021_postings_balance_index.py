"""Covering indexes for the two hot postings lookups: per-account and per-journal sums.

Revision ID: 0021_postings_balance_index
Revises: 0020_payout_overdraft_guard
Create Date: 2026-10-20

Every balance read (derive_balances' _account_balance, the refund source split, and so on) runs

    SELECT sum(CASE WHEN side = 'CREDIT' THEN amount ELSE -amount END)
    FROM postings WHERE account_id = :account

The only index on account_id, ix_postings_account_created, leads with organisation_id, so these
queries sequentially scanned the whole postings table. That is cheap on a fresh database and
linear in total ledger size afterwards. On a 27k-posting development database it was about
6 ms per read, called several times per payment operation; table statistics showed 1.1 million
seq scans of postings in one session. This index answers the sum as an index-only scan, because
postings are append-only so the visibility map stays current.

The same scan hid in the deferred journal-balance trigger from 0001, which runs
``... FROM postings WHERE journal_id = :journal`` at commit for every journal and every posting.
postings had no index on journal_id at all, so each ledger write scanned the whole table
(3.9 ms at 30k postings, about 30 ms per journal), making every write linear in ledger size.
ix_postings_journal covers it.

Measured on the development database: the account sum went from a seq scan (490 buffers,
3.5-6.2 ms) to an index-only scan (5 buffers, 0.39 ms, 0 heap fetches). A chaos slice of
3 x 1,000 operations went from 237 s to 110 s with the account index alone.

Plain CREATE INDEX (not CONCURRENTLY): Alembic runs migrations in a transaction, and the
sandbox has no online-migration requirement (docs/limitations.md).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_postings_balance_index"
down_revision: str | None = "0020_payout_overdraft_guard"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_postings_account_balance",
        "postings",
        ["account_id"],
        postgresql_include=["side", "amount"],
    )
    op.create_index(
        "ix_postings_journal",
        "postings",
        ["journal_id"],
        postgresql_include=["side", "amount", "currency"],
    )


def downgrade() -> None:
    op.drop_index("ix_postings_journal", table_name="postings")
    op.drop_index("ix_postings_account_balance", table_name="postings")
