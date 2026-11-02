import asyncio
from api.database import engine
from sqlalchemy import text

async def clean():
    async with engine.begin() as conn:
        r1 = await conn.execute(text("DELETE FROM raw_flood_staging WHERE source_type='facebook'"))
        r2 = await conn.execute(text("DELETE FROM flood_events WHERE source='community' AND source_url LIKE '%facebook%'"))
        r3 = await conn.execute(text("DELETE FROM flood_events_review WHERE source_type='facebook'"))
        print(f'deleted staging={r1.rowcount}, events={r2.rowcount}, review={r3.rowcount}')

asyncio.run(clean())
