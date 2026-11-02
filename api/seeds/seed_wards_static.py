"""
Static dev seed for ward_centroids — used when Overpass API is unavailable.
Inserts 12 Hanoi wards (those used by seed_dev.py + Mỹ Đình I/II for E2E tests).
"""

import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

WARDS = [
    ("dong da",          "Đống Đa",           "Đống Đa",    21.0245, 105.8412),
    ("kham thien",       "Khâm Thiên",        "Đống Đa",    21.0198, 105.8438),
    ("cau giay",         "Cầu Giấy",          "Cầu Giấy",   21.0317, 105.7937),
    ("trung liet",       "Trung Liệt",        "Đống Đa",    21.0168, 105.8298),
    ("thanh xuan trung", "Thanh Xuân Trung",  "Thanh Xuân", 20.9952, 105.8123),
    ("lieu giai",        "Liễu Giai",         "Ba Đình",    21.0402, 105.8213),
    ("nhan chinh",       "Nhân Chính",        "Thanh Xuân", 21.0012, 105.8076),
    ("yen hoa",          "Yên Hòa",           "Cầu Giấy",   21.0255, 105.7889),
    ("trung hoa",        "Trung Hòa",         "Cầu Giấy",   21.0178, 105.7934),
    ("kim ma",           "Kim Mã",            "Ba Đình",    21.0367, 105.8264),
    ("my dinh ii",       "Mỹ Đình II",        "Nam Từ Liêm",21.0285, 105.7767),
    ("my dinh i",        "Mỹ Đình I",         "Nam Từ Liêm",21.0310, 105.7730),
]

url = os.environ.get(
    "DATABASE_SYNC_URL",
    "postgresql+psycopg2://flood:flood@localhost:5432/flooddb",
)
engine = create_engine(url)

with engine.begin() as conn:
    inserted = 0
    for ward_name, ward_name_vi, district, lat, lng in WARDS:
        r = conn.execute(
            text("""
                INSERT INTO ward_centroids (ward_name, ward_name_vi, district, lat, lng, geom)
                VALUES (
                    :ward_name, :ward_name_vi, :district, :lat, :lng,
                    ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                )
                ON CONFLICT (ward_name) DO NOTHING
            """),
            {"ward_name": ward_name, "ward_name_vi": ward_name_vi,
             "district": district, "lat": lat, "lng": lng},
        )
        inserted += r.rowcount

    total = conn.execute(text("SELECT COUNT(*) FROM ward_centroids")).scalar()

engine.dispose()
print(f"Inserted {inserted} wards. Total in table: {total}")
