"""Add index on raw_flood_staging.source_url

The YouTube/manual ingest duplicate check runs
`SELECT id FROM raw_flood_staging WHERE source_url = :url LIMIT 1`,
which is a full table scan without an index. Fine at current volume,
but staging grows with every crawl and manual submission.

Revision ID: 022
Revises: 021
Create Date: 2026-11-03
"""

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_raw_flood_staging_source_url "
        "ON raw_flood_staging(source_url)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_raw_flood_staging_source_url")
