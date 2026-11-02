"""Staging table for raw flood content + review queue for low-confidence LLM extractions

Revision ID: 006
Revises: 005
Create Date: 2026-04-25

raw_flood_staging: holds unprocessed social/news content before LLM extraction.
flood_events_review: low-confidence events awaiting human review.
"""

from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE raw_flood_staging (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            content        TEXT NOT NULL,
            source_type    VARCHAR(20) NOT NULL
                               CHECK (source_type IN ('social_media', 'news')),
            source_url     TEXT,
            post_timestamp TIMESTAMPTZ,
            processed      BOOLEAN NOT NULL DEFAULT FALSE,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX idx_raw_flood_staging_unprocessed
            ON raw_flood_staging (created_at)
            WHERE NOT processed
    """)

    op.execute("""
        CREATE TABLE flood_events_review (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            staging_id        UUID REFERENCES raw_flood_staging(id),
            location_name     VARCHAR(255),
            ward              VARCHAR(100),
            district          VARCHAR(100),
            lat               NUMERIC(9, 6),
            lng               NUMERIC(9, 6),
            event_date        DATE,
            depth_cm          INT,
            depth_description TEXT,
            duration_hours    INT,
            severity          VARCHAR(20) CHECK (severity IN ('minor', 'moderate', 'severe')),
            confidence        VARCHAR(10) NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
            source_type       VARCHAR(20),
            source_url        TEXT,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS flood_events_review")
    op.execute("DROP TABLE IF EXISTS raw_flood_staging")
