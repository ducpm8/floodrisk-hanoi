"""Add flood_events.depth_basis

Records where depth_cm came from so measured values are not confused with
values extracted from text or estimated from a body/vehicle reference.

  measured   - number given by the source
  extracted  - number parsed from the description text
  estimated  - mapped from a body/vehicle reference (e.g. "ngang gối" -> 50)

NULL when depth_cm is NULL. Existing rows with a depth are backfilled as
'measured'; the DataFlood import then re-tags its extracted/estimated rows.

Revision ID: 023
Revises: 022
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("flood_events", sa.Column("depth_basis", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_flood_events_depth_basis",
        "flood_events",
        "depth_basis IS NULL OR depth_basis IN ('measured', 'extracted', 'estimated')",
    )
    op.execute("UPDATE flood_events SET depth_basis = 'measured' WHERE depth_cm IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint("ck_flood_events_depth_basis", "flood_events", type_="check")
    op.drop_column("flood_events", "depth_basis")
