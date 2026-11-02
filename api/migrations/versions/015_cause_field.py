"""flood_events.cause — weather event that triggered the flood (FIR-40)

Free-text field extracted by Haiku from news articles.
Examples: "hoàn lưu bão số 10 Bualoi", "mưa lớn ngày 30.9", "mưa rất to".
NULL for governance source (thoatnuochanoi.com.vn) which bypasses Haiku.

Revision ID: 015
Revises: 014
Create Date: 2026-05-01
"""

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE flood_events ADD COLUMN IF NOT EXISTS cause TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE flood_events DROP COLUMN IF EXISTS cause")
