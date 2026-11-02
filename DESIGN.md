# Design System — Hanoi Flood Risk App

> Locked decisions from plan-design-review 2026-05-16. Update this file when decisions change.

---

## Brand

| Token | Value |
|---|---|
| App name | Flood Risk Hà Nội |
| Tagline | Tra cứu rủi ro ngập lụt theo phường |
| Tone | Calm authority — trustworthy, not alarmist |
| Language | Vietnamese UI, English code |

---

## Homepage Hero (locked 2026-05-16)

**Layout:** Full-screen hero. OSM static tile as background + dark overlay. Hero content centered.

### Layer stack (z-index)

| Layer | Element | z-index |
|---|---|---|
| 0 | OSM static map image (`/public/hanoi-hoankiem-map.png`) | absolute inset-0 |
| 1 | Dark overlay `bg-black/55` | absolute inset-0 |
| 10 | FloodMap (google mode only) | absolute inset-0 z-10 |
| 20 | Hero content (badge + h1 + sub + search) | absolute inset-0 z-20 |
| 30 | Nav bar | absolute top-0 z-30 |
| ResultSheet | Bottom sheet (ward detail) | fixed bottom-0 z-40+ |

### Hero copy

```
Badge:  "Hà Nội · 579 phường đang được theo dõi"
H1:     "Nhà bạn có đang trong vùng ngập?"
Sub:    "Tra cứu lịch sử ngập lụt của bất kỳ phường nào tại Hà Nội — trước khi xuống tiền."
Hint:   "Nhập tên phường, ngõ hoặc tên đường bất kỳ tại Hà Nội"
```

### After-search state
Hero stays visible. `ResultSheet` slides up as overlay. No layout transition.

---

## Color palette

| Role | Value | Usage |
|---|---|---|
| Primary accent | `sky-700` `#0369a1` | Search button, links |
| Accent light | `sky-300` `#7dd3fc` | Badge text, highlights on dark bg |
| Accent bg | `sky-400/14` | Badge pill background |
| Overlay | `black/55` | Map overlay for text legibility |
| Text on dark | `white` / `white/70` / `white/40` | Hero h1 / sub / hint |
| Surface | `white` | Search pill, sheets, cards |

---

## Typography

| Element | Style |
|---|---|
| Hero H1 | `text-[2.5rem] font-extrabold leading-tight tracking-tight text-white` (single line, question form) |
| Sub copy | `text-base leading-relaxed text-white/70 max-w-[460px]` (one sentence, hook-style) |
| Badge | `text-xs font-semibold tracking-wide text-sky-300` |
| Hint | `text-xs text-white/40` |
| Body | system-ui (deferred: Be Vietnam Pro — FIR-52) |

---

## Search box variants

### `variant="top"` (default)
White top bar with `border-b border-slate-200 shadow-sm`. Used in admin pages.

### `variant="hero"`
Pill-shaped white box floating over dark bg. `rounded-full bg-white shadow-2xl`.
Includes "Tra cứu" button. cmdk overrides via `.hero-search` CSS class.

---

## Components

| Component | Location | Notes |
|---|---|---|
| SearchSection | `web/components/search/SearchSection.tsx` | Accepts `variant="hero"\|"top"` |
| ResultSheet | `web/components/search/ResultSheet.tsx` | Bottom sheet / lg left panel |
| FloodMap | `web/components/map/FloodMap.tsx` | Google Maps; renders behind hero in google mode |
| ProximityList | `web/components/search/ProximityList.tsx` | Text fallback; not in hero main view |

---

## Search Result Display (locked 2026-05-16, FIR-80)

### Result item anatomy

```
┌──────────────────────────────────────────────────────┐
│ 📍 <location_name>                    [badge]         │
│    <ward_name_vi> · <district>                        │
└──────────────────────────────────────────────────────┘
```

- **`location_name`** — primary bold label. For alias matches: `alias_vi` (e.g. "Royal City", "Đường Nguyễn Trãi"). For direct ward matches: `ward_name_vi`.
- **subtitle** — for alias matches: `ward_name_vi · district`. For direct ward matches: `district` only.
- **district fallback** — `"Unknown"` → renders as `"Hà Nội"`.

### location_type values

| Value | Source | Example |
|---|---|---|
| `ward` | direct ward_centroids hit | "Phường Mỹ Đình I" |
| `street` | location_aliases alias_type=street | "Đường Nguyễn Trãi" |
| `apartment` | location_aliases alias_type=apartment | "Royal City" |
| `landmark` | location_aliases alias_type=landmark | "Hồ Tây" |
| `other` | location_aliases alias_type=other | long specific descriptions |

### API fields

```
SearchResult {
  ward: str           # ward_name_vi — used by ResultSheet detail panel
  ward_normalized: str
  location_name: str  # display name (alias_vi or ward_name_vi)
  location_type: str  # "ward" | "street" | "apartment" | "landmark" | "other"
  district: str
  lat: float
  lng: float
  has_flood_data: bool
  data_year_range: str | None
}
```

---

## Deferred decisions

| Decision | Reason deferred | Target ticket |
|---|---|---|
| Be Vietnam Pro font | Polish pass; system-ui acceptable for v1 | FIR-52 |
| Animated flood markers on bg | Nice-to-have | Backlog |
| DESIGN.md (this file) creation | Was missing; created FIR-52 review | — |
