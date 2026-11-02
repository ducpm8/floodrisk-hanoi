"""crawl_source_config and crawl_runs tables for FIR-16

Revision ID: 009
Revises: 008
Create Date: 2026-04-26
"""

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE crawl_source_config (
            id              SERIAL PRIMARY KEY,
            source_name     VARCHAR(50) UNIQUE NOT NULL,
            source_type     VARCHAR(20) NOT NULL
                                CHECK (source_type IN ('governance', 'news', 'social')),
            source_url      VARCHAR(500) NOT NULL,
            crawler_class   VARCHAR(100) NOT NULL,
            is_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
            last_crawled_at TIMESTAMPTZ,
            notes           TEXT,
            updated_by      VARCHAR(100),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE crawl_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_name     VARCHAR(50) NOT NULL,
            started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at     TIMESTAMPTZ,
            records_fetched INT NOT NULL DEFAULT 0,
            records_new     INT NOT NULL DEFAULT 0,
            status          VARCHAR(20) NOT NULL
                                CHECK (status IN ('running', 'success', 'partial', 'failed')),
            error_message   TEXT
        )
    """)

    op.execute("""
        CREATE INDEX idx_crawl_runs_source_started
            ON crawl_runs (source_name, started_at DESC)
    """)

    # Seed the 7 initial sources
    op.execute("""
        INSERT INTO crawl_source_config
            (source_name, source_type, source_url, crawler_class, is_enabled)
        VALUES
            ('thoatnuochanoi', 'governance', 'https://thoatnuochanoi.com.vn/tin-tuc', 'ThoatNuocHanoiCrawler', TRUE),
            ('vnexpress',      'news',       'https://vnexpress.net/chu-de/ngap-o-ha-noi-4591', 'VnExpressCrawler', TRUE),
            ('tuoitre',        'news',       'https://tuoitre.vn/ngap-lut-ha-noi.html',          'TuoiTreCrawler',  TRUE),
            ('dantri',         'news',       'https://dantri.com.vn/ha-noi-ngap.tag',             'DanTriCrawler',   FALSE),
            ('facebook_offb',  'social',     'https://facebook.com/groups/offb',                  'FacebookOFFBCrawler', TRUE),
            ('facebook_local', 'social',     'https://facebook.com/groups/hanoinhadan',           'FacebookLocalGroupCrawler', TRUE),
            ('instagram_hashtag', 'social',  'https://instagram.com/explore/tags/ngapHaNoi',      'InstagramHashtagCrawler', TRUE)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS crawl_runs")
    op.execute("DROP TABLE IF EXISTS crawl_source_config")
