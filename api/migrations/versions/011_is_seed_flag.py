"""Add is_seed flag to flood_events and price_records (FIR-36 cleanup prep)

Marks all currently-existing rows as seed data so they can be bulk-deleted
before go-live without touching real data entered afterwards.

Revision ID: 011
Revises: 010
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flood_events", sa.Column("is_seed", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("price_records", sa.Column("is_seed", sa.Boolean(), nullable=False, server_default="false"))

    # Mark every existing row as seed — no real data in DB yet
    op.execute("UPDATE flood_events SET is_seed = TRUE")
    op.execute("UPDATE price_records SET is_seed = TRUE")


def downgrade() -> None:
    op.drop_column("flood_events", "is_seed")
    op.drop_column("price_records", "is_seed")
