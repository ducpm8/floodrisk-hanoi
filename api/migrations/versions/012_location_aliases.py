"""Add location_aliases table for dynamic location resolution (FIR-37 AC2).

Stores alias → ward mappings discovered at crawl time: district names,
landmarks, apartment names, street slang. Ward_centroids remain the
canonical geographic anchor; this table grows with each crawl run.

Revision ID: 012
Revises: 011
Create Date: 2026-04-27
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE location_aliases (
            alias_norm   TEXT PRIMARY KEY,
            alias_vi     TEXT,
            ward_name    TEXT NOT NULL REFERENCES ward_centroids(ward_name),
            alias_type   VARCHAR(20) NOT NULL
                             CHECK (alias_type IN
                                 ('district','street','landmark','apartment','slang','other')),
            confidence   FLOAT NOT NULL DEFAULT 1.0
                             CHECK (confidence >= 0 AND confidence <= 1),
            source       VARCHAR(20) NOT NULL
                             CHECK (source IN ('static','llm','manual','pending')),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX idx_location_aliases_ward ON location_aliases(ward_name)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS location_aliases")
