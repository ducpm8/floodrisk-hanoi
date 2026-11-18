"""Make ward_centroids.district nullable (FIR-84)

The API now returns `district=None` for wards whose OSM tags carry no
district, instead of leaking the literal "Unknown" placeholder. Migration 002
declared the column NOT NULL, so seed_wards.py currently skips those wards.
This migration drops the constraint and clears any legacy "Unknown" values
already written to the table.

Revision ID: 021
Revises: 020
Create Date: 2026-11-03
"""

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ward_centroids ALTER COLUMN district DROP NOT NULL"
    )
    op.execute(
        "UPDATE ward_centroids SET district = NULL WHERE district = 'Unknown'"
    )


def downgrade() -> None:
    # Backfill NULLs before restoring NOT NULL, otherwise the ALTER fails.
    op.execute(
        "UPDATE ward_centroids SET district = 'Unknown' WHERE district IS NULL"
    )
    op.execute(
        "ALTER TABLE ward_centroids ALTER COLUMN district SET NOT NULL"
    )
