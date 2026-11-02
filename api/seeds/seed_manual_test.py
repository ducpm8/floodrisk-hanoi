"""
Manual test seed: ~100 flood events + 120 price records for hands-on QA.

Covers 20+ wards across 8 districts. Truncates flood_events + price_records
then re-inserts — safe to run multiple times.

Usage:
    cd /c/firstproject
    PYTHONPATH=/c/firstproject DATABASE_SYNC_URL=postgresql+psycopg2://flood:flood@localhost:5432/flooddb \
        python -m api.seeds.seed_manual_test
"""

import logging
import os
import sys
from datetime import date

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ~100 flood events across 8 districts, years 2019-2024, all severities
# ---------------------------------------------------------------------------

FLOOD_EVENTS = [
    # === Đống Đa (13 events) ===
    {"location_name": "Ngõ Xã Đàn, Đống Đa", "ward": "dong da", "district": "Đống Đa", "lat": 21.0245, "lng": 105.8412, "event_date": date(2024, 7, 15), "severity": "severe", "depth_cm": 90, "duration_hours": 7, "source": "official"},
    {"location_name": "Phố Khâm Thiên", "ward": "kham thien", "district": "Đống Đa", "lat": 21.0198, "lng": 105.8438, "event_date": date(2024, 8, 2), "severity": "moderate", "depth_cm": 45, "duration_hours": 4, "source": "news"},
    {"location_name": "Ngõ 178 Thái Hà", "ward": "trung liet", "district": "Đống Đa", "lat": 21.0168, "lng": 105.8298, "event_date": date(2024, 9, 5), "severity": "severe", "depth_cm": 95, "duration_hours": 8, "source": "community"},
    {"location_name": "Phố Tây Sơn, Đống Đa", "ward": "quang trung", "district": "Đống Đa", "lat": 21.0221, "lng": 105.8361, "event_date": date(2023, 7, 20), "severity": "moderate", "depth_cm": 40, "duration_hours": 3, "source": "official"},
    {"location_name": "Ngõ 76 Tây Sơn", "ward": "quang trung", "district": "Đống Đa", "lat": 21.0218, "lng": 105.8355, "event_date": date(2023, 8, 15), "severity": "severe", "depth_cm": 80, "duration_hours": 6, "source": "community"},
    {"location_name": "Phường Láng Hạ", "ward": "lang ha", "district": "Đống Đa", "lat": 21.0193, "lng": 105.8232, "event_date": date(2023, 9, 10), "severity": "minor", "depth_cm": 15, "duration_hours": 1, "source": "news"},
    {"location_name": "Ngõ Thổ Quan, Đống Đa", "ward": "tho quan", "district": "Đống Đa", "lat": 21.0265, "lng": 105.8402, "event_date": date(2022, 7, 28), "severity": "moderate", "depth_cm": 50, "duration_hours": 5, "source": "official"},
    {"location_name": "Phố Đặng Văn Ngữ", "ward": "phuong lien", "district": "Đống Đa", "lat": 21.0145, "lng": 105.8369, "event_date": date(2022, 8, 18), "severity": "severe", "depth_cm": 75, "duration_hours": 6, "source": "community"},
    {"location_name": "Phường Trung Phụng", "ward": "trung phung", "district": "Đống Đa", "lat": 21.0272, "lng": 105.8456, "event_date": date(2021, 9, 3), "severity": "moderate", "depth_cm": 35, "duration_hours": 3, "source": "news"},
    {"location_name": "Phố Hào Nam", "ward": "o cho dua", "district": "Đống Đa", "lat": 21.0312, "lng": 105.8389, "event_date": date(2021, 7, 22), "severity": "minor", "depth_cm": 20, "duration_hours": 2, "source": "official"},
    {"location_name": "Ngõ 119 Hồ Đắc Di", "ward": "nam dong", "district": "Đống Đa", "lat": 21.0132, "lng": 105.8312, "event_date": date(2020, 8, 5), "severity": "severe", "depth_cm": 100, "duration_hours": 9, "source": "community"},
    {"location_name": "Phường Văn Chương", "ward": "van chuong", "district": "Đống Đa", "lat": 21.0289, "lng": 105.8477, "event_date": date(2019, 7, 30), "severity": "moderate", "depth_cm": 30, "duration_hours": 3, "source": "news"},
    {"location_name": "Phố Chợ Khâm Thiên", "ward": "kham thien", "district": "Đống Đa", "lat": 21.0201, "lng": 105.8442, "event_date": date(2019, 9, 12), "severity": "minor", "depth_cm": 18, "duration_hours": 1, "source": "official"},

    # === Cầu Giấy (10 events) ===
    {"location_name": "Phường Cầu Giấy", "ward": "cau giay", "district": "Cầu Giấy", "lat": 21.0317, "lng": 105.7937, "event_date": date(2024, 8, 10), "severity": "moderate", "depth_cm": 40, "duration_hours": 4, "source": "official"},
    {"location_name": "Phường Yên Hòa, Cầu Giấy", "ward": "yen hoa", "district": "Cầu Giấy", "lat": 21.0255, "lng": 105.7889, "event_date": date(2024, 9, 2), "severity": "severe", "depth_cm": 70, "duration_hours": 6, "source": "community"},
    {"location_name": "Phường Trung Hòa", "ward": "trung hoa", "district": "Cầu Giấy", "lat": 21.0178, "lng": 105.7934, "event_date": date(2023, 8, 20), "severity": "minor", "depth_cm": 20, "duration_hours": 2, "source": "news"},
    {"location_name": "Phường Dịch Vọng Hậu", "ward": "dich vong hau", "district": "Cầu Giấy", "lat": 21.0389, "lng": 105.7956, "event_date": date(2023, 7, 15), "severity": "moderate", "depth_cm": 55, "duration_hours": 5, "source": "official"},
    {"location_name": "Phố Trần Duy Hưng", "ward": "trung hoa", "district": "Cầu Giấy", "lat": 21.0165, "lng": 105.7921, "event_date": date(2022, 9, 8), "severity": "severe", "depth_cm": 80, "duration_hours": 7, "source": "community"},
    {"location_name": "Ngõ 167 Xuân Thủy", "ward": "dich vong", "district": "Cầu Giấy", "lat": 21.0401, "lng": 105.7878, "event_date": date(2022, 8, 1), "severity": "moderate", "depth_cm": 45, "duration_hours": 4, "source": "news"},
    {"location_name": "Phường Mai Dịch", "ward": "mai dich", "district": "Cầu Giấy", "lat": 21.0445, "lng": 105.7834, "event_date": date(2021, 7, 18), "severity": "minor", "depth_cm": 12, "duration_hours": 1, "source": "official"},
    {"location_name": "Ngõ 12 Láng Thượng", "ward": "lang thuong", "district": "Đống Đa", "lat": 21.0231, "lng": 105.8198, "event_date": date(2021, 8, 25), "severity": "moderate", "depth_cm": 38, "duration_hours": 3, "source": "community"},
    {"location_name": "Phường Quan Hoa", "ward": "quan hoa", "district": "Cầu Giấy", "lat": 21.0356, "lng": 105.8012, "event_date": date(2020, 9, 14), "severity": "severe", "depth_cm": 85, "duration_hours": 8, "source": "news"},
    {"location_name": "Phường Nghĩa Tân", "ward": "nghia tan", "district": "Cầu Giấy", "lat": 21.0478, "lng": 105.7912, "event_date": date(2019, 8, 22), "severity": "minor", "depth_cm": 22, "duration_hours": 2, "source": "official"},

    # === Ba Đình (10 events) ===
    {"location_name": "Phố Hoàng Hoa Thám, Ba Đình", "ward": "lieu giai", "district": "Ba Đình", "lat": 21.0402, "lng": 105.8213, "event_date": date(2024, 7, 8), "severity": "moderate", "depth_cm": 50, "duration_hours": 4, "source": "official"},
    {"location_name": "Phường Kim Mã, Ba Đình", "ward": "kim ma", "district": "Ba Đình", "lat": 21.0367, "lng": 105.8264, "event_date": date(2024, 8, 22), "severity": "minor", "depth_cm": 25, "duration_hours": 2, "source": "news"},
    {"location_name": "Ngõ 279 Đội Cấn", "ward": "doi can", "district": "Ba Đình", "lat": 21.0434, "lng": 105.8178, "event_date": date(2023, 9, 18), "severity": "severe", "depth_cm": 78, "duration_hours": 6, "source": "community"},
    {"location_name": "Phường Vĩnh Phúc", "ward": "vinh phuc", "district": "Ba Đình", "lat": 21.0421, "lng": 105.8156, "event_date": date(2023, 7, 5), "severity": "moderate", "depth_cm": 42, "duration_hours": 3, "source": "official"},
    {"location_name": "Phố Ngọc Hà, Ba Đình", "ward": "ngoc ha", "district": "Ba Đình", "lat": 21.0389, "lng": 105.8134, "event_date": date(2022, 8, 12), "severity": "minor", "depth_cm": 18, "duration_hours": 1, "source": "news"},
    {"location_name": "Phường Cống Vị", "ward": "cong vi", "district": "Ba Đình", "lat": 21.0356, "lng": 105.8198, "event_date": date(2022, 9, 25), "severity": "moderate", "depth_cm": 35, "duration_hours": 3, "source": "official"},
    {"location_name": "Phố Phan Đình Phùng", "ward": "quan thanh", "district": "Ba Đình", "lat": 21.0445, "lng": 105.8312, "event_date": date(2021, 8, 8), "severity": "severe", "depth_cm": 65, "duration_hours": 5, "source": "community"},
    {"location_name": "Phường Trúc Bạch", "ward": "truc bach", "district": "Ba Đình", "lat": 21.0478, "lng": 105.8289, "event_date": date(2020, 7, 28), "severity": "minor", "depth_cm": 14, "duration_hours": 1, "source": "news"},
    {"location_name": "Phường Thụy Khuê", "ward": "thuy khue", "district": "Tây Hồ", "lat": 21.0523, "lng": 105.8234, "event_date": date(2020, 9, 5), "severity": "moderate", "depth_cm": 48, "duration_hours": 4, "source": "official"},
    {"location_name": "Phố Hàng Than, Ba Đình", "ward": "nguyen trung truc", "district": "Ba Đình", "lat": 21.0412, "lng": 105.8345, "event_date": date(2019, 8, 15), "severity": "minor", "depth_cm": 20, "duration_hours": 2, "source": "news"},

    # === Thanh Xuân (10 events) ===
    {"location_name": "Phường Thanh Xuân Trung", "ward": "thanh xuan trung", "district": "Thanh Xuân", "lat": 20.9952, "lng": 105.8123, "event_date": date(2024, 7, 30), "severity": "severe", "depth_cm": 88, "duration_hours": 8, "source": "official"},
    {"location_name": "Phường Nhân Chính", "ward": "nhan chinh", "district": "Thanh Xuân", "lat": 21.0012, "lng": 105.8076, "event_date": date(2024, 8, 18), "severity": "moderate", "depth_cm": 55, "duration_hours": 5, "source": "community"},
    {"location_name": "Phố Vũ Trọng Phụng", "ward": "thanh xuan bac", "district": "Thanh Xuân", "lat": 20.9978, "lng": 105.8098, "event_date": date(2023, 9, 22), "severity": "minor", "depth_cm": 16, "duration_hours": 1, "source": "news"},
    {"location_name": "Ngõ Tập thể Thanh Xuân", "ward": "khuong mai", "district": "Thanh Xuân", "lat": 20.9934, "lng": 105.8145, "event_date": date(2023, 8, 8), "severity": "moderate", "depth_cm": 38, "duration_hours": 3, "source": "official"},
    {"location_name": "Phường Khương Đình", "ward": "khuong dinh", "district": "Thanh Xuân", "lat": 20.9889, "lng": 105.8167, "event_date": date(2022, 7, 12), "severity": "severe", "depth_cm": 72, "duration_hours": 7, "source": "community"},
    {"location_name": "Phường Hạ Đình, Thanh Xuân", "ward": "ha dinh", "district": "Thanh Xuân", "lat": 20.9912, "lng": 105.8201, "event_date": date(2022, 9, 3), "severity": "moderate", "depth_cm": 42, "duration_hours": 4, "source": "news"},
    {"location_name": "Phố Quan Nhân", "ward": "nhan chinh", "district": "Thanh Xuân", "lat": 21.0023, "lng": 105.8089, "event_date": date(2021, 8, 20), "severity": "minor", "depth_cm": 22, "duration_hours": 2, "source": "official"},
    {"location_name": "Phường Kim Giang", "ward": "kim giang", "district": "Thanh Xuân", "lat": 20.9867, "lng": 105.8223, "event_date": date(2021, 7, 15), "severity": "severe", "depth_cm": 82, "duration_hours": 8, "source": "community"},
    {"location_name": "Ngõ Phùng Khoang", "ward": "ha dinh", "district": "Thanh Xuân", "lat": 20.9898, "lng": 105.8187, "event_date": date(2020, 8, 28), "severity": "moderate", "depth_cm": 35, "duration_hours": 3, "source": "news"},
    {"location_name": "Phường Thanh Xuân Nam", "ward": "thanh xuan nam", "district": "Thanh Xuân", "lat": 20.9856, "lng": 105.8134, "event_date": date(2019, 9, 5), "severity": "minor", "depth_cm": 18, "duration_hours": 1, "source": "official"},

    # === Hoàng Mai (12 events) ===
    {"location_name": "Phường Tương Mai", "ward": "tuong mai", "district": "Hoàng Mai", "lat": 20.9923, "lng": 105.8456, "event_date": date(2024, 7, 22), "severity": "severe", "depth_cm": 92, "duration_hours": 8, "source": "official"},
    {"location_name": "Phố Minh Khai, Hai Bà Trưng", "ward": "vinh tuy", "district": "Hai Bà Trưng", "lat": 20.9978, "lng": 105.8523, "event_date": date(2024, 8, 5), "severity": "moderate", "depth_cm": 48, "duration_hours": 5, "source": "news"},
    {"location_name": "Phường Hoàng Văn Thụ", "ward": "hoang van thu", "district": "Hoàng Mai", "lat": 20.9867, "lng": 105.8489, "event_date": date(2024, 9, 15), "severity": "minor", "depth_cm": 14, "duration_hours": 1, "source": "community"},
    {"location_name": "Phường Giáp Bát, Hoàng Mai", "ward": "giap bat", "district": "Hoàng Mai", "lat": 20.9834, "lng": 105.8412, "event_date": date(2023, 7, 10), "severity": "severe", "depth_cm": 85, "duration_hours": 9, "source": "official"},
    {"location_name": "Phường Lĩnh Nam", "ward": "linh nam", "district": "Hoàng Mai", "lat": 20.9756, "lng": 105.8534, "event_date": date(2023, 8, 28), "severity": "moderate", "depth_cm": 55, "duration_hours": 6, "source": "news"},
    {"location_name": "Phường Yên Sở", "ward": "yen so", "district": "Hoàng Mai", "lat": 20.9712, "lng": 105.8489, "event_date": date(2023, 9, 8), "severity": "minor", "depth_cm": 20, "duration_hours": 2, "source": "community"},
    {"location_name": "Ngõ 318 Lĩnh Nam", "ward": "linh nam", "district": "Hoàng Mai", "lat": 20.9745, "lng": 105.8512, "event_date": date(2022, 8, 15), "severity": "severe", "depth_cm": 78, "duration_hours": 7, "source": "official"},
    {"location_name": "Phường Trần Phú, Hoàng Mai", "ward": "tran phu", "district": "Hoàng Mai", "lat": 20.9889, "lng": 105.8467, "event_date": date(2022, 7, 25), "severity": "moderate", "depth_cm": 42, "duration_hours": 4, "source": "news"},
    {"location_name": "Phường Đại Kim", "ward": "dai kim", "district": "Hoàng Mai", "lat": 20.9823, "lng": 105.8378, "event_date": date(2021, 9, 12), "severity": "minor", "depth_cm": 15, "duration_hours": 1, "source": "official"},
    {"location_name": "Phường Định Công", "ward": "dinh cong", "district": "Hoàng Mai", "lat": 20.9801, "lng": 105.8345, "event_date": date(2021, 8, 3), "severity": "severe", "depth_cm": 95, "duration_hours": 10, "source": "community"},
    {"location_name": "Phường Tân Mai, Hoàng Mai", "ward": "tan mai", "district": "Hoàng Mai", "lat": 20.9934, "lng": 105.8423, "event_date": date(2020, 7, 18), "severity": "moderate", "depth_cm": 38, "duration_hours": 3, "source": "news"},
    {"location_name": "Phường Mai Động", "ward": "mai dong", "district": "Hoàng Mai", "lat": 20.9956, "lng": 105.8501, "event_date": date(2019, 8, 10), "severity": "minor", "depth_cm": 22, "duration_hours": 2, "source": "official"},

    # === Hai Bà Trưng (10 events) ===
    {"location_name": "Phường Bạch Mai", "ward": "bach mai", "district": "Hai Bà Trưng", "lat": 21.0012, "lng": 105.8534, "event_date": date(2024, 8, 28), "severity": "severe", "depth_cm": 82, "duration_hours": 8, "source": "official"},
    {"location_name": "Phố Bạch Mai", "ward": "bach mai", "district": "Hai Bà Trưng", "lat": 21.0023, "lng": 105.8545, "event_date": date(2024, 7, 12), "severity": "moderate", "depth_cm": 52, "duration_hours": 5, "source": "news"},
    {"location_name": "Phường Thanh Nhàn", "ward": "thanh nhan", "district": "Hai Bà Trưng", "lat": 21.0056, "lng": 105.8589, "event_date": date(2023, 9, 5), "severity": "minor", "depth_cm": 18, "duration_hours": 2, "source": "community"},
    {"location_name": "Phường Trương Định", "ward": "truong dinh", "district": "Hai Bà Trưng", "lat": 20.9978, "lng": 105.8512, "event_date": date(2023, 8, 18), "severity": "severe", "depth_cm": 76, "duration_hours": 7, "source": "official"},
    {"location_name": "Phố Lê Đại Hành", "ward": "le dai hanh", "district": "Hai Bà Trưng", "lat": 21.0089, "lng": 105.8556, "event_date": date(2022, 7, 8), "severity": "moderate", "depth_cm": 44, "duration_hours": 4, "source": "news"},
    {"location_name": "Phường Minh Khai", "ward": "minh khai", "district": "Hai Bà Trưng", "lat": 21.0034, "lng": 105.8601, "event_date": date(2022, 8, 25), "severity": "minor", "depth_cm": 20, "duration_hours": 2, "source": "official"},
    {"location_name": "Phường Đồng Tâm, Hai Bà Trưng", "ward": "dong tam", "district": "Hai Bà Trưng", "lat": 21.0067, "lng": 105.8523, "event_date": date(2021, 9, 18), "severity": "severe", "depth_cm": 88, "duration_hours": 9, "source": "community"},
    {"location_name": "Ngõ Phố Vọng", "ward": "pho vong", "district": "Hai Bà Trưng", "lat": 21.0045, "lng": 105.8567, "event_date": date(2021, 7, 30), "severity": "moderate", "depth_cm": 35, "duration_hours": 3, "source": "news"},
    {"location_name": "Phường Tân Mai, Hai Bà Trưng", "ward": "tan mai", "district": "Hai Bà Trưng", "lat": 21.0012, "lng": 105.8612, "event_date": date(2020, 8, 12), "severity": "minor", "depth_cm": 16, "duration_hours": 1, "source": "official"},
    {"location_name": "Phường Vĩnh Tuy", "ward": "vinh tuy", "district": "Hai Bà Trưng", "lat": 21.0023, "lng": 105.8634, "event_date": date(2019, 9, 20), "severity": "moderate", "depth_cm": 40, "duration_hours": 4, "source": "community"},

    # === Nam Từ Liêm (10 events) ===
    {"location_name": "Phường Mỹ Đình II", "ward": "my dinh ii", "district": "Nam Từ Liêm", "lat": 21.0289, "lng": 105.7712, "event_date": date(2024, 7, 5), "severity": "severe", "depth_cm": 98, "duration_hours": 10, "source": "community"},
    {"location_name": "Phố Lê Đức Thọ", "ward": "my dinh i", "district": "Nam Từ Liêm", "lat": 21.0312, "lng": 105.7689, "event_date": date(2024, 8, 15), "severity": "moderate", "depth_cm": 55, "duration_hours": 5, "source": "official"},
    {"location_name": "Phường Trung Văn", "ward": "trung van", "district": "Nam Từ Liêm", "lat": 21.0089, "lng": 105.7834, "event_date": date(2024, 9, 28), "severity": "minor", "depth_cm": 18, "duration_hours": 2, "source": "news"},
    {"location_name": "Phường Tây Mỗ", "ward": "tay mo", "district": "Nam Từ Liêm", "lat": 21.0023, "lng": 105.7712, "event_date": date(2023, 7, 22), "severity": "severe", "depth_cm": 85, "duration_hours": 8, "source": "official"},
    {"location_name": "Phường Đại Mỗ", "ward": "dai mo", "district": "Nam Từ Liêm", "lat": 20.9978, "lng": 105.7678, "event_date": date(2023, 8, 10), "severity": "moderate", "depth_cm": 48, "duration_hours": 5, "source": "community"},
    {"location_name": "Ngõ Mỹ Đình, Nam Từ Liêm", "ward": "my dinh ii", "district": "Nam Từ Liêm", "lat": 21.0278, "lng": 105.7723, "event_date": date(2022, 9, 15), "severity": "severe", "depth_cm": 92, "duration_hours": 9, "source": "news"},
    {"location_name": "Phường Phú Đô", "ward": "phu do", "district": "Nam Từ Liêm", "lat": 21.0134, "lng": 105.7756, "event_date": date(2022, 8, 5), "severity": "minor", "depth_cm": 22, "duration_hours": 2, "source": "official"},
    {"location_name": "Phường Mễ Trì", "ward": "me tri", "district": "Nam Từ Liêm", "lat": 21.0178, "lng": 105.7801, "event_date": date(2021, 7, 28), "severity": "moderate", "depth_cm": 38, "duration_hours": 4, "source": "community"},
    {"location_name": "Phường Xuân Phương", "ward": "xuan phuong", "district": "Nam Từ Liêm", "lat": 21.0245, "lng": 105.7623, "event_date": date(2020, 9, 8), "severity": "severe", "depth_cm": 75, "duration_hours": 7, "source": "news"},
    {"location_name": "Phường Phương Canh", "ward": "phuong canh", "district": "Nam Từ Liêm", "lat": 21.0289, "lng": 105.7589, "event_date": date(2019, 8, 18), "severity": "minor", "depth_cm": 15, "duration_hours": 1, "source": "official"},

    # === Long Biên (8 events) ===
    {"location_name": "Phường Phúc Lợi, Long Biên", "ward": "phuc loi", "district": "Long Biên", "lat": 21.0589, "lng": 105.9034, "event_date": date(2024, 8, 20), "severity": "moderate", "depth_cm": 45, "duration_hours": 4, "source": "official"},
    {"location_name": "Phường Thượng Thanh", "ward": "thuong thanh", "district": "Long Biên", "lat": 21.0623, "lng": 105.8978, "event_date": date(2024, 7, 18), "severity": "severe", "depth_cm": 78, "duration_hours": 7, "source": "community"},
    {"location_name": "Phường Ngọc Lâm", "ward": "ngoc lam", "district": "Long Biên", "lat": 21.0556, "lng": 105.8912, "event_date": date(2023, 9, 12), "severity": "minor", "depth_cm": 20, "duration_hours": 2, "source": "news"},
    {"location_name": "Phường Bồ Đề, Long Biên", "ward": "bo de", "district": "Long Biên", "lat": 21.0512, "lng": 105.8867, "event_date": date(2023, 8, 5), "severity": "moderate", "depth_cm": 52, "duration_hours": 5, "source": "official"},
    {"location_name": "Phường Giang Biên", "ward": "giang bien", "district": "Long Biên", "lat": 21.0645, "lng": 105.9089, "event_date": date(2022, 7, 15), "severity": "severe", "depth_cm": 88, "duration_hours": 8, "source": "community"},
    {"location_name": "Phường Đức Giang", "ward": "duc giang", "district": "Long Biên", "lat": 21.0578, "lng": 105.8956, "event_date": date(2022, 9, 20), "severity": "minor", "depth_cm": 18, "duration_hours": 1, "source": "news"},
    {"location_name": "Phường Long Biên", "ward": "long bien", "district": "Long Biên", "lat": 21.0489, "lng": 105.8834, "event_date": date(2021, 8, 12), "severity": "moderate", "depth_cm": 42, "duration_hours": 4, "source": "official"},
    {"location_name": "Phường Thạch Bàn", "ward": "thach ban", "district": "Long Biên", "lat": 21.0445, "lng": 105.9123, "event_date": date(2019, 7, 25), "severity": "severe", "depth_cm": 82, "duration_hours": 9, "source": "community"},

    # === Tây Hồ (7 events) ===
    {"location_name": "Phường Nhật Tân, Tây Hồ", "ward": "nhat tan", "district": "Tây Hồ", "lat": 21.0712, "lng": 105.8234, "event_date": date(2024, 8, 8), "severity": "minor", "depth_cm": 15, "duration_hours": 1, "source": "news"},
    {"location_name": "Phường Tứ Liên", "ward": "tu lien", "district": "Tây Hồ", "lat": 21.0678, "lng": 105.8189, "event_date": date(2024, 7, 25), "severity": "moderate", "depth_cm": 45, "duration_hours": 4, "source": "official"},
    {"location_name": "Phường Quảng An", "ward": "quang an", "district": "Tây Hồ", "lat": 21.0645, "lng": 105.8212, "event_date": date(2023, 9, 2), "severity": "severe", "depth_cm": 72, "duration_hours": 6, "source": "community"},
    {"location_name": "Phường Xuân La", "ward": "xuan la", "district": "Tây Hồ", "lat": 21.0589, "lng": 105.8145, "event_date": date(2023, 8, 22), "severity": "minor", "depth_cm": 20, "duration_hours": 2, "source": "news"},
    {"location_name": "Phường Phú Thượng", "ward": "phu thuong", "district": "Tây Hồ", "lat": 21.0756, "lng": 105.8267, "event_date": date(2022, 7, 10), "severity": "moderate", "depth_cm": 38, "duration_hours": 3, "source": "official"},
    {"location_name": "Phường Bưởi, Tây Hồ", "ward": "buoi", "district": "Tây Hồ", "lat": 21.0534, "lng": 105.8156, "event_date": date(2021, 8, 28), "severity": "severe", "depth_cm": 85, "duration_hours": 8, "source": "community"},
    {"location_name": "Phường Yên Phụ", "ward": "yen phu", "district": "Tây Hồ", "lat": 21.0623, "lng": 105.8345, "event_date": date(2020, 9, 15), "severity": "minor", "depth_cm": 16, "duration_hours": 1, "source": "news"},
]

# ---------------------------------------------------------------------------
# ~120 price records: 20 wards × 3 types, 6 months of history (2023-2024)
# for PriceHistoryChart testing
# ---------------------------------------------------------------------------

_PRICE_BASE = [
    # (ward, district, apt_m2, house_m2, land_m2)
    ("dong da",         "Đống Đa",      55_000_000, 220_000_000, 310_000_000),
    ("kham thien",      "Đống Đa",      48_000_000, 195_000_000, 270_000_000),
    ("trung liet",      "Đống Đa",      52_000_000, 210_000_000, 290_000_000),
    ("quang trung",     "Đống Đa",      50_000_000, 200_000_000, 280_000_000),
    ("cau giay",        "Cầu Giấy",     62_000_000, 240_000_000, 330_000_000),
    ("yen hoa",         "Cầu Giấy",     58_000_000, 225_000_000, 315_000_000),
    ("trung hoa",       "Cầu Giấy",     60_000_000, 235_000_000, 320_000_000),
    ("kim ma",          "Ba Đình",      72_000_000, 280_000_000, 420_000_000),
    ("lieu giai",       "Ba Đình",      68_000_000, 265_000_000, 395_000_000),
    ("thanh xuan trung","Thanh Xuân",   45_000_000, 185_000_000, 255_000_000),
    ("nhan chinh",      "Thanh Xuân",   47_000_000, 190_000_000, 260_000_000),
    ("khuong mai",      "Thanh Xuân",   44_000_000, 180_000_000, 248_000_000),
    ("my dinh ii",      "Nam Từ Liêm",  42_000_000, 175_000_000, 240_000_000),
    ("my dinh i",       "Nam Từ Liêm",  44_000_000, 182_000_000, 248_000_000),
    ("trung van",       "Nam Từ Liêm",  38_000_000, 160_000_000, 218_000_000),
    ("tuong mai",       "Hoàng Mai",    40_000_000, 168_000_000, 228_000_000),
    ("giap bat",        "Hoàng Mai",    38_000_000, 158_000_000, 215_000_000),
    ("bach mai",        "Hai Bà Trưng", 52_000_000, 208_000_000, 285_000_000),
    ("ngoc lam",        "Long Biên",    36_000_000, 152_000_000, 205_000_000),
    ("nhat tan",        "Tây Hồ",       65_000_000, 255_000_000, 380_000_000),
]

_MONTHS = [
    date(2023, 7, 1), date(2023, 9, 1), date(2023, 11, 1),
    date(2024, 1, 1), date(2024, 3, 1), date(2024, 5, 1),
]

PRICE_RECORDS = []
for _month_idx, _month in enumerate(_MONTHS):
    for _ward, _district, _apt, _house, _land in _PRICE_BASE:
        # Slight upward drift each period (~1.5% per 2 months)
        _factor = 1 + _month_idx * 0.015
        PRICE_RECORDS.extend([
            {
                "ward": _ward, "district": _district,
                "property_type": "apartment",
                "price_per_m2_vnd": int(_apt * _factor),
                "recorded_month": _month,
                "last_updated": _month.strftime("%Y-%m-%d"),
                "source": "batdongsan.com.vn",
            },
            {
                "ward": _ward, "district": _district,
                "property_type": "house",
                "price_per_m2_vnd": int(_house * _factor),
                "recorded_month": _month,
                "last_updated": _month.strftime("%Y-%m-%d"),
                "source": "batdongsan.com.vn",
            },
            {
                "ward": _ward, "district": _district,
                "property_type": "land",
                "price_per_m2_vnd": int(_land * _factor),
                "recorded_month": _month,
                "last_updated": _month.strftime("%Y-%m-%d"),
                "source": "batdongsan.com.vn",
            },
        ])


def seed(database_url: str | None = None) -> dict:
    url = database_url or os.environ.get(
        "DATABASE_SYNC_URL",
        "postgresql+psycopg2://flood:flood@localhost:5432/flooddb",
    )
    engine = create_engine(url)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM flood_events_review"))
        conn.execute(text("DELETE FROM flood_events"))
        conn.execute(text("DELETE FROM price_records"))

        for ev in FLOOD_EVENTS:
            conn.execute(
                text("""
                    INSERT INTO flood_events
                        (location_name, ward, district, lat, lng, geom,
                         event_date, severity, depth_cm, duration_hours, source, is_seed)
                    VALUES (
                        :location_name, :ward, :district, :lat, :lng,
                        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                        :event_date, :severity, :depth_cm, :duration_hours, :source, TRUE
                    )
                """),
                {**ev, "source_url": ev.get("source_url")},
            )

        for pr in PRICE_RECORDS:
            conn.execute(
                text("""
                    INSERT INTO price_records
                        (ward, district, property_type, price_per_m2_vnd,
                         recorded_month, last_updated, source, data_caveat, is_seed)
                    VALUES (
                        :ward, :district, :property_type, :price_per_m2_vnd,
                        :recorded_month, :last_updated, :source,
                        'Listing price, not confirmed sale price', TRUE
                    )
                """),
                pr,
            )

    engine.dispose()
    log.info("Seed complete: %d flood events, %d price records",
             len(FLOOD_EVENTS), len(PRICE_RECORDS))
    return {"flood_events": len(FLOOD_EVENTS), "price_records": len(PRICE_RECORDS)}


if __name__ == "__main__":
    result = seed()
    log.info("Done: %s", result)
