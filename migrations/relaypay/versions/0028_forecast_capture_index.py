"""Let a settlement forecast hold a capture with its own refund.

Revision ID: 0028_forecast_capture_index
Revises: 0027_webhook_evidence
Create Date: 2026-11-03

A REFUND forecast item carries the refunded capture's id, so the unique index on
``(forecast_id, capture_id)`` rejected every forecast that contained both a capture and a refund
of it. The index now applies to CAPTURE items only, which is what it was meant to guarantee: one
CAPTURE item per capture per forecast. Refund items stay unique through their own index. No row
changes.

The platform fee and GST are retained on refund (D7), so a refunded capture can pay out less than
it refunds. The expected settlement is then a negative shortfall, which the identity
``expected = captures - refunds - offset`` already allows; only the ``>= 0`` check rejected it, so
that check is dropped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_forecast_capture_index"
down_revision: str | None = "0027_webhook_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NON_NEGATIVE = "settlement_forecasts_expected_settlement_amount_check"
INDEX = "uq_settlement_forecast_items_capture"
TABLE = "settlement_forecast_items"


def upgrade() -> None:
    op.drop_constraint(NON_NEGATIVE, "settlement_forecasts", type_="check")
    op.drop_index(INDEX, table_name=TABLE)
    op.create_index(
        INDEX,
        TABLE,
        ["forecast_id", "capture_id"],
        unique=True,
        postgresql_where=sa.text("item_type = 'CAPTURE'"),
    )


def downgrade() -> None:
    op.create_check_constraint(
        NON_NEGATIVE, "settlement_forecasts", "expected_settlement_amount >= 0"
    )
    op.drop_index(INDEX, table_name=TABLE)
    op.create_index(
        INDEX,
        TABLE,
        ["forecast_id", "capture_id"],
        unique=True,
        postgresql_where=sa.text("capture_id IS NOT NULL"),
    )
