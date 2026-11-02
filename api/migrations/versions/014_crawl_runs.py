"""crawl_runs: add source_url + stats columns + crawl_run_id FK on flood_events (FIR-35)

crawl_runs table already exists from migration 009 with source_name, status
CHECK ('running','success','partial','failed'), records_fetched, records_new.
This migration extends it with source_url and stats JSONB, and links
flood_events back to the run that produced each event.

Revision ID: 014
Revises: 013
Create Date: 2026-05-01
"""

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE crawl_runs
            ADD COLUMN IF NOT EXISTS source_url TEXT,
            ADD COLUMN IF NOT EXISTS stats JSONB
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_crawl_runs_source
            ON crawl_runs (source_name)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_crawl_runs_started
            ON crawl_runs (started_at DESC)
    """)
    op.execute("""
        ALTER TABLE flood_events
            ADD COLUMN IF NOT EXISTS crawl_run_id UUID REFERENCES crawl_runs(id) ON DELETE SET NULL
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE flood_events DROP COLUMN IF EXISTS crawl_run_id")
    op.execute("ALTER TABLE crawl_runs DROP COLUMN IF EXISTS stats")
    op.execute("ALTER TABLE crawl_runs DROP COLUMN IF EXISTS source_url")
