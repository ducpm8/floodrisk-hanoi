import re
import unicodedata


def clean_district(value: str | None) -> str | None:
    """
    Return a usable district label, or None when the value is missing/placeholder.

    The ward seed historically wrote the literal string "Unknown" when OSM had
    no district tag. That leaked straight into API responses (FIR-84), so all
    API read paths funnel district values through this helper.

    >>> clean_district("Đống Đa")
    'Đống Đa'
    >>> clean_district("  unknown ")
    >>> clean_district("")
    >>> clean_district(None)
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() == "unknown":
        return None
    return cleaned


def normalize_ward(name: str) -> str:
    """
    Normalize a Vietnamese ward name for database lookup.

    Strips diacritics and converts to lowercase ASCII so that
    "Đống Đa", "dong da", "DONG DA", "Dong Da" all resolve to "dong da".

    Called at:
      - Seed time (ward_centroids.ward_name)
      - Every ward lookup in api/routers/search.py before DB query

    >>> normalize_ward("Đống Đa")
    'dong da'
    >>> normalize_ward("PHƯỜNG CẦU GIẤY")
    'phuong cau giay'
    >>> normalize_ward("dong-da")
    'dong da'
    """
    # đ/Đ (U+0110/U+0111) don't decompose via NFKD — replace before normalization
    name = name.replace("Đ", "D").replace("đ", "d")
    nfkd = unicodedata.normalize("NFKD", name.lower())
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]", " ", ascii_str).strip()
