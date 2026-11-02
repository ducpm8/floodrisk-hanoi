"""
Extended static aliases for location_aliases — FIR-37.

Covers the four remaining alias types (districts are in seed_districts.py):
  - street    : major Hanoi roads/streets commonly referenced in flood reports
  - landmark  : lakes, hospitals, universities, malls, stations
  - apartment : residential complexes frequently searched for flood risk
  - slang     : colloquial area names and khu đô thị references

Each entry: (alias_vi, representative_ward_vi_primary, representative_ward_vi_fallback, alias_type)

Ward lookup is identical to seed_districts.py — tries exact, prefix variants,
then skips with a WARNING if nothing found (ward may be missing from OSM set).

Safe to re-run — ON CONFLICT (alias_norm) DO NOTHING.

Usage:
    cd api/
    python -m seeds.seed_aliases_extended
"""

import logging
import os
import sys

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from api.utils.text import normalize_ward

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# (alias_vi, ward_vi_primary, ward_vi_fallback, alias_type)
ALIASES: list[tuple[str, str, str, str]] = [

    # ── Streets ──────────────────────────────────────────────────────────────
    # Major Hanoi roads frequently referenced in flood/drainage news

    ("Nguyễn Trãi",          "Phường Thượng Đình",    "Xã Thanh Xuân",        "street"),
    ("Trần Duy Hưng",        "Phường Trung Hòa",      "Phường Yên Hòa",       "street"),
    ("Phạm Hùng",            "Phường Mỹ Đình 1",      "Phường Mỹ Đình 2",     "street"),
    ("Hoàng Quốc Việt",      "Phường Nghĩa Đô",       "Phường Xuân La",       "street"),
    ("Phố Giải Phóng",       "Phường Phương Liệt",    "Phường Thịnh Liệt",    "street"),
    ("Đường Lê Duẩn",        "Phường Cát Linh",       "Phường Văn Miếu",      "street"),
    ("Kim Mã",               "Phường Kim Mã",         "Phường Ngọc Khánh",    "street"),
    ("Liễu Giai",            "Phường Liễu Giai",      "Phường Ngọc Khánh",    "street"),
    ("Đê La Thành",          "Phường Ô Chợ Dừa",      "Phường Thổ Quan",      "street"),
    ("Láng Hạ",              "Phường Láng Hạ",        "Phường Thành Công",    "street"),
    ("Nguyễn Chí Thanh",     "Phường Láng Thượng",    "Phường Thành Công",    "street"),
    ("Trần Phú",             "Phường Hà Cầu",         "Phường Văn Quán",      "street"),
    ("Lê Văn Lương",         "Phường Nhân Chính",     "Phường Trung Hòa",     "street"),
    ("Tây Sơn",              "Phường Thịnh Quang",    "Phường Quang Trung",   "street"),
    ("Phố Khâm Thiên",       "Phường Khâm Thiên",     "Phường Thổ Quan",      "street"),
    ("Xã Đàn",               "Phường Phương Liên",    "Phường Nam Đồng",      "street"),
    ("Bạch Mai",             "Phường Bạch Mai",       "Phường Cầu Dền",       "street"),
    ("Minh Khai",            "Phường Minh Khai",      "Phường Vĩnh Tuy",      "street"),
    ("Vĩnh Tuy",             "Phường Vĩnh Tuy",       "Phường Thanh Lương",   "street"),
    ("Nguyễn Văn Cừ",        "Phường Gia Thụy",       "Phường Ngọc Lâm",      "street"),
    ("Âu Cơ",                "Phường Tứ Liên",        "Phường Nhật Tân",      "street"),
    ("Lạc Long Quân",        "Phường Nghĩa Đô",       "Phường Bưởi",          "street"),
    ("Hoàng Hoa Thám",       "Phường Liễu Giai",      "Phường Ngọc Hà",       "street"),
    ("Đội Cấn",              "Phường Đội Cấn",        "Phường Cống Vị",       "street"),
    ("Xuân Thủy",            "Phường Dịch Vọng Hậu",  "Phường Dịch Vọng",     "street"),
    ("Cầu Giấy",             "Phường Dịch Vọng",      "Phường Quan Hoa",      "street"),
    ("Nguyễn Xiển",          "Phường Đại Kim",        "Phường Định Công",     "street"),
    ("Trương Định",          "Phường Trương Định",    "Phường Tương Mai",     "street"),
    ("Tam Trinh",            "Phường Mai Động",       "Phường Vĩnh Hưng",     "street"),
    ("Đường Pháp Vân",        "Phường Hoàng Liệt",     "Phường Thịnh Liệt",    "street"),
    ("Nguyễn Tuân",          "Phường Thanh Xuân Trung","Phường Nhân Chính",   "street"),
    ("Vũ Trọng Phụng",       "Phường Thanh Xuân Trung","Phường Phương Liệt",  "street"),
    ("Khuất Duy Tiến",       "Phường Thanh Xuân Bắc", "Phường Nhân Chính",   "street"),
    ("Phạm Văn Đồng",        "Phường Đức Thắng",      "Phường Xuân Đỉnh",     "street"),
    ("Võ Chí Công",          "Phường Phú Thượng",     "Phường Tứ Liên",       "street"),
    ("Trần Quốc Hoàn",       "Phường Dịch Vọng",      "Phường Nghĩa Tân",     "street"),
    ("Nguyễn Phong Sắc",     "Phường Dịch Vọng Hậu",  "Phường Nghĩa Tân",     "street"),
    ("Dương Đình Nghệ",      "Phường Yên Hòa",        "Phường Dịch Vọng",     "street"),

    # ── Landmarks ────────────────────────────────────────────────────────────

    # Lakes
    ("Hồ Tây",               "Phường Quảng An",       "Phường Tây Hồ",        "landmark"),
    ("Hồ Hoàn Kiếm",         "Phường Hàng Trống",     "Phường Lý Thái Tổ",    "landmark"),
    ("Hồ Thiền Quang",        "Phường Nguyễn Du",      "Phường Trần Hưng Đạo","landmark"),
    ("Hồ Đống Đa",            "Phường Thổ Quan",       "Phường Khâm Thiên",    "landmark"),
    ("Hồ Ba Mẫu",             "Phường Phương Liên",    "Phường Nam Đồng",      "landmark"),
    ("Hồ Linh Đàm",           "Phường Hoàng Liệt",     "Phường Đại Kim",       "landmark"),
    ("Hồ Yên Sở",             "Phường Yên Sở",         "Phường Trần Phú",      "landmark"),
    ("Hồ Nghĩa Đô",           "Phường Nghĩa Đô",       "Phường Cầu Giấy",      "landmark"),

    # Hospitals
    ("Bệnh viện Bạch Mai",    "Phường Phương Mai",     "Phường Đồng Tâm",      "landmark"),
    ("Bệnh viện 108",         "Phường Bạch Đằng",      "Phường Lê Đại Hành",   "landmark"),
    ("Bệnh viện Việt Đức",    "Phường Cửa Nam",        "Phường Hàng Bông",     "landmark"),
    ("Bệnh viện Nhi",         "Phường La Thành",       "Phường Giảng Võ",      "landmark"),
    ("Bệnh viện E",           "Phường Cống Vị",        "Phường Đội Cấn",       "landmark"),
    ("Bệnh viện K",           "Phường Quan Hoa",       "Phường Dịch Vọng",     "landmark"),
    ("Bệnh viện Saint Paul",  "Phường Nguyễn Trung Trực","Phường Trúc Bạch",  "landmark"),

    # Universities
    ("Đại học Quốc gia",      "Phường Dịch Vọng Hậu",  "Phường Dịch Vọng",     "landmark"),
    ("Đại học Bách Khoa",     "Phường Bách Khoa",      "Phường Đồng Tâm",      "landmark"),
    ("ĐH Bách Khoa",          "Phường Bách Khoa",      "Phường Đồng Tâm",      "landmark"),
    ("Đại học Xây Dựng",      "Phường Đồng Tâm",       "Phường Bách Khoa",     "landmark"),
    ("Đại học Kinh Tế Quốc Dân","Phường Đồng Tâm",    "Phường Bách Khoa",     "landmark"),
    ("NEU",                   "Phường Đồng Tâm",       "Phường Bách Khoa",     "landmark"),
    ("Đại học Thủy Lợi",      "Phường Thượng Đình",    "Phường Phương Liệt",   "landmark"),
    ("ĐHQG Hà Nội",           "Phường Dịch Vọng Hậu",  "Phường Dịch Vọng",     "landmark"),
    ("Học viện Kỹ thuật Quân sự","Phường Hoàng Liệt",  "Phường Đại Kim",       "landmark"),

    # Transport / civic
    ("Ga Hà Nội",             "Phường Cửa Nam",        "Phường Trần Hưng Đạo","landmark"),
    ("Sân bay Nội Bài",       "Thị trấn Sóc Sơn",     "Xã Phù Lỗ",           "landmark"),
    ("Nội Bài",               "Thị trấn Sóc Sơn",     "Xã Phù Lỗ",           "landmark"),

    # Malls / commercial
    ("Vincom Phạm Ngọc Thạch","Phường Ô Chợ Dừa",     "Phường Cát Linh",      "landmark"),
    ("Vincom Bà Triệu",       "Phường Lê Đại Hành",   "Phường Bùi Thị Xuân",  "landmark"),
    ("Vincom Trần Duy Hưng",  "Phường Trung Hòa",     "Phường Yên Hòa",       "landmark"),
    ("Lotte Center",          "Phường Liễu Giai",     "Phường Ngọc Khánh",    "landmark"),
    ("Big C Thăng Long",      "Phường Mỹ Đình 2",     "Phường Mỹ Đình 1",     "landmark"),
    ("Aeon Mall Hà Đông",     "Phường Dương Nội",     "Phường La Khê",         "landmark"),
    ("Aeon Long Biên",        "Phường Phúc Lợi",      "Phường Bồ Đề",          "landmark"),
    ("TTTM Mipec Long Biên",  "Phường Bồ Đề",         "Phường Ngọc Lâm",       "landmark"),
    ("Savico Mall",           "Phường Gia Thụy",      "Phường Ngọc Lâm",       "landmark"),

    # Parks
    ("Công viên Thống Nhất",  "Phường Lê Đại Hành",   "Phường Nguyễn Du",     "landmark"),
    ("Công viên Nghĩa Đô",    "Phường Nghĩa Đô",      "Phường Cầu Giấy",      "landmark"),
    ("Công viên Yên Sở",      "Phường Yên Sở",        "Phường Trần Phú",       "landmark"),
    ("Công viên Hòa Bình",    "Phường Đức Thắng",     "Phường Thụy Phương",    "landmark"),

    # ── Apartment complexes ───────────────────────────────────────────────────
    # High-density residential complexes — top flood-risk search terms

    ("Times City",            "Phường Vĩnh Tuy",       "Phường Minh Khai",     "apartment"),
    ("Royal City",            "Phường Thanh Xuân Trung","Phường Thượng Đình",  "apartment"),
    ("Vinhomes Gardenia",     "Phường Hàm Nghi",       "Phường Mỹ Đình 1",     "apartment"),
    ("Vinhomes Smart City",   "Phường Tây Mỗ",         "Phường Đại Mỗ",        "apartment"),
    ("Vinhomes West Point",   "Phường Mỹ Đình 2",      "Phường Phú Đô",        "apartment"),
    ("Vinhomes Ocean Park",   "Xã Đặng Xá",            "Thị trấn Yên Viên",    "apartment"),
    ("Ecopark",               "Xã Đặng Xá",            "Xã Dương Quang",       "apartment"),
    ("Keangnam",              "Phường Mỹ Đình 1",      "Phường Mỹ Đình 2",     "apartment"),
    ("The Manor",             "Phường Mỹ Đình 2",      "Phường Mỹ Đình 1",     "apartment"),
    ("Goldmark City",         "Phường Phúc Diễn",      "Phường Đức Thắng",     "apartment"),
    ("Mipec Riverside",       "Phường Long Biên",      "Phường Bồ Đề",          "apartment"),
    ("Imperia Sky Garden",    "Phường Vĩnh Tuy",       "Phường Minh Khai",     "apartment"),
    ("D'. Capitale",          "Phường Trần Phú",       "Phường Yên Hòa",       "apartment"),
    ("Golden Land",           "Phường Thanh Xuân Trung","Phường Nhân Chính",   "apartment"),
    ("Mulberry Lane",         "Phường Dương Nội",      "Phường Mỗ Lao",        "apartment"),
    ("HH Linh Đàm",           "Phường Hoàng Liệt",     "Phường Đại Kim",       "apartment"),
    ("Rivera Park",           "Phường Thượng Đình",    "Xã Thanh Xuân",        "apartment"),
    ("Mon City",              "Phường Mỹ Đình 1",      "Phường Phú Đô",        "apartment"),
    ("The Zei",               "Phường Mỹ Đình 1",      "Phường Mỹ Đình 2",     "apartment"),
    ("Seasons Avenue",        "Phường Mỗ Lao",         "Phường Dương Nội",     "apartment"),
    ("Pandora City",          "Phường Thanh Xuân Trung","Phường Thượng Đình",  "apartment"),
    ("Homeland",              "Phường Thượng Thanh",   "Phường Giang Biên",    "apartment"),
    ("CT2 Trung Văn",         "Phường Trung Văn",      "Phường Mỹ Đình 2",     "apartment"),
    ("Dương Nội",             "Phường Dương Nội",      "Phường La Khê",         "apartment"),
    ("An Bình City",          "Phường Phúc Diễn",      "Phường Đức Thắng",     "apartment"),
    ("Gelexia Riverside",     "Phường Tam Trinh",      "Phường Hoàng Văn Thụ", "apartment"),
    ("Park Hill",             "Phường Times City",     "Phường Vĩnh Tuy",      "apartment"),
    ("Valencia",              "Phường Long Biên",      "Phường Giang Biên",    "apartment"),
    ("Mandarin Garden",       "Phường Trung Hòa",      "Phường Yên Hòa",       "apartment"),
    ("FLC Complex",           "Phường Phương Canh",    "Phường Xuân Phương",   "apartment"),
    ("Chung cư Linh Đàm",     "Phường Hoàng Liệt",     "Phường Đại Kim",       "apartment"),

    # ── Slang / colloquial area names ─────────────────────────────────────────

    ("Khu Mỹ Đình",           "Phường Mỹ Đình 1",      "Phường Mỹ Đình 2",     "slang"),
    ("Mỹ Đình",               "Phường Mỹ Đình 1",      "Phường Mỹ Đình 2",     "slang"),
    ("Khu Linh Đàm",          "Phường Hoàng Liệt",     "Phường Đại Kim",       "slang"),
    ("Linh Đàm",              "Phường Hoàng Liệt",     "Phường Đại Kim",       "slang"),
    ("Khu Định Công",         "Phường Định Công",      "Phường Đại Kim",       "slang"),
    ("Định Công",             "Phường Định Công",      "Phường Đại Kim",       "slang"),
    ("Khu Đại Kim",           "Phường Đại Kim",        "Phường Hoàng Liệt",    "slang"),
    ("Khu Mỗ Lao",            "Phường Mỗ Lao",         "Phường Dương Nội",     "slang"),
    ("Mỗ Lao",                "Phường Mỗ Lao",         "Phường La Khê",         "slang"),
    ("Khu Nhổn",              "Phường Minh Khai",      "Phường Phúc Diễn",     "slang"),
    ("Khu Trung Hòa Nhân Chính","Phường Trung Hòa",   "Phường Nhân Chính",    "slang"),
    ("THNC",                  "Phường Trung Hòa",      "Phường Nhân Chính",    "slang"),
    ("Khu Đô Thị Mới Cầu Giấy","Phường Dịch Vọng",   "Phường Trung Hòa",     "slang"),
    ("Khu vực Láng",          "Phường Láng Thượng",    "Phường Láng Hạ",       "slang"),
    ("Láng",                  "Phường Láng Thượng",    "Phường Láng Hạ",       "slang"),
    ("Khu vực Giảng Võ",      "Phường Giảng Võ",       "Phường Thành Công",    "slang"),
    ("Giảng Võ",              "Phường Giảng Võ",       "Phường Thành Công",    "slang"),
    ("Khu vực Kim Liên",      "Phường Kim Liên",       "Phường Phương Mai",    "slang"),
    ("Kim Liên",              "Phường Kim Liên",       "Phường Phương Mai",    "slang"),
    ("Khu tập thể Thành Công","Phường Thành Công",     "Phường Giảng Võ",      "slang"),
    ("Tập thể Giảng Võ",      "Phường Giảng Võ",       "Phường Thành Công",    "slang"),
    ("Khu vực Bưởi",          "Phường Bưởi",           "Phường Thụy Khuê",     "slang"),
    ("Bưởi",                  "Phường Bưởi",           "Phường Thụy Khuê",     "slang"),
    ("Nhật Tân",              "Phường Nhật Tân",       "Phường Tứ Liên",       "slang"),
    ("Khu vực Tứ Liên",       "Phường Tứ Liên",        "Phường Nhật Tân",      "slang"),
    ("Khu Văn Quán",          "Phường Văn Quán",       "Phường Hà Cầu",        "slang"),
    ("Yên Nghĩa",             "Phường Yên Nghĩa",      "Phường Hà Cầu",        "slang"),
    ("Khu đô thị Xa La",      "Phường Phú La",         "Phường Hà Cầu",        "slang"),
    ("Xa La",                 "Phường Phú La",         "Phường Hà Cầu",        "slang"),
    ("Khu Dương Nội",         "Phường Dương Nội",      "Phường La Khê",         "slang"),
    ("KĐT Dương Nội",         "Phường Dương Nội",      "Phường La Khê",         "slang"),
    ("Khu vực Gia Lâm",       "Thị trấn Yên Viên",    "Xã Đặng Xá",           "slang"),
    ("Khu đô thị Đặng Xá",    "Xã Đặng Xá",           "Thị trấn Yên Viên",    "slang"),
    ("Khu Bắc Linh Đàm",      "Phường Hoàng Liệt",     "Phường Vĩnh Hưng",     "slang"),
    ("Khu Thịnh Liệt",        "Phường Thịnh Liệt",     "Phường Hoàng Liệt",    "slang"),
    ("Khu vực Pháp Vân",      "Phường Hoàng Liệt",     "Phường Thịnh Liệt",    "slang"),
    ("Pháp Vân",              "Phường Hoàng Liệt",     "Phường Thịnh Liệt",    "slang"),
    ("Khu đô thị Nam Thăng Long","Phường Phú Thượng",  "Phường Xuân La",       "slang"),
    ("Ciputra",               "Phường Xuân La",        "Phường Phú Thượng",    "slang"),
    ("KĐT Ciputra",           "Phường Xuân La",        "Phường Phú Thượng",    "slang"),
    ("Tây Hồ Tây",            "Phường Xuân Tảo",       "Phường Thụy Phương",   "slang"),
    ("Khu Tây Hồ Tây",        "Phường Xuân Tảo",       "Phường Thụy Phương",   "slang"),
    ("Khu vực Nhổn",          "Phường Minh Khai",      "Phường Phúc Diễn",     "slang"),
    ("Nhổn",                  "Phường Minh Khai",      "Phường Phúc Diễn",     "slang"),
]


def seed(database_url: str | None = None) -> dict:
    url = database_url or os.environ.get(
        "DATABASE_SYNC_URL",
        "postgresql+psycopg2://flood:flood@localhost:5432/flooddb",
    )
    engine = create_engine(url)
    counts: dict[str, int] = {"street": 0, "landmark": 0, "apartment": 0, "slang": 0}
    skipped = 0

    with engine.begin() as conn:
        for alias_vi, ward_vi_primary, ward_vi_fallback, alias_type in ALIASES:
            alias_norm = normalize_ward(alias_vi)

            # Resolve ward: try primary then fallback, with prefix variants
            ward_name = _resolve_ward(conn, ward_vi_primary) or _resolve_ward(conn, ward_vi_fallback)

            if not ward_name:
                log.warning("SKIP %r (%s) — no ward found in DB", alias_vi, alias_type)
                skipped += 1
                continue

            result = conn.execute(
                text("""
                    INSERT INTO location_aliases
                        (alias_norm, alias_vi, ward_name, alias_type, confidence, source)
                    VALUES (:alias_norm, :alias_vi, :ward_name, :alias_type, :confidence, 'static')
                    ON CONFLICT (alias_norm) DO NOTHING
                """),
                {
                    "alias_norm": alias_norm,
                    "alias_vi": alias_vi,
                    "ward_name": ward_name,
                    "alias_type": alias_type,
                    "confidence": 0.85,
                },
            )
            if result.rowcount:
                counts[alias_type] = counts.get(alias_type, 0) + 1
                log.info("  ✓ [%s] %s → %s", alias_type, alias_vi, ward_name)
            else:
                log.debug("  - %s already exists", alias_vi)

    total = sum(counts.values())
    log.info(
        "Done. %d inserted (street=%d, landmark=%d, apartment=%d, slang=%d). %d skipped.",
        total, counts["street"], counts["landmark"], counts["apartment"], counts["slang"],
        skipped,
    )
    engine.dispose()
    return {**counts, "skipped": skipped}


def _resolve_ward(conn, ward_vi: str) -> str | None:
    """Try exact + prefix variants against ward_centroids. Return ward_name or None."""
    norm = normalize_ward(ward_vi)
    # 1. Exact
    row = conn.execute(
        text("SELECT ward_name FROM ward_centroids WHERE ward_name = :n LIMIT 1"),
        {"n": norm},
    ).fetchone()
    if row:
        return row.ward_name
    # 2. Try prefix variants (strip/add phuong, xa, thi tran)
    bare = norm
    for prefix in ("phuong ", "xa ", "thi tran "):
        if norm.startswith(prefix):
            bare = norm[len(prefix):]
            break
    for prefix in ("phuong ", "xa ", "thi tran "):
        candidate = prefix + bare
        row = conn.execute(
            text("SELECT ward_name FROM ward_centroids WHERE ward_name = :n LIMIT 1"),
            {"n": candidate},
        ).fetchone()
        if row:
            return row.ward_name
    # 3. LIKE fallback
    row = conn.execute(
        text("SELECT ward_name FROM ward_centroids WHERE ward_name LIKE :p LIMIT 1"),
        {"p": f"%{bare}%"},
    ).fetchone()
    if row:
        return row.ward_name
    return None


if __name__ == "__main__":
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    result = seed()
    log.info("Result: %s", result)
