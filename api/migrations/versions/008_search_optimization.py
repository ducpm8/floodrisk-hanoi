"""FIR-19: Search index optimization — tsvector FTS + composite indexes

Revision ID: 008
Revises: 007
Create Date: 2026-04-26

Adds:
  search_vector  — generated tsvector on flood_events for diacritic-stripped FTS
  GIN index      — idx_flood_events_fts on search_vector
  composite idx  — idx_flood_events_district_date (district, event_date DESC)
  partial idx    — idx_flood_events_severity_severe WHERE severity='severe'
"""

from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Generated tsvector column — strips diacritics via 'simple' config
    # 'simple' lowercases without language-specific stemming (correct for Vietnamese)
    op.execute("""
        ALTER TABLE flood_events
        ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('simple',
                    coalesce(location_name, '') || ' ' ||
                    coalesce(ward, '') || ' ' ||
                    coalesce(district, '')
                )
            ) STORED
    """)

    op.execute("""
        CREATE INDEX idx_flood_events_fts
            ON flood_events USING GIN(search_vector)
    """)

    # Composite index for district+date queries (flood report card)
    op.execute("""
        CREATE INDEX idx_flood_events_district_date
            ON flood_events(district, event_date DESC)
    """)

    # Partial index — severe-only queries hit this without scanning all rows
    op.execute("""
        CREATE INDEX idx_flood_events_severity_severe
            ON flood_events(severity)
            WHERE severity = 'severe'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_flood_events_severity_severe")
    op.execute("DROP INDEX IF EXISTS idx_flood_events_district_date")
    op.execute("DROP INDEX IF EXISTS idx_flood_events_fts")
    op.execute("ALTER TABLE flood_events DROP COLUMN IF EXISTS search_vector")
