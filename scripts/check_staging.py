import psycopg2, os, sys
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv()
db_url = os.getenv("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")
conn = psycopg2.connect(db_url)
cur = conn.cursor()

print("=== Unique index on flood_events ===")
cur.execute("""
    SELECT indexname, indexdef
    FROM pg_indexes
    WHERE tablename = 'flood_events' AND indexname = 'uidx_flood_events_upsert'
""")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

print("\n=== Unprocessed staging rows ===")
cur.execute("SELECT COUNT(*) FROM raw_flood_staging WHERE processed=FALSE")
print(f"  Count: {cur.fetchone()[0]}")

print("\n=== flood_events count ===")
cur.execute("SELECT COUNT(*) FROM flood_events")
print(f"  Total: {cur.fetchone()[0]}")
