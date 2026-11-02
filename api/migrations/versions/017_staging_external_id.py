"""raw_flood_staging: add external_id for cross-run dedup (FIR-45)

Stores Facebook post IDs (and future: YouTube video IDs, etc.) so the
facebook-crawl endpoint can skip already-staged content across runs.

Revision ID: 017
Revises: 016
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "raw_flood_staging",
        sa.Column("external_id", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_raw_flood_staging_external_id",
        "raw_flood_staging",
        ["external_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_flood_staging_external_id", table_name="raw_flood_staging")
    op.drop_column("raw_flood_staging", "external_id")
