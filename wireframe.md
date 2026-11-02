# FIR-11 Wireframes — Search UI + Vietnamese Fuzzy Search

> **Jira:** FIR-11 | **Status:** To Do | **Date:** 2026-04-12
>
> Điểm vào chính của app. Người dùng tìm kiếm phường/ngõ → bản đồ center + highlight flood zones 500m.

---

## Thông tin chung

| Item | Value |
|---|---|
| Route | `web/app/page.tsx` (Home) |
| Components | `SearchBar`, `WardSuggestions`, `FloodMap`, `FloodMarker` |
| Stack | Next.js 14, shadcn/ui, Google Maps SDK, Fuse.js |
| Breakpoints | Mobile-first: `sm` 640px, `md` 768px, `lg` 1024px |
| Language | Vietnamese UI |

---

## Wireframe A — Floating Search (Centered, Google Maps Style)

**Concept:** Search bar nổi trên bản đồ full-screen. Dropdown hiện xuống bên dưới search khi gõ.

### State 1: Default (chưa gõ)

```
┌──────────────────────────────────────────────────────────────┐
│  🌊 Flood Risk Hà Nội                              ★ logo   │  ← navbar tối giản, h-12
├──────────────────────────────────────────────────────────────┤
│                                                              │
│       ┌──────────────────────────────────────────────┐      │
│       │ 🔍  Nhập tên phường, ngõ...  (VD: Tây Sơn)  │      │  ← Input, shadow-lg, rounded-full
│       └──────────────────────────────────────────────┘      │
│                                                              │
│                                                              │
│                    [ GOOGLE MAP ]                            │
│              (100vw × calc(100vh - 48px))                    │
│                                                              │
│              📍  ← flood marker (blue circle)                │
│         📍        📍                                         │
│                                                              │
│                                                              │
│                                               ┌───┐          │
│                                               │ + │          │  ← map controls
│                                               ├───┤          │
│                                               │ − │          │
│                                               ├───┤          │
│                                               │ ⊙ │          │
│                                               └───┘          │
└──────────────────────────────────────────────────────────────┘
```

### State 2: Đang gõ "tay son" — dropdown mở

```
┌──────────────────────────────────────────────────────────────┐
│  🌊 Flood Risk Hà Nội                              ★ logo   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│       ┌──────────────────────────────────────────────┐      │
│       │ 🔍  tay son                              [×] │      │  ← clear button
│       ├──────────────────────────────────────────────┤      │  ← dropdown attached
│       │  📍  Tây Sơn, Đống Đa            🔴 5 vùng  │      │  ← item selected/hover
│       │  📍  Tây Hồ, Tây Hồ             🟡 2 vùng  │      │
│       │  📍  Tây Mỗ, Nam Từ Liêm        🟢 0 vùng  │      │
│       │  📍  Tây La, Hà Đông            🟢 0 vùng  │      │
│       │  📍  Tây Tựu, Bắc Từ Liêm       🟡 1 vùng  │      │
│       └──────────────────────────────────────────────┘      │  ← max 5 items
│                                                              │
│                    [ GOOGLE MAP ] (dim: 0.6 opacity)         │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### State 3: Đã chọn kết quả — map panned, flood zones highlighted

```
┌──────────────────────────────────────────────────────────────┐
│  🌊 Flood Risk Hà Nội                              ★ logo   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│       ┌──────────────────────────────────────────────┐      │
│       │ 🔍  Tây Sơn, Đống Đa                    [×] │      │
│       └──────────────────────────────────────────────┘      │
│                                                              │
│                    [ GOOGLE MAP ]                            │
│                                                              │
│              ┌───────────────┐                               │
│              │  500m radius  │  ← blue circle overlay        │
│              │   📍 📍 📍   │  ← flood markers inside        │
│              └───────────────┘                               │
│                                                              │
│       ┌──────────────────────────────────────────────┐      │
│       │  🔴 Tây Sơn — 5 vùng ngập lụt  [Xem báo cáo →]│  │  ← info chip bottom-center
│       └──────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────┘
```

### State 4: No-result

```
│       ┌──────────────────────────────────────────────┐      │
│       │ 🔍  abcxyz123                           [×] │      │
│       ├──────────────────────────────────────────────┤      │
│       │  ⚠️  Không tìm thấy địa điểm.               │      │
│       │      Thử tên phường (VD: Đống Đa) hoặc quận. │      │
│       └──────────────────────────────────────────────┘      │
```

**shadcn/ui mapping:**
- `Command` (cmdk) — wrapper cho search + dropdown
- `CommandInput` — input field với icon tìm kiếm
- `CommandList` → `CommandItem` × 5 — result items
- `CommandEmpty` — no-result state
- `Badge` (`variant="destructive"/"secondary"`) — flood count pill
- Map info chip: `Card` + `Button variant="default"`

---

## Wireframe B — Left Sidebar + Map Split

**Concept:** Sidebar cố định bên trái (30%) chứa search + kết quả + CTA. Bản đồ chiếm 70% còn lại.

### State 1: Default

```
┌──────────────────────────────────────────────────────────────┐
│  🌊 Flood Risk Hà Nội                                       │  ← full-width header
├───────────────────┬──────────────────────────────────────────┤
│  SIDEBAR (30%)    │         MAP (70%)                        │
│  ─────────────    │                                          │
│                   │                                          │
│  Tìm kiếm địa điểm│                                          │
│  ┌───────────────┐│                                          │
│  │🔍 Nhập phường││         [ GOOGLE MAP ]                   │
│  └───────────────┘│                                          │
│                   │                                          │
│  ─────────────    │              📍                          │
│                   │          📍      📍                      │
│  Chưa tìm kiếm   │                                          │
│  Nhập tên phường  │                                          │
│  để bắt đầu.      │                                          │
│                   │                              ┌───┐       │
│                   │                              │ + │       │
│                   │                              ├───┤       │
│                   │                              │ − │       │
│                   │                              └───┘       │
└───────────────────┴──────────────────────────────────────────┘
```

### State 2: Đang gõ — dropdown trong sidebar

```
┌───────────────────┬──────────────────────────────────────────┤
│  SIDEBAR          │         MAP                              │
│                   │                                          │
│  ┌───────────────┐│                                          │
│  │🔍 tay son [×]││         [ GOOGLE MAP ]                   │
│  ├───────────────┤│                                          │
│  │📍 Tây Sơn    ││                                          │
│  │   Đống Đa    ││                                          │
│  │   🔴 5 vùng  ││                                          │
│  ├───────────────┤│                                          │
│  │📍 Tây Hồ    ││                                          │
│  │   Tây Hồ    ││                                          │
│  │   🟡 2 vùng  ││                                          │
│  ├───────────────┤│                                          │
│  │📍 Tây Mỗ    ││                                          │
│  │   Nam Từ Liêm││                                          │
│  │   🟢 0 vùng  ││                                          │
│  └───────────────┘│                                          │
└───────────────────┴──────────────────────────────────────────┘
```

### State 3: Đã chọn — sidebar hiện chi tiết + CTA

```
┌───────────────────┬──────────────────────────────────────────┤
│  SIDEBAR          │         MAP                              │
│                   │                                          │
│  ┌───────────────┐│                                          │
│  │🔍 Tây Sơn [×]││     ┌──────────────┐                    │
│  └───────────────┘│     │  500m radius │                    │
│                   │     │  📍 📍 📍   │                    │
│  ── Kết quả ──    │     └──────────────┘                    │
│                   │                                          │
│  📍 Tây Sơn       │                                          │
│     Đống Đa       │                                          │
│     🔴 5 vùng     │                                          │
│     ngập lụt      │                                          │
│                   │                                          │
│  ┌───────────────┐│                                          │
│  │[Xem báo cáo →]││                                          │
│  │   35.000 VND  ││                                          │
│  └───────────────┘│                                          │
│                   │                                          │
│  ← Tìm kiếm mới   │                                          │
└───────────────────┴──────────────────────────────────────────┘
```

**shadcn/ui mapping:**
- `Sheet side="left"` hoặc CSS `aside` cố định
- `Input` + icon `Search` từ lucide-react
- `ScrollArea` — cho result list (overflow khi nhiều kết quả)
- `Card` — mỗi result item
- `Separator` — giữa các items
- `Button size="lg"` — CTA "Xem báo cáo"
- `Badge` — flood count

---

## Wireframe C — Top Bar + Bottom Sheet (Mobile-First) ⭐ KHUYẾN NGHỊ

**Concept:** Search bar full-width cố định trên đầu. Bản đồ chiếm toàn màn hình còn lại. Kết quả hiện dưới dạng bottom sheet (mobile) hoặc panel nổi (desktop). Pattern giống Google Maps Mobile + Grab.

### State 1: Default (mobile — 375px)

```
┌───────────────────────────────────┐
│  🌊 Flood Risk Hà Nội   [menu≡]  │  ← h-12, bg-white, shadow-sm
├───────────────────────────────────┤
│ ┌─────────────────────────────┐   │
│ │🔍 Nhập tên phường, ngõ...  │   │  ← h-10, rounded-full, mx-3, my-2
│ └─────────────────────────────┘   │
├───────────────────────────────────┤
│                                   │
│                                   │
│         [ GOOGLE MAP ]            │
│      (fills remaining height)     │
│                                   │
│      📍                           │
│           📍   📍                 │
│                                   │
│                         ┌───┐     │
│                         │ + │     │
│                         ├───┤     │
│                         │ − │     │
│                         └───┘     │
│                                   │
└───────────────────────────────────┘
  (bottom sheet — collapsed)
```

### State 2: Đang gõ — dropdown ngay bên dưới search bar

> **Spec v2 (2026-04-12):** Dropdown KHÔNG hiển thị flood count. Chỉ hiện ward name + district + chỉ báo có/không dữ liệu. Map dim, không hiển thị flood markers khi dropdown mở.

```
┌───────────────────────────────────┐
│  🌊 Flood Risk Hà Nội   [menu≡]  │
├───────────────────────────────────┤
│ ┌─────────────────────────────┐   │
│ │🔍 tay son               [×]│   │
│ └─────────────────────────────┘   │
│ ┌─────────────────────────────┐   │
│ │📍 Tây Sơn · Đống Đa   ✓   │   │  ← "✓ Có dữ liệu" — KHÔNG hiện count
│ ├─────────────────────────────┤   │    bg-white, shadow-lg, z-50
│ │📍 Tây Hồ · Tây Hồ     ✓   │   │  ← ✓ = has_flood_data: true
│ ├─────────────────────────────┤   │
│ │📍 Tây Mỗ · Nam Từ Liêm  —  │   │  ← "—" = has_flood_data: false
│ ├─────────────────────────────┤   │
│ │📍 Tây La · Hà Đông      —  │   │
│ ├─────────────────────────────┤   │
│ │📍 Tây Tựu · Bắc Từ Liêm ✓  │   │
│ └─────────────────────────────┘   │
│                                   │
│   [ MAP — dimmed 0.5 opacity ]    │  ← flood markers ẩn khi dropdown mở
│   (no flood markers visible)      │
└───────────────────────────────────┘
```

### State 3: Đã chọn — map pan + bottom sheet slide up

```
┌───────────────────────────────────┐
│  🌊 Flood Risk Hà Nội   [menu≡]  │
├───────────────────────────────────┤
│ ┌─────────────────────────────┐   │
│ │📍 Tây Sơn, Đống Đa      [×]│   │  ← filled with selection
│ └─────────────────────────────┘   │
│                                   │
│                                   │
│         [ GOOGLE MAP ]            │
│                                   │
│       ┌───────────────┐           │
│       │  500m radius  │           │  ← ST_DWithin circle overlay
│       │  📍 📍 📍    │           │
│       └───────────────┘           │
│                                   │
├╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┤
│ ▔▔▔▔▔▔▔ (drag handle) ▔▔▔▔▔▔▔   │  ← bottom sheet (snap to ~40vh)
│                                   │
│  📍 Tây Sơn · Đống Đa             │  ← ward name + district only
│  ✓ Chúng tôi có dữ liệu cho       │  ← has_flood_data signal
│    khu vực này (2019–2024)        │  ← data_year_range (KHÔNG hiện count)
│                                   │
│  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │  ← blurred content placeholder
│  ░░ Xem đầy đủ sau khi mua ░░░░░  │  ← NO event details pre-payment
│  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │  ← NO source attribution
│                                   │
│ ┌─────────────────────────────┐   │
│ │   🛒 Xem báo cáo đầy đủ    │   │  ← Button primary (blue)
│ │         35.000 VND          │   │
│ └─────────────────────────────┘   │
└───────────────────────────────────┘
```

> **Spec v2 (2026-04-12):** Bottom sheet KHÔNG hiển thị: (1) flood zone count, (2) event details (date/severity/depth), (3) nguồn thông tin (source_url). Chỉ hiện ward name, district, has_flood_data signal + data_year_range, và CTA.

### State 4: No-result

```
│ ┌─────────────────────────────┐   │
│ │🔍 abcxyz123             [×]│   │
│ └─────────────────────────────┘   │
│ ┌─────────────────────────────┐   │
│ │ ⚠️  Không tìm thấy địa điểm│   │
│ │                             │   │
│ │  Thử: "Đống Đa", "Hoàn     │   │
│ │  Kiếm", "Cầu Giấy"         │   │
│ └─────────────────────────────┘   │
```

### Responsive Desktop (lg ≥ 1024px) — Bottom sheet → Left panel

```
┌──────────────────────────────────────────────────────────────┐
│  🌊 Flood Risk Hà Nội                                       │
├──────────────────────────────────────────────────────────────┤
│ ┌────────────────────────────────────────────────────────┐   │
│ │🔍 Nhập tên phường, ngõ... (VD: Tây Sơn, Đống Đa)  [×]│   │  ← full-width search
│ └────────────────────────────────────────────────────────┘   │
├──────────────────────────┬───────────────────────────────────┤
│  RESULT PANEL (lg:w-80)  │      MAP (flex-1)                 │
│  ─────────────────────   │                                   │
│                          │                                   │
│  📍 Tây Sơn              │       ┌───────────────┐          │
│     Đống Đa              │       │  500m radius  │          │
│     ✓ Có dữ liệu         │       │  📍 📍 📍    │          │
│     (2019–2024)          │       └───────────────┘          │
│                          │                                   │
│  ░░░░░░░░░░░░░░░░░░░░░   │                                   │
│  ░ Xem sau khi mua ░░░   │                                   │
│  ░░░░░░░░░░░░░░░░░░░░░   │                                   │
│                          │                                   │
│  ┌────────────────────┐  │                                   │
│  │ 🛒 Xem báo cáo đầy │  │                                   │
│  │     35.000 VND     │  │                                   │
│  └────────────────────┘  │                                   │
│                          │                       ┌───┐       │
│                          │                       │ + │       │
└──────────────────────────┴───────────────────────┴───┴───────┘
```

**shadcn/ui mapping:**
- `Command` (cmdk) — search input + dropdown autocomplete
- `CommandInput`, `CommandList`, `CommandItem`, `CommandEmpty`
- `Sheet` (`side="bottom"`, snapPoints) → on desktop: `Card` panel absolute left
- `Badge` (`variant="destructive"` cho 🔴, `"secondary"` cho 🟡/🟢)
- `Button size="lg" variant="default"` — CTA "Xem báo cáo"
- `Skeleton` — loading state cho dropdown items
- `Separator` — giữa result items

---

## Heuristic Evaluation

### Nielsen's 10 Heuristics × 3 Wireframes

| Heuristic | Wireframe A | Wireframe B | Wireframe C |
|---|---|---|---|
| **1. Visibility of system status** | ⚠️ Loading state ẩn sau dropdown | ✅ Sidebar rõ ràng trạng thái | ✅ Dropdown + bottom sheet rõ |
| **2. Match real world** | ✅ Google Maps pattern quen thuộc | ⚠️ Sidebar pattern ít phổ biến tại VN | ✅ Google Maps Mobile + Grab (quen VN) |
| **3. User control & freedom** | ✅ Nút [×] clear | ✅ Nút clear + "← Tìm mới" | ✅ Nút [×] + drag down để đóng sheet |
| **4. Consistency & standards** | ✅ Follows Google Maps web | ❌ Sidebar lạ với mobile-first UX | ✅ Bottom sheet = mobile standard |
| **5. Error prevention** | ⚠️ Dropdown đè bản đồ, khó đánh giá | ✅ Kết quả + map đồng thời visible | ✅ Dropdown compact, không che map nhiều |
| **6. Recognition over recall** | ✅ Search placeholder gợi ý | ✅ Empty state rõ ràng | ✅ Suggestions trong CommandEmpty |
| **7. Flexibility & efficiency** | ⚠️ Không có shortcut | ✅ Power user thấy được nhiều kết quả | ✅ Keyboard-friendly (Command) |
| **8. Aesthetic & minimalist** | ✅ Clean, tối giản | ❌ Sidebar chiếm 30% không gian | ✅ Map-first, minimal chrome |
| **9. Help users recognize errors** | ✅ No-result inline | ✅ No-result trong sidebar | ✅ No-result inline + suggestion |
| **10. Help & documentation** | ❌ Không có hướng dẫn onboarding | ⚠️ "Chưa tìm kiếm" text đơn giản | ✅ Placeholder và empty state đủ rõ |

### Accessibility Audit

| Criterion | Wireframe A | Wireframe B | Wireframe C |
|---|---|---|---|
| **Keyboard navigation** | ⚠️ Map focus trap risk | ✅ Sidebar tab order tốt | ✅ Command có keyboard nav sẵn |
| **ARIA landmarks** | ⚠️ Float layout khó map | ✅ `<aside>` + `<main>` rõ | ✅ `<header>` + `<main>` + `role="dialog"` cho sheet |
| **Focus management** | ❌ Sau chọn kết quả, focus không rõ | ✅ Focus về sidebar sau chọn | ✅ Focus vào sheet sau chọn, back khi đóng |
| **Color contrast** | ⚠️ Map overlay mờ | ✅ Sidebar nền trắng | ✅ Dropdown + sheet nền trắng |
| **Touch targets (44px)** | ⚠️ Dropdown items có thể nhỏ | ✅ Card items đủ lớn | ✅ CommandItem min-h-11, Sheet handle lớn |
| **Screen reader** | ⚠️ "Floating" announcement khó | ✅ List semantics rõ | ✅ `CommandList` có role="listbox" |

### Design Consistency (với design system hiện tại)

> **Lưu ý:** Frontend chưa có file nào. Đây là wireframe đầu tiên → Wireframe C trở thành baseline cho design system.

| Dimension | Wireframe A | Wireframe B | Wireframe C |
|---|---|---|---|
| shadcn/ui alignment | ✅ Command component | ✅ Sheet + Card | ✅ Command + Sheet (best fit) |
| Tailwind utilities | ✅ | ✅ | ✅ |
| Mobile-first | ❌ Desktop pattern | ❌ Desktop pattern | ✅ Mobile-first |
| Vietnamese content | ⚠️ Overlay text ngắn | ✅ Sidebar có đủ space | ✅ Sheet có space cho VI text dài |
| Payment CTA visibility | ❌ CTA nhỏ + ẩn | ✅ CTA rõ trong sidebar | ✅ CTA prominent trong sheet |

---

## Kết luận — Wireframe được chọn

### ⭐ CHỌN: Wireframe C — Top Bar + Bottom Sheet

**Lý do:**

1. **Mobile-first phù hợp thị trường VN** — >70% traffic bản đồ tại Việt Nam đến từ mobile. Pattern bottom sheet là chuẩn của Google Maps Mobile và Grab mà người dùng VN đã quen.

2. **Map-first experience được bảo toàn** — Bản đồ chiếm toàn màn hình còn lại, không bị sidebar hay dropdown lớn che khuất. Core value prop (nhìn thấy flood zones) không bị hy sinh.

3. **Dropdown compact + sheet rõ ràng** — Dropdown chỉ hiện khi gõ, nằm ngay dưới search bar, không đè bản đồ quá nhiều. Sau khi chọn, sheet slide up cung cấp đủ thông tin + CTA.

4. **shadcn/ui `Command` + `Sheet` là fit tốt nhất** — `Command` (cmdk) có sẵn fuzzy search, keyboard navigation, `CommandEmpty` cho no-result. `Sheet` với `snapPoints` có sẵn drag handle, focus management, ARIA.

5. **Responsive tự nhiên** — Mobile: bottom sheet. Desktop (lg): sheet chuyển thành panel cố định bên trái. Không cần viết lại layout logic.

6. **CTA "Xem báo cáo" prominent** — Sheet có đủ không gian để hiện preview data + Button lớn → conversion rate cao hơn so với chip nhỏ của Wireframe A.

---

## Component Tree (Wireframe C)

```
page.tsx (Home)
├── <Header />                          ← h-12, logo + app name
├── <SearchSection />                   ← relative, z-10
│   ├── <Command>                       ← shadcn/ui Command
│   │   ├── <CommandInput />            ← with Search icon + clear button
│   │   └── <CommandList>              ← absolute, overlay, z-50
│   │       ├── <CommandItem /> × 5    ← ward name + district + FloodBadge
│   │       └── <CommandEmpty />       ← no-result message
│   └── <FloodBadge />                 ← Badge (color by severity)
├── <FloodMap />                        ← flex-1, Google Maps SDK
│   ├── <FloodMarker /> × N            ← per flood event
│   └── <RadiusCircle />               ← ST_DWithin 500m overlay
└── <ResultSheet />                     ← shadcn/ui Sheet, side="bottom"
    ├── <SheetHeader>                   ← ward name + district
    ├── <FloodSummary />               ← count, latest event, severity
    └── <Button size="lg">             ← "Xem báo cáo đầy đủ — 35.000 VND"
```

---

## API Response Shape (v2 — 2026-04-12)

```typescript
// GET /api/search?q=tay+son
{
  "results": [
    {
      "ward": "tay son",
      "ward_vi": "Tây Sơn",
      "district": "Đống Đa",
      "lat": 21.0245,
      "lng": 105.8412,
      "has_flood_data": true,           // ← NEW: bool (thay flood_zone_count)
      "data_year_range": "2019–2024"    // ← NEW: MIN/MAX event_date, null nếu no data
      // flood_zone_count: REMOVED      ← tránh lộ số lượng
      // teaser_events: REMOVED         ← không hiện event details pre-payment
      // source / source_url: REMOVED   ← ẩn nguồn dữ liệu
    }
  ]
}
```

## Data Flow

```
User types "tay son"
  → debounce 300ms
  → normalize_vi("tay son") on client (lib/normalize.ts)
  → Fuse.js local match (instant)  ←── cached ward list (ward_vi, district only)
  → GET /api/search?q=tay+son      ←── server authoritative
  → merge results (server wins)
  → render CommandItems (ward + district + ✓/— indicator)
  → flood markers HIDDEN while dropdown open

User selects "Tây Sơn"
  → dropdown closes
  → map.panTo({ lat, lng })
  → map.setZoom(15)
  → draw RadiusCircle(500m)
  → flood markers re-appear
  → Sheet.open() with: ward name, district, has_flood_data, data_year_range, CTA
  → NO event details, NO source shown

User taps "Xem báo cáo"
  → PaymentModal opens (FIR-12)
```

---

## Acceptance Criteria Mapping

| AC | Wireframe C coverage |
|---|---|
| "tay son" → "Tây Sơn" trong dropdown | ✅ CommandItem hiện kết quả normalize |
| "dong da" → "Đống Đa" | ✅ Fuse.js + API fallback |
| "TAY SON" hoa → đúng kết quả | ✅ normalize_vi() toLowerCase trước search |
| Debounce — không gọi API mỗi keystroke | ✅ 300ms debounce trong SearchSection |
| Chọn kết quả → map pan | ✅ State 3 — map.panTo() |
| Flood zones 500m được highlight | ✅ RadiusCircle + FloodMarker |
| No-result message | ✅ CommandEmpty |
| Input clear [×] → reset map | ✅ Clear button trong CommandInput |

---

> **Bước tiếp theo:** Confirm wireframe này → FIR-11 implementation với `/frontend-design` skill.
>
> **File này:** `wireframe.md` tại root của project. Xóa sau khi confirm + merge.
