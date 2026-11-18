# DataFlood import — data completeness & unknown-depth report

Snapshot: 2026-11-04. Scope: `flood_events` rows with `resolution_level = 'dataflood_import'` (10,533 rows, imported from `D:\MMO\DataFlood\flood_points.csv` + `sources_master.csv`). Dev DB `flooddb`, migration head `023`.

## 1. Completeness of the 10,533 imported rows

| Field | Filled | Missing | Notes |
|---|---|---|---|
| `location_name`, `event_date`, `source_url`, `cause` | 10,533 (100%) | 0 | Always present from the source |
| `source` | 10,533 | 0 | news 9,311 · community 956 · official 266 |
| `district` | 9,363 (88.9%) | 1,170 | 7 values are multi-district strings, e.g. "Quận Hoàn Kiếm, Ba Đình" |
| `ward` | 2,837 (26.9%) | 7,696 | 687 filled wards are not in `ward_centroids` |
| `lat` / `lng` / `geom` | **0 (0%)** | 10,533 | No coordinates at all |
| `severity` | 3,968 (37.7%) | 6,565 | |
| `depth_cm` | 2,454 (23.3%) | 8,079 | See section 2 |
| `duration_hours` | 0 | 10,533 | Not in the source |

### Location resolution

| Group | Rows | Share |
|---|---|---|
| Ward + district | 2,780 | 26.4% |
| District only (no ward) | 6,583 | 62.5% |
| Ward only (no district) | 57 | 0.5% |
| Neither | 1,113 | 10.6% |

Neither/unplaced rows are mostly Đại lộ Thăng Long tunnels and service roads, long highways (QL32, QL6, QL3, Đường tỉnh 421B/422/423), and generic river/floodplain or city-wide entries. Streets that need a decision: Nguyễn Gia Bồng (30 rows), Đặng Trần Đức (19), Phạm Hùng (12), Phạm Văn Đồng (11), Chiến Thắng (10), Lê Trọng Tấn (10).

### Ward centroid coverage after the 2025 admin reform (updated 2026-11-08)

Nghị quyết 1656/NQ-UBTVQH15 (effective 2025-07-01) replaced Hà Nội's old commune/ward units with **126 units (51 phường, 75 xã)**. `ward_centroids` now holds **563 rows**: the 519 pre-2025 OSM rows plus 44 new names (82 of the 126 names already existed in the OSM set). Seeded by `api/seeds/seed_wards_2025.py --apply`; dataset `api/seeds/data/hanoi_wards_2025.json`.

- Names/type: Nghị quyết 1656/NQ-UBTVQH15 (S2 `fullName` matches the official list; 9 urban units are labelled "Xã" in some articles but are "Phường" in the dataset).
- Coordinates: representative point of each polygon from `thanglequoc/vietnamese-provinces-database` v5.2.0 (MIT, generated 2026-09-20) — guaranteed inside the polygon, unlike a concave-shape centroid.
- Old → new names: 108 `location_aliases` rows (`source='manual'`, confidence 0.9) from `quangtam/vietnam-address-database` ward mappings, only for old names that the four-strategy lookup could not resolve. 25 old names map to more than one new unit; the 2 that no existing row already resolves (`phuong nga tu so`, `xa dai hung`) are skipped as ambiguous.

Result: unresolved coordinate-less `ward` values dropped from **25 values / 110 rows** to **3 values / 3 rows** (`xa phu le`, `xa phu quang`, `xa yen lac` — typos or out-of-Hà-Nội names).

Manual review items:
- 2 pre-existing rows keep their old centroid because the PK is `ward_name`: `xa da phuc` ↔ "Sóc Sơn" vs new Xã Đa Phúc (7.5 km apart) and `xa o dien` ↔ "Đan Phượng" vs new Xã Ô Diên (4.5 km apart).
- 3 new centroids are >5 km from the median of their *known* predecessor rows: Phường Hoàng Liệt (Tam Hiệp row is the Sóc Sơn unit, not Thanh Trì), Xã Vân Đình (only `xa dong tien` matches, which is the Sóc Sơn "Đông Tiến"), Xã Hoà Xá (`xa thai hoa` matches the Ba Vì unit). The new polygons are consistent with the 2025 boundaries; the flags come from pre-existing same-name collisions, not from the new coordinates.

### How trustworthy is the ward/district value

Ward and district came partly from the source and partly from inference. `resolution_confidence` marks which:

| `resolution_confidence` | Rows | Origin |
|---|---|---|
| NULL | 2,441 | Given by the source |
| 0.80 | 1,945 | Propagated from identical location names |
| 0.70 | 47 | First hand-mapped batch |
| 0.65 | 916 | Hand-mapped |
| 0.60 | 3,390 | Rule-based (street/landmark dictionary) |
| 0.55 | 1,794 | Pair-fill (ward→district or district→ward) |

Only 23% of rows carry a source-given location; the rest rely on inference.

### Other open issues

- 28 duplicate groups (same source URL, date and name) not reviewed.
- 7 multi-district strings not normalised.
- 57 ward-only rows have no confident district (phuong hong ha 25, xa duong hoa 10, phuong van mieu quoc tu giam 6, xa hung dao 5, xa phuc son 3, xa hong son 2, xa phu dong 1, xa doai phuong 1).
- Source columns not stored: description, street, segment, images, notes.

## 2. Depth

### Current state

| `depth_basis` | Rows | Meaning |
|---|---|---|
| measured | 2,214 | Number given by the source (includes 4 values restored from `90.0`-style cells) |
| extracted | 170 | Number parsed from the description text |
| estimated | 181 | Mapped from a body/vehicle reference |
| NULL | 8,124 table-wide, of which 8,079 are imported rows | No depth |

`depth_basis` (migration 023) is NULL exactly when `depth_cm` is NULL. Estimated values are not measurements and are not displayed as such yet (API/UI do not expose `depth_basis`).

Severity × depth (imported rows):

| Severity | Rows | With depth |
|---|---|---|
| minor | 534 | 424 |
| moderate | 1,298 | 1,192 |
| severe | 2,136 | 777 |
| NULL | 6,565 | 61 |

Rows with neither severity nor depth: **6,504**. Rows with severity but no depth: 1,575.

Depth ranges of rows that have one (cm): minor 6–30 (median 10), moderate 20–80 (median 30), severe 40–400 (median 55).

### Why depth is unknown — classification

Counted on CSV rows (8,154 rows still empty after the fill; DB count is 8,079 because multi-ward rows expand and some rows de-duplicate). Classes come from keyword rules, not manual review.

| Class | Meaning | Rows | Share | Treatment |
|---|---|---|---|---|
| **N/A** | Risk, forecast or drainage-list entry; depth has no meaning | ~1,518 | 19% | Leave NULL on purpose |
| **U1** | Severity unknown, source says no depth or is an official list | ~4,135 | 51% | Nothing to recover |
| **U2** | Severity unknown, no information at all | ~844 | 10% | Nothing to recover |
| **S1** | Severity known, source says no number | ~866 | 11% | Keep severity only |
| **S2** | Severity known, source silent on depth | ~162 | 2% | Keep severity only |
| **R3** | Qualitative words only ("ngập sâu", "ngập nặng") | ~338 | 4% | Keep qualitative, no number |
| **Excluded from R1/R2** | Number was rainfall, a river/flood-peak level, basement depth, a forecast, a receding amount, a past-event repeat, or an R2 estimate that contradicted severity | ~290 | 4% | Treated as S1 / N/A |

About 96% of the empty rows have no usable depth in the source.

### What was filled (355 rows)

| Group | Rows | Rule |
|---|---|---|
| R1 — number in text | 170 | Upper value of a range; "trên/hơn N" → N; "nửa mét" → 50 |
| R2 — body/vehicle reference | 181 | Highest match in the text, fixed map below |
| `90.0`-style cells | 4 | Parsing fix |

R2 map (cm): mắt cá 10 · nửa bánh/ngập bánh xe máy 15 · bắp chân 25 · nửa bánh ô tô 35 · ngập yên 40 · ngang gối 50 · quá bánh ô tô 50 · quá đầu gối 60 · ngang đùi 70 · ngang bụng/thắt lưng 90 · ngang ngực 120 · ngang cổ/quá đầu 150.

Exclusions applied: rainfall (mm), river/flood-peak levels, basements, forecasts ("cảnh báo/nguy cơ"), receding amounts ("rút N cm"), 262 rows noted "nhắc lại, không phải ghi nhận mới", and 20 R2 rows whose estimate conflicted with severity.

## 3. Rollback

- Depth fills: ids in `depth_fill_changes.json` (scratchpad); set `depth_cm = NULL, depth_basis = NULL` for them.
- `depth_basis` column: `alembic downgrade 022` (drops the column only).
- Location pair-fill: `UPDATE flood_events SET resolution_confidence = NULL WHERE resolution_confidence = 0.55;` clears the marker only; ids in `fill_pair_changes.json` restore values.
- Whole import: `DELETE FROM flood_events WHERE resolution_level = 'dataflood_import';`

## 4. Suggested next steps

1. Geocode / derive coordinates — 100% of imported rows have none, so none can be placed on the map.
2. Show `depth_basis` in the report (e.g. "≈" for estimated).
3. Decide severity for the 6,565 rows with none, or label them "severity unknown".
4. Review the 28 duplicate groups and normalise the 7 multi-district strings.
5. Hand-check ~50 rows per depth class to validate the keyword rules.
