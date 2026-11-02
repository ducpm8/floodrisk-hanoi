"""crawler_sessions table for persistent scraper cookie storage

Revision ID: 007
Revises: 006
Create Date: 2026-04-25
"""

from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE crawler_sessions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name     VARCHAR(50) NOT NULL UNIQUE,
            cookies_encrypted TEXT,
            last_valid_at   TIMESTAMPTZ,
            expires_at      TIMESTAMPTZ,
            status          VARCHAR(20) NOT NULL DEFAULT 'active'
                                CHECK (status IN ('active', 'expired', 'blocked')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX idx_crawler_sessions_source ON crawler_sessions (source_name)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS crawler_sessions")
