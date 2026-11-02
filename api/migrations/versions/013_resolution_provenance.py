"""Resolution provenance + admin manual ingest source type (FIR-39 + location resolution D1-D7)

Schema changes (additive — no existing data affected):
  flood_events:       add resolution_level, resolution_confidence columns
  flood_events:       replace UNIQUE(source_url, event_date, ward) with
                      UNIQUE(source_url, event_date, COALESCE(ward, ''))
                      to stop silent duplicate failures when ward IS NULL
  location_aliases:   add 'intersection' to alias_type CHECK
  raw_flood_staging:  add 'governance' and 'manual' to source_type CHECK

Revision ID: 013
Revises: 012
Create Date: 2026-04-30
"""

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- flood_events: resolution provenance columns -------------------------
    op.execute("""
        ALTER TABLE flood_events
            ADD COLUMN IF NOT EXISTS resolution_level      VARCHAR(20),
            ADD COLUMN IF NOT EXISTS resolution_confidence NUMERIC(3,2)
                CHECK (resolution_confidence IS NULL
                    OR (resolution_confidence >= 0 AND resolution_confidence <= 1))
    """)

    # --- flood_events: replace NULL-hostile upsert key ----------------------
    # Old constraint silently fails when ward IS NULL (NULL != NULL in UNIQUE).
    # New partial unique index uses COALESCE so NULL wards deduplicate correctly.
    op.execute("""
        ALTER TABLE flood_events
            DROP CONSTRAINT IF EXISTS uidx_flood_events_upsert_key
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uidx_flood_events_upsert
            ON flood_events (source_url, event_date, COALESCE(ward, ''))
    """)

    # --- location_aliases: add 'intersection' alias type --------------------
    op.execute("""
        ALTER TABLE location_aliases
            DROP CONSTRAINT IF EXISTS location_aliases_alias_type_check
    """)
    op.execute("""
        ALTER TABLE location_aliases
            ADD CONSTRAINT location_aliases_alias_type_check
                CHECK (alias_type IN
                    ('district','street','landmark','apartment','slang','intersection','other'))
    """)

    # --- raw_flood_staging: add 'governance' and 'manual' source types ------
    op.execute("""
        ALTER TABLE raw_flood_staging
            DROP CONSTRAINT IF EXISTS raw_flood_staging_source_type_check
    """)
    op.execute("""
        ALTER TABLE raw_flood_staging
            ADD CONSTRAINT raw_flood_staging_source_type_check
                CHECK (source_type IN ('social_media', 'news', 'governance', 'manual'))
    """)


def downgrade() -> None:
    # raw_flood_staging source_type — restore original
    op.execute("""
        ALTER TABLE raw_flood_staging
            DROP CONSTRAINT IF EXISTS raw_flood_staging_source_type_check
    """)
    op.execute("""
        ALTER TABLE raw_flood_staging
            ADD CONSTRAINT raw_flood_staging_source_type_check
                CHECK (source_type IN ('social_media', 'news'))
    """)

    # location_aliases alias_type — restore original
    op.execute("""
        ALTER TABLE location_aliases
            DROP CONSTRAINT IF EXISTS location_aliases_alias_type_check
    """)
    op.execute("""
        ALTER TABLE location_aliases
            ADD CONSTRAINT location_aliases_alias_type_check
                CHECK (alias_type IN
                    ('district','street','landmark','apartment','slang','other'))
    """)

    # flood_events upsert key — restore original UNIQUE constraint
    op.execute("DROP INDEX IF EXISTS uidx_flood_events_upsert")
    op.execute("""
        ALTER TABLE flood_events
            ADD CONSTRAINT uidx_flood_events_upsert_key
            UNIQUE (source_url, event_date, ward)
    """)

    # flood_events resolution columns — drop
    op.execute("""
        ALTER TABLE flood_events
            DROP COLUMN IF EXISTS resolution_level,
            DROP COLUMN IF EXISTS resolution_confidence
    """)
