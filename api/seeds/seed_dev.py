"""
Dev seed: 10 flood events + 5 price records for local development.

Uses real Hanoi ward names, realistic rainy-season dates (May–October),
and plausible price data. Safe to re-run (truncates then re-inserts).

Usage:
    cd api/
    python -m seeds.seed_dev

Requirements:
    - DATABASE_SYNC_URL env var (or .env file)
    - alembic upgrade head + seeds/seed_wards.py already run
"""

import logging
import os
import sys
from datetime import date

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# 10 flood events: real Hanoi wards, rainy season dates 2019-2023
FLOOD_EVENTS = [
    {
        "location_name": "Ngõ Xã Đàn, Đống Đa",
        "ward": "dong da",
        "district": "Đống Đa",
        "lat": 21.0245,
        "lng": 105.8412,
        "event_date": date(2023, 7, 15),
        "severity": "severe",
        "depth_cm": 80,
        "duration_hours": 6,
        "source": "official",
        "source_url": "http://thoatnuochanoi.vn/ungngap",
    },
    {
        "location_name": "Phố Khâm Thiên, Đống Đa",
        "ward": "kham thien",
        "district": "Đống Đa",
        "lat": 21.0198,
        "lng": 105.8438,
        "event_date": date(2023, 8, 2),
        "severity": "moderate",
        "depth_cm": 40,
        "duration_hours": 3,
        "source": "news",
        "source_url": "https://vnexpress.net/ha-noi-ngap-2023",
    },
    {
        "location_name": "Phường Cầu Giấy",
        "ward": "cau giay",
        "district": "Cầu Giấy",
        "lat": 21.0317,
        "lng": 105.7937,
        "event_date": date(2022, 8, 20),
        "severity": "moderate",
        "depth_cm": 35,
        "duration_hours": 4,
        "source": "official",
        "source_url": None,
    },
    {
        "location_name": "Ngõ 178 Thái Hà, Đống Đa",
        "ward": "trung liet",
        "district": "Đống Đa",
        "lat": 21.0168,
        "lng": 105.8298,
        "event_date": date(2022, 9, 5),
        "severity": "severe",
        "depth_cm": 90,
        "duration_hours": 8,
        "source": "community",
        "source_url": None,
    },
    {
        "location_name": "Phường Thanh Xuân Trung",
        "ward": "thanh xuan trung",
        "district": "Thanh Xuân",
        "lat": 20.9952,
        "lng": 105.8123,
        "event_date": date(2022, 7, 30),
        "severity": "minor",
        "depth_cm": 15,
        "duration_hours": 1,
        "source": "news",
        "source_url": "https://tuoitre.vn/ha-noi-mua-lon-2022",
    },
    {
        "location_name": "Phố Hoàng Hoa Thám, Ba Đình",
        "ward": "lieu giai",
        "district": "Ba Đình",
        "lat": 21.0402,
        "lng": 105.8213,
        "event_date": date(2021, 6, 18),
        "severity": "moderate",
        "depth_cm": 50,
        "duration_hours": 2,
        "source": "official",
        "source_url": None,
    },
    {
        "location_name": "Phường Nhân Chính, Thanh Xuân",
        "ward": "nhan chinh",
        "district": "Thanh Xuân",
        "lat": 21.0012,
        "lng": 105.8076,
        "event_date": date(2021, 8, 10),
        "severity": "severe",
        "depth_cm": 70,
        "duration_hours": 5,
        "source": "community",
        "source_url": None,
    },
    {
        "location_name": "Phường Yên Hòa, Cầu Giấy",
        "ward": "yen hoa",
        "district": "Cầu Giấy",
        "lat": 21.0255,
        "lng": 105.7889,
        "event_date": date(2020, 9, 12),
        "severity": "moderate",
        "depth_cm": 30,
        "duration_hours": 3,
        "source": "news",
        "source_url": None,
    },
    {
        "location_name": "Phường Trung Hòa, Cầu Giấy",
        "ward": "trung hoa",
        "district": "Cầu Giấy",
        "lat": 21.0178,
        "lng": 105.7934,
        "event_date": date(2019, 7, 25),
        "severity": "minor",
        "depth_cm": 20,
        "duration_hours": 2,
        "source": "official",
        "source_url": None,
    },
    {
        "location_name": "Phường Kim Mã, Ba Đình",
        "ward": "kim ma",
        "district": "Ba Đình",
        "lat": 21.0367,
        "lng": 105.8264,
        "event_date": date(2019, 8, 8),
        "severity": "moderate",
        "depth_cm": 45,
        "duration_hours": 4,
        "source": "news",
        "source_url": None,
    },
]

# 5 price records: ward-level monthly aggregates
PRICE_RECORDS = [
    {
        "ward": "dong da",
        "district": "Đống Đa",
        "property_type": "apartment",
        "price_per_m2_vnd": 52_000_000,
        "recorded_month": date(2024, 1, 1),
        "last_updated": "2024-01-31",
        "source": "batdongsan.com.vn",
        "data_caveat": "Listing price, not confirmed sale price",
    },
    {
        "ward": "cau giay",
        "district": "Cầu Giấy",
        "property_type": "apartment",
        "price_per_m2_vnd": 58_000_000,
        "recorded_month": date(2024, 1, 1),
        "last_updated": "2024-01-31",
        "source": "batdongsan.com.vn",
        "data_caveat": "Listing price, not confirmed sale price",
    },
    {
        "ward": "thanh xuan trung",
        "district": "Thanh Xuân",
        "property_type": "apartment",
        "price_per_m2_vnd": 44_000_000,
        "recorded_month": date(2024, 1, 1),
        "last_updated": "2024-01-31",
        "source": "batdongsan.com.vn",
        "data_caveat": "Listing price, not confirmed sale price",
    },
    {
        "ward": "kim ma",
        "district": "Ba Đình",
        "property_type": "house",
        "price_per_m2_vnd": 210_000_000,
        "recorded_month": date(2024, 1, 1),
        "last_updated": "2024-01-31",
        "source": "batdongsan.com.vn",
        "data_caveat": "Listing price, not confirmed sale price",
    },
    {
        "ward": "yen hoa",
        "district": "Cầu Giấy",
        "property_type": "land",
        "price_per_m2_vnd": 180_000_000,
        "recorded_month": date(2024, 1, 1),
        "last_updated": "2024-01-31",
        "source": "batdongsan.com.vn",
        "data_caveat": "Listing price, not confirmed sale price",
    },
]


def seed(database_url: str | None = None) -> dict:
    url = database_url or os.environ.get(
        "DATABASE_SYNC_URL",
        "postgresql+psycopg2://flood:flood@localhost:5432/flooddb",
    )
    engine = create_engine(url)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM flood_events"))
        conn.execute(text("DELETE FROM price_records"))

        for event in FLOOD_EVENTS:
            conn.execute(
                text("""
                    INSERT INTO flood_events
                        (location_name, ward, district, lat, lng, geom,
                         event_date, severity, depth_cm, duration_hours, source, source_url, is_seed)
                    VALUES (
                        :location_name, :ward, :district, :lat, :lng,
                        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                        :event_date, :severity, :depth_cm, :duration_hours,
                        :source, :source_url, TRUE
                    )
                """),
                event,
            )

        for record in PRICE_RECORDS:
            conn.execute(
                text("""
                    INSERT INTO price_records
                        (ward, district, property_type, price_per_m2_vnd,
                         recorded_month, last_updated, source, data_caveat, is_seed)
                    VALUES (
                        :ward, :district, :property_type, :price_per_m2_vnd,
                        :recorded_month, :last_updated, :source, :data_caveat, TRUE
                    )
                """),
                record,
            )

    engine.dispose()
    log.info(f"Dev seed complete: {len(FLOOD_EVENTS)} flood events, {len(PRICE_RECORDS)} price records")
    return {"flood_events": len(FLOOD_EVENTS), "price_records": len(PRICE_RECORDS)}


if __name__ == "__main__":
    result = seed()
    log.info(f"Done: {result}")
