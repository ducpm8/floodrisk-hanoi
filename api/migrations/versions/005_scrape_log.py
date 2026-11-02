"""Scrape log table for tracking scraper runs

Revision ID: 005
Revises: 004
Create Date: 2026-04-05

Records every scraper run: source, timestamp, how many records were added,
and any errors encountered. Append-only — never queried in the hot path.
"""

from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE scrape_log (
            id              SERIAL PRIMARY KEY,
            source          VARCHAR(50) NOT NULL,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at     TIMESTAMPTZ,
            records_added   INT DEFAULT 0,
            errors          TEXT,
            status          VARCHAR(20) NOT NULL DEFAULT 'running'
                                CHECK (status IN ('running', 'success', 'partial', 'failed'))
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scrape_log")
