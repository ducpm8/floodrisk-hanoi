"""
Unit tests for api/utils/text.py — normalize_ward()

No database required. These run in pure Python.
Covers 24 Hanoi ward/district names (FIR-6 AC: >= 20).
"""

import pytest

from api.utils.text import normalize_ward


@pytest.mark.parametrize(
    "input_name, expected",
    [
        # --- Basic diacritics (tone marks) ---
        ("Đống Đa", "dong da"),
        ("Cầu Giấy", "cau giay"),
        ("Thanh Xuân", "thanh xuan"),
        ("Hoàn Kiếm", "hoan kiem"),
        ("Ba Đình", "ba dinh"),
        ("Tây Hồ", "tay ho"),
        ("Long Biên", "long bien"),
        ("Hai Bà Trưng", "hai ba trung"),
        ("Hà Đông", "ha dong"),
        ("Bắc Từ Liêm", "bac tu liem"),
        # --- Case variants ---
        ("DONG DA", "dong da"),
        ("dong da", "dong da"),
        ("Dong Da", "dong da"),
        ("ĐỐNG ĐA", "dong da"),
        # --- With ward/district prefix ---
        ("Phường Đống Đa", "phuong dong da"),
        ("PHƯỜNG CẦU GIẤY", "phuong cau giay"),
        ("Quận Hoàn Kiếm", "quan hoan kiem"),
        # --- Hyphens and punctuation stripped ---
        ("dong-da", "dong da"),
        ("Đống-Đa", "dong da"),
        # --- Numbers preserved ---
        ("Phường 1", "phuong 1"),
        ("ward 12", "ward 12"),
        # --- Compound tones (multi-diacritic chars) ---
        ("Nghĩa Tân", "nghia tan"),
        ("Trương Định", "truong dinh"),
        ("Nhân Chính", "nhan chinh"),
    ],
)
def test_normalize_ward(input_name: str, expected: str) -> None:
    assert normalize_ward(input_name) == expected


def test_normalize_ward_empty_string() -> None:
    assert normalize_ward("") == ""


def test_normalize_ward_only_special_chars() -> None:
    result = normalize_ward("---")
    assert result == ""


def test_normalize_ward_is_idempotent() -> None:
    for name in ["Đống Đa", "Cầu Giấy", "Hoàn Kiếm", "Hai Bà Trưng", "Tây Hồ"]:
        once = normalize_ward(name)
        twice = normalize_ward(once)
        assert once == twice, f"normalize_ward not idempotent for '{name}'"


def test_normalize_ward_whitespace_trimmed() -> None:
    assert normalize_ward("  Đống Đa  ") == "dong da"


def test_normalize_ward_mixed_case_diacritics() -> None:
    # TAY HON — mixed case with tones removed
    assert normalize_ward("TÂY HỒ") == "tay ho"
