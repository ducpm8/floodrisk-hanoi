"""Ward centroids table for geocoding ward name → lat/lng

Revision ID: 002
Revises: 001
Create Date: 2026-04-05

ward_name is the normalized (no diacritics, lowercase) form used for lookup.
Seeded from OSM Overpass — 579 Hanoi wards. Run seeds/seed_wards.py after migration.
"""

from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE ward_centroids (
            ward_name    VARCHAR(100) PRIMARY KEY,
            ward_name_vi VARCHAR(100) NOT NULL,
            district     VARCHAR(100) NOT NULL,
            lat          NUMERIC(9, 6) NOT NULL,
            lng          NUMERIC(9, 6) NOT NULL,
            geom         GEOGRAPHY(POINT, 4326) NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ward_centroids")
