"""Indexes: GIST spatial + composite query indexes

Revision ID: 003
Revises: 002
Create Date: 2026-04-05

Indexes created:
  idx_flood_events_geom       GIST on flood_events.geom (ST_DWithin 500m query)
  idx_flood_events_ward       (ward, district) for ward-filtered lookups
  idx_flood_events_ward_date  (ward, event_date) for time-filtered report queries
  idx_price_records_ward      (ward, district, recorded_month) for price trend queries
  idx_ward_centroids_geom     GIST on ward_centroids.geom
"""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Spatial indexes (GIST) — required for ST_DWithin performance
    op.execute(
        "CREATE INDEX idx_flood_events_geom ON flood_events USING GIST(geom)"
    )
    op.execute(
        "CREATE INDEX idx_ward_centroids_geom ON ward_centroids USING GIST(geom)"
    )

    # Composite indexes for report API queries
    op.execute(
        "CREATE INDEX idx_flood_events_ward ON flood_events(ward, district)"
    )
    op.execute(
        "CREATE INDEX idx_flood_events_ward_date ON flood_events(ward, event_date)"
    )
    op.execute(
        "CREATE INDEX idx_price_records_ward ON price_records(ward, district, recorded_month)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_price_records_ward")
    op.execute("DROP INDEX IF EXISTS idx_flood_events_ward_date")
    op.execute("DROP INDEX IF EXISTS idx_flood_events_ward")
    op.execute("DROP INDEX IF EXISTS idx_ward_centroids_geom")
    op.execute("DROP INDEX IF EXISTS idx_flood_events_geom")
