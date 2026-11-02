"""Initial schema: flood_events and price_records

Revision ID: 001
Revises:
Create Date: 2026-04-05
"""

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.execute("""
        CREATE TABLE flood_events (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            location_name  VARCHAR(255),
            ward           VARCHAR(100),
            district       VARCHAR(100),
            lat            NUMERIC(9, 6),
            lng            NUMERIC(9, 6),
            geom           GEOGRAPHY(POINT, 4326),
            event_date     DATE NOT NULL,
            severity       VARCHAR(20) CHECK (severity IN ('minor', 'moderate', 'severe')),
            depth_cm       INT,
            duration_hours INT,
            source         VARCHAR(20) CHECK (source IN ('official', 'news', 'community')),
            source_url     TEXT,
            created_at     TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE price_records (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ward             VARCHAR(100),
            district         VARCHAR(100),
            property_type    VARCHAR(20) CHECK (property_type IN ('apartment', 'house', 'land')),
            price_per_m2_vnd BIGINT,
            recorded_month   DATE,
            last_updated     TIMESTAMPTZ,
            source           VARCHAR(255),
            source_url       TEXT,
            data_caveat      TEXT DEFAULT 'Listing price, not confirmed sale price',
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS price_records")
    op.execute("DROP TABLE IF EXISTS flood_events")
