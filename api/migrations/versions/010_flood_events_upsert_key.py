"""Add unique constraint to flood_events for idempotent upsert (FIR-35)

Upsert key: (source_url, event_date, ward) — prevents duplicate seed runs
from creating duplicate rows and enables ON CONFLICT DO NOTHING.

Revision ID: 010
Revises: 009
Create Date: 2026-04-27
"""

from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE flood_events
            ADD CONSTRAINT uidx_flood_events_upsert_key
            UNIQUE (source_url, event_date, ward)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE flood_events
            DROP CONSTRAINT IF EXISTS uidx_flood_events_upsert_key
    """)
