"""Add coordinate provenance for offline street geocoding

Adds three nullable columns to flood_events (Tier 1 backfill):

  coord_source      geocoded_street | ward_centroid
  coord_confidence  0..1 (0.90 <=1 km, 0.75 <=2.5 km, 0.60 fallback/xã up to 5 km)
  coord_flag        ward_only | geocode_failed | too_far | no_street

`resolution_level` is deliberately NOT reused: it holds 'dataflood_import' on
10,533 rows and the import rollback notes depend on that value.

Revision ID: 024
Revises: 023
Create Date: 2026-11-09
"""

import sqlalchemy as sa
from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flood_events", sa.Column("coord_source", sa.Text(), nullable=True))
    op.add_column(
        "flood_events",
        sa.Column("coord_confidence", sa.Numeric(3, 2), nullable=True),
    )
    op.add_column("flood_events", sa.Column("coord_flag", sa.Text(), nullable=True))

    op.create_check_constraint(
        "ck_flood_events_coord_source",
        "flood_events",
        "coord_source IS NULL OR coord_source IN ('geocoded_street', 'ward_centroid')",
    )
    op.create_check_constraint(
        "ck_flood_events_coord_flag",
        "flood_events",
        "coord_flag IS NULL OR coord_flag IN "
        "('ward_only', 'geocode_failed', 'too_far', 'no_street')",
    )
    op.create_check_constraint(
        "ck_flood_events_coord_confidence",
        "flood_events",
        "coord_confidence IS NULL "
        "OR (coord_confidence >= 0 AND coord_confidence <= 1)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_flood_events_coord_confidence", "flood_events", type_="check"
    )
    op.drop_constraint("ck_flood_events_coord_flag", "flood_events", type_="check")
    op.drop_constraint("ck_flood_events_coord_source", "flood_events", type_="check")
    op.drop_column("flood_events", "coord_flag")
    op.drop_column("flood_events", "coord_confidence")
    op.drop_column("flood_events", "coord_source")
