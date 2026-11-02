import sqlalchemy
from sqlalchemy import text
engine = sqlalchemy.create_engine("postgresql+psycopg2://flood:flood@localhost:5432/flooddb")
with engine.connect() as conn:
    conn.execute(text(
        "DELETE FROM flood_events_review WHERE staging_id IN "
        "(SELECT id FROM raw_flood_staging WHERE source_type = 'social_media' AND external_id IS NOT NULL)"
    ))
    result = conn.execute(text(
        "DELETE FROM raw_flood_staging WHERE source_type = 'social_media' AND external_id IS NOT NULL"
    ))
    conn.commit()
    print(f"deleted {result.rowcount} staging rows")
