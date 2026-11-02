"""Add lat/lng to location_aliases for per-alias geocoding

Allows landmark/apartment aliases to store their own coordinates
instead of falling back to the ward centroid. Nullable — NULL means
use ward centroid (existing behaviour unchanged).

Revision ID: 020
Revises: 019
Create Date: 2026-06-14
"""

from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("location_aliases", sa.Column("lat", sa.Numeric(9, 6), nullable=True))
    op.add_column("location_aliases", sa.Column("lng", sa.Numeric(9, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("location_aliases", "lng")
    op.drop_column("location_aliases", "lat")
