"""Add location_name to flood_events upsert key (FIR-84)

Problem: the old key (source_url, event_date, COALESCE(ward, '')) silently
drops the second event when a single FB post mentions two different streets
on the same day and Haiku extracts both as separate events with ward=NULL.
Both share the same source_url + event_date + '' → only the first inserts.

Fix: include COALESCE(location_name, '') in the key so that two null-ward
events from the same post on the same day survive as long as their
location_name differs.

Revision ID: 019
Revises: 018
Create Date: 2026-05-18
"""

from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uidx_flood_events_upsert")
    op.execute("""
        CREATE UNIQUE INDEX uidx_flood_events_upsert
            ON flood_events (
                source_url,
                event_date,
                COALESCE(ward, ''),
                COALESCE(location_name, '')
            )
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uidx_flood_events_upsert")
    op.execute("""
        CREATE UNIQUE INDEX uidx_flood_events_upsert
            ON flood_events (source_url, event_date, COALESCE(ward, ''))
    """)
