"""raw_flood_staging.source_type: add 'youtube' (FIR-41)

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE raw_flood_staging
            DROP CONSTRAINT IF EXISTS raw_flood_staging_source_type_check
    """)
    op.execute("""
        ALTER TABLE raw_flood_staging
            ADD CONSTRAINT raw_flood_staging_source_type_check
                CHECK (source_type IN ('social_media', 'news', 'governance', 'manual', 'youtube'))
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE raw_flood_staging
            DROP CONSTRAINT IF EXISTS raw_flood_staging_source_type_check
    """)
    op.execute("""
        ALTER TABLE raw_flood_staging
            ADD CONSTRAINT raw_flood_staging_source_type_check
                CHECK (source_type IN ('social_media', 'news', 'governance', 'manual'))
    """)
