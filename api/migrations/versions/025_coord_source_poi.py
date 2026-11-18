"""Allow POI geocoding provenance on flood_events

Extends the Tier-1 provenance constraints (migration 024):

  coord_source  + geocoded_poi    named place (thôn, cầu, chung cư, sân vận động...)
  coord_flag    + generic_area    unnamed subdivision (tổ dân phố, khu dân cư số N)

Revision ID: 025
Revises: 024
Create Date: 2026-11-09
"""

from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_flood_events_coord_source", "flood_events", type_="check")
    op.create_check_constraint(
        "ck_flood_events_coord_source",
        "flood_events",
        "coord_source IS NULL OR coord_source IN "
        "('geocoded_street', 'geocoded_poi', 'ward_centroid')",
    )
    op.drop_constraint("ck_flood_events_coord_flag", "flood_events", type_="check")
    op.create_check_constraint(
        "ck_flood_events_coord_flag",
        "flood_events",
        "coord_flag IS NULL OR coord_flag IN "
        "('ward_only', 'geocode_failed', 'too_far', 'no_street', 'generic_area')",
    )


def downgrade() -> None:
    # Rows using the new values must be cleared before the old checks can return.
    op.execute(
        "UPDATE flood_events SET lat = NULL, lng = NULL, geom = NULL, "
        "coord_source = NULL, coord_confidence = NULL "
        "WHERE coord_source = 'geocoded_poi'"
    )
    op.execute("UPDATE flood_events SET coord_flag = NULL WHERE coord_flag = 'generic_area'")
    op.drop_constraint("ck_flood_events_coord_flag", "flood_events", type_="check")
    op.create_check_constraint(
        "ck_flood_events_coord_flag",
        "flood_events",
        "coord_flag IS NULL OR coord_flag IN "
        "('ward_only', 'geocode_failed', 'too_far', 'no_street')",
    )
    op.drop_constraint("ck_flood_events_coord_source", "flood_events", type_="check")
    op.create_check_constraint(
        "ck_flood_events_coord_source",
        "flood_events",
        "coord_source IS NULL OR coord_source IN ('geocoded_street', 'ward_centroid')",
    )
