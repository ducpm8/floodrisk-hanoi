"""
Seed Hanoi district-level entries into location_aliases.

Each of Hanoi's 30 districts (12 urban quận + 17 rural huyện + 1 thị xã)
is seeded as a static alias pointing to the most central ward in that district.
This means "Hai Bà Trưng", "Tây Hồ", etc. resolve immediately without Haiku.

Strategy: map district name → representative ward (geographically central).
These are approximate; district-level resolution is by definition imprecise.
Uses ON CONFLICT DO NOTHING — safe to re-run.

Usage:
    cd api/
    python -m seeds.seed_districts
"""

import logging
import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from api.utils.text import normalize_ward

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# District name (Vietnamese) → representative ward name (normalized, must exist in ward_centroids)
# If the representative ward is missing, the entry is skipped with a warning.
DISTRICT_ALIASES: list[tuple[str, str, str]] = [
    # (district_vi, representative_ward_vi, alternate_ward_vi_fallback)
    # Urban quận
    ("Ba Đình",         "Phường Điện Biên",         "Phường Trúc Bạch"),
    ("Hoàn Kiếm",       "Phường Hàng Bạc",          "Phường Hàng Buồm"),
    ("Đống Đa",         "Phường Ô Chợ Dừa",         "Phường Khương Thượng"),
    ("Hai Bà Trưng",    "Phường Bách Khoa",          "Phường Thanh Lương"),
    ("Cầu Giấy",        "Phường Dịch Vọng",          "Phường Nghĩa Đô"),
    ("Tây Hồ",          "Phường Tây Hồ",             "Phường Quảng An"),
    ("Thanh Xuân",      "Xã Thanh Xuân",             "Phường Khương Trung"),
    ("Hoàng Mai",       "Phường Hoàng Liệt",         "Phường Vĩnh Hưng"),
    ("Long Biên",       "Phường Bồ Đề",              "Phường Ngọc Lâm"),
    ("Nam Từ Liêm",     "Phường Mỹ Đình 1",          "Phường Mỹ Đình 2"),
    ("Bắc Từ Liêm",     "Phường Đức Thắng",          "Phường Thụy Phương"),
    ("Hà Đông",         "Phường Hà Cầu",             "Phường Văn Quán"),
    # Rural huyện
    ("Sóc Sơn",         "Thị trấn Sóc Sơn",         "Xã Phù Lỗ"),
    ("Đông Anh",        "Thị trấn Đông Anh",         "Xã Uy Nỗ"),
    ("Gia Lâm",         "Thị trấn Yên Viên",         "Xã Đặng Xá"),
    ("Thanh Trì",       "Phường Thanh Trì",          "Xã Ngũ Hiệp"),
    ("Thường Tín",       "Thị trấn Thường Tín",      "Xã Văn Bình"),
    ("Phú Xuyên",       "Thị trấn Phú Xuyên",        "Xã Phú Túc"),
    ("Ứng Hòa",         "Thị trấn Vân Đình",         "Xã Trầm Lộng"),
    ("Mỹ Đức",          "Thị trấn Đại Nghĩa",        "Xã An Tiến"),
    ("Thanh Oai",       "Thị trấn Kim Bài",          "Xã Bình Minh"),
    ("Chương Mỹ",       "Thị trấn Chúc Sơn",         "Xã Đồng Phú"),
    ("Quốc Oai",        "Thị trấn Quốc Oai",         "Xã Sài Sơn"),
    ("Thạch Thất",      "Thị trấn Liên Quan",        "Xã Đại Đồng"),
    ("Ba Vì",           "Thị trấn Tây Đằng",         "Xã Minh Châu"),
    ("Phúc Thọ",        "Thị trấn Phúc Thọ",         "Xã Sen Chiểu"),
    ("Đan Phượng",      "Thị trấn Phùng",            "Xã Song Phượng"),
    ("Hoài Đức",        "Thị trấn Trạm Trôi",        "Xã An Khánh"),
    ("Mê Linh",         "Thị trấn Chi Đông",         "Xã Tiến Thắng"),
    # Thị xã
    ("Sơn Tây",         "Phường Lê Lợi",             "Phường Quang Trung"),
]


def seed(database_url: str | None = None) -> int:
    url = database_url or os.environ.get(
        "DATABASE_SYNC_URL",
        "postgresql+psycopg2://flood:flood@localhost:5432/flooddb",
    )
    engine = create_engine(url)
    inserted = 0

    with engine.begin() as conn:
        for district_vi, ward_vi_primary, ward_vi_fallback in DISTRICT_ALIASES:
            district_norm = normalize_ward(district_vi)

            # Try primary ward, then fallback
            ward_name = None
            for candidate_vi in (ward_vi_primary, ward_vi_fallback):
                candidate_norm = normalize_ward(candidate_vi)
                row = conn.execute(
                    text("SELECT ward_name FROM ward_centroids WHERE ward_name = :n LIMIT 1"),
                    {"n": candidate_norm},
                ).fetchone()
                if row:
                    ward_name = row.ward_name
                    break
                # Try with known prefixes stripped/added
                for prefix in ("phuong ", "xa ", "thi tran "):
                    row = conn.execute(
                        text("SELECT ward_name FROM ward_centroids WHERE ward_name = :n LIMIT 1"),
                        {"n": prefix + normalize_ward(candidate_vi.split(" ", 1)[-1])},
                    ).fetchone()
                    if row:
                        ward_name = row.ward_name
                        break
                if ward_name:
                    break

            if not ward_name:
                log.warning("SKIP district %r — no representative ward found in DB", district_vi)
                continue

            result = conn.execute(
                text("""
                    INSERT INTO location_aliases
                        (alias_norm, alias_vi, ward_name, alias_type, confidence, source)
                    VALUES (:alias_norm, :alias_vi, :ward_name, 'district', 0.7, 'static')
                    ON CONFLICT (alias_norm) DO NOTHING
                """),
                {"alias_norm": district_norm, "alias_vi": district_vi, "ward_name": ward_name},
            )
            if result.rowcount:
                inserted += 1
                log.info("  ✓ %s → %s", district_vi, ward_name)
            else:
                log.info("  - %s already exists", district_vi)

    log.info("Done. %d district aliases inserted.", inserted)
    engine.dispose()
    return inserted


if __name__ == "__main__":
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    seed()
