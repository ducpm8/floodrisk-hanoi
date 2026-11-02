import sqlalchemy
from sqlalchemy import text
engine = sqlalchemy.create_engine("postgresql+psycopg2://flood:flood@localhost:5432/flooddb")
with engine.connect() as conn:
    result = conn.execute(text(
        "DELETE FROM raw_flood_staging WHERE external_id IN ('2264083300724304','1279366457720758','1081277157529690')"
    ))
    conn.commit()
    print(f"deleted {result.rowcount} rows")
