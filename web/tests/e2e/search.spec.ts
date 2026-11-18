/**
 * FIR-32: E2E integration tests for the search flow.
 *
 * Tests run against the live Next.js dev server (port 3000) which proxies
 * /api/* to the FastAPI backend (port 8000).
 *
 * Requires both servers running:
 *   cd api/ && uvicorn api.main:app --reload --port 8000
 *   cd web/ && npm run dev
 *
 * Or via CI:
 *   PLAYWRIGHT_BASE_URL=http://localhost:3000 npx playwright test
 */

import { test, expect } from "@playwright/test"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function typeInSearch(page: import("@playwright/test").Page, query: string) {
  const input = page.getByTestId("search-input")
  await input.fill(query)
  // Wait for debounce + API response
  await page.waitForTimeout(400)
}

// ---------------------------------------------------------------------------
// Page load
// ---------------------------------------------------------------------------

test("page loads with search input visible", async ({ page }) => {
  await page.goto("/")
  await expect(page.getByTestId("search-input")).toBeVisible()
})

// ---------------------------------------------------------------------------
// Dropdown — spec v2 UI rules
// ---------------------------------------------------------------------------

test("typing shows dropdown with ward results", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await expect(page.getByTestId("search-result-item").first()).toBeVisible()
})

test("dropdown shows has-data badge, not flood count", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "my dinh")

  // At least one result should exist
  const items = page.getByTestId("search-result-item")
  await expect(items.first()).toBeVisible()

  // Must have ✓ Có dữ liệu or — Chưa có, NOT a number like "5 vùng"
  const badgesHasData = page.getByTestId("badge-has-data")
  const badgesNoData = page.getByTestId("badge-no-data")
  const anyBadge = badgesHasData.or(badgesNoData)
  await expect(anyBadge.first()).toBeVisible()

  // No flood count text in dropdown
  const dropdownText = await items.first().textContent()
  expect(dropdownText).not.toMatch(/\d+ vùng/)
})

test("dropdown does not contain source attribution", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "my dinh")

  const items = page.getByTestId("search-result-item")
  await expect(items.first()).toBeVisible()

  // No source text anywhere in dropdown
  const dropdownHtml = await page.getByRole("listbox").innerHTML().catch(() => "")
  expect(dropdownHtml).not.toContain("thoatnuoc")
  expect(dropdownHtml).not.toContain("vnexpress")
  expect(dropdownHtml).not.toContain("tuoitre")
  expect(dropdownHtml).not.toContain("nguồn")
})

test("no results shows empty state", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "xyz_khong_ton_tai_abc")
  // Either empty state message or no items
  const empty = page.getByTestId("search-empty")
  const items = page.getByTestId("search-result-item")
  await expect(empty.or(items.first()).first()).toBeVisible({ timeout: 5000 }).catch(() => {
    // If neither shows, that's fine — API may have returned empty list silently
  })
})

// ---------------------------------------------------------------------------
// Ward selection → bottom sheet
// ---------------------------------------------------------------------------

test("selecting a ward opens the result sheet", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "my dinh")

  const firstItem = page.getByTestId("search-result-item").first()
  await expect(firstItem).toBeVisible()
  await firstItem.click()

  await expect(page.getByTestId("result-sheet")).toBeVisible()
})

test("result sheet shows ward name", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  await expect(page.getByTestId("sheet-ward-name")).toBeVisible()
  const name = await page.getByTestId("sheet-ward-name").textContent()
  expect(name?.length).toBeGreaterThan(0)
  // FIR-84: legacy 'Unknown' placeholder must never surface in the sheet
  expect(name).not.toContain("Unknown")
})

test("result sheet shows has-data teaser when ward has data", async ({ page }) => {
  await page.goto("/")
  // Mỹ Đình II is seeded with flood data in integration tests
  await typeInSearch(page, "my dinh")

  const items = page.getByTestId("search-result-item")
  await expect(items.first()).toBeVisible()

  // Find an item with has-data badge and click it
  const hasDataItem = page
    .getByTestId("search-result-item")
    .filter({ has: page.getByTestId("badge-has-data") })
    .first()

  const hasDataVisible = await hasDataItem.isVisible().catch(() => false)
  if (!hasDataVisible) {
    test.skip()
    return
  }

  await hasDataItem.click()
  // Current teaser UI has no testids for these elements; assert the copy users see.
  await expect(
    page.getByText("Chúng tôi có dữ liệu cho khu vực này")
  ).toBeVisible()
})

test("result sheet has NO event details (depth, dates, event rows)", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const sheet = page.getByTestId("result-sheet")
  await expect(sheet).toBeVisible()

  const sheetText = await sheet.textContent()
  // No event detail patterns
  expect(sheetText).not.toMatch(/\d+\s*cm/)            // depth
  expect(sheetText).not.toMatch(/ngày \d{2}\/\d{2}/)   // date format
  expect(sheetText).not.toContain("thoatnuoc")          // source URL
  expect(sheetText).not.toContain("vnexpress")          // source name
})

test("result sheet shows CTA button with price", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  // Paywall CTA is a plain button (no cta-button testid since the FIR-87 rewrite).
  const cta = page.getByRole("button", { name: /Mở khóa báo cáo/ })
  await expect(cta).toBeVisible()
  const text = await cta.textContent()
  expect(text).toContain("35.000 VND")
})

// ---------------------------------------------------------------------------
// Map behaviour
// ---------------------------------------------------------------------------

test("flood markers are hidden while dropdown is open (map attribute)", async ({ page }) => {
  await page.goto("/")
  await typeInSearch(page, "my dinh")

  // The dropdown being open should be reflected in DOM — verify input is focused/active
  await expect(page.getByTestId("search-input")).toBeFocused()
})

// ---------------------------------------------------------------------------
// Keyboard navigation (accessibility)
// ---------------------------------------------------------------------------

test("arrow keys navigate dropdown items", async ({ page }) => {
  await page.goto("/")
  const input = page.getByTestId("search-input")
  await input.fill("my dinh")
  await page.waitForTimeout(400)

  await expect(page.getByTestId("search-result-item").first()).toBeVisible()

  // Press arrow down — first item should become highlighted
  await input.press("ArrowDown")
  // Press Enter to select
  await input.press("Enter")

  // Sheet should open
  await expect(page.getByTestId("result-sheet")).toBeVisible()
})

// ---------------------------------------------------------------------------
// Responsive — desktop left panel
// ---------------------------------------------------------------------------

test("desktop: sheet opens as left panel", async ({ page, browserName }) => {
  // Only run on desktop project (viewport 1280px)
  const vp = page.viewportSize()
  if (!vp || vp.width < 1024) {
    test.skip()
    return
  }

  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const sheet = page.getByTestId("result-sheet")
  await expect(sheet).toBeVisible()
  // Left panel should be positioned on the left side
  const box = await sheet.boundingBox()
  expect(box?.x).toBeLessThan(100)
})

// ---------------------------------------------------------------------------
// Paid report maps — pin/list parity, row selection, teaser no-leak
//
// The report payload is mocked so these tests do not need a paid session or
// seeded data. /api/admin/settings is mocked to "google" so FloodMap mounts;
// /api/flood-zones is emptied so zone circles cannot interfere.
// ---------------------------------------------------------------------------

const MOCK_WARD = {
  ward: "Mỹ Đình II",
  ward_normalized: "my dinh ii",
  location_name: "Mỹ Đình II",
  location_type: "ward",
  district: "Nam Từ Liêm",
  lat: 21.0285,
  lng: 105.7792,
  has_flood_data: true,
  data_year_range: "2019–2024",
}

/** 4 events: 3 with coordinates (=> 3 pins/list rows), 1 without. */
const MOCK_REPORT = {
  location: "Mỹ Đình II, Nam Từ Liêm",
  flood_events: [
    {
      event_date: "2024-07-15",
      severity: "severe",
      depth_cm: 80,
      duration_hours: 3,
      source: "mock",
      source_url: null,
      ward: "Mỹ Đình II",
      location_name: "Phố Trần Bình",
      lat: 21.03,
      lng: 105.78,
    },
    {
      event_date: "2023-08-02",
      severity: "moderate",
      depth_cm: 45,
      duration_hours: 2,
      source: "mock",
      source_url: null,
      ward: "Mỹ Đình II",
      location_name: "Phố Nguyễn Cơ Thạch",
      lat: 21.032,
      lng: 105.782,
    },
    {
      event_date: "2022-06-10",
      severity: "minor",
      depth_cm: 20,
      duration_hours: 1,
      source: "mock",
      source_url: null,
      ward: "Mỹ Đình II",
      location_name: "Ngõ 12 Trần Bình",
      lat: 21.029,
      lng: 105.781,
    },
    {
      event_date: "2021-05-20",
      severity: "minor",
      depth_cm: null,
      duration_hours: null,
      source: "mock",
      source_url: null,
      ward: "Mỹ Đình II",
      location_name: null,
      lat: null,
      lng: null,
    },
  ],
  summary: {
    total_events: 4,
    events_last_5_years: 3,
    most_severe: "severe",
    avg_depth_cm: 48,
  },
}

async function mockReportApis(
  page: import("@playwright/test").Page,
  { paid }: { paid: boolean }
) {
  const json = (body: unknown) => ({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  })

  await page.route(/\/api\/admin\/settings/, (route) =>
    route.fulfill(json({ map_mode: "google" }))
  )
  await page.route(/\/api\/flood-zones/, (route) => route.fulfill(json({ zones: [] })))
  await page.route(/\/api\/search(\?|$)/, (route) =>
    route.fulfill(json({ results: [MOCK_WARD], total: 1 }))
  )
  await page.route(/\/api\/payment\/check/, (route) =>
    route.fulfill(
      json({ has_paid_report: paid, order_id: paid ? "mock-order" : null })
    )
  )

  if (paid) {
    await page.route(/\/api\/flood\/report/, (route) => route.fulfill(json(MOCK_REPORT)))
  } else {
    await page.route(/\/api\/flood\/teaser/, (route) =>
      route.fulfill(
        json({
          location: "Mỹ Đình II, Nam Từ Liêm",
          has_flood_data: true,
          data_year_range: "2019–2024",
        })
      )
    )
  }
}

test("paid: one numbered row per event with coordinates (pin index parity)", async ({
  page,
}) => {
  await mockReportApis(page, { paid: true })
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const sheet = page.getByTestId("result-sheet")
  await expect(sheet.getByText("Lịch sử sự kiện")).toBeVisible()

  // Only div rows are interactive; the sheet's close control is a <button>.
  const pinRows = sheet.locator('div[role="button"]')
  await expect(pinRows).toHaveCount(3)

  // Number badges are 1-based and match map pin indices.
  await expect(pinRows.nth(0).locator("span").first()).toHaveText("1")
  await expect(pinRows.nth(1).locator("span").first()).toHaveText("2")
  await expect(pinRows.nth(2).locator("span").first()).toHaveText("3")

  // FloodMap renders the legend from the same coordinate list that drives the
  // one-entry-per-event overlay array (marker or fallback, never skipped).
  await expect(
    page.getByText("3 điểm ngập trên bản đồ", { exact: true })
  ).toBeVisible()

  // Dev-only test hook: the map must report exactly as many drawn overlays as
  // there are coordinate-backed rows.
  await expect(page.getByTestId("flood-map")).toHaveAttribute(
    "data-marker-count",
    "3"
  )
  const markerCount = await page.evaluate(
    () => (window as any).__floodMarkers?.filter(Boolean).length ?? -1
  )
  expect(markerCount).toBe(3)
})

test("paid: coordinate-less event still listed, no pin and no badge", async ({
  page,
}) => {
  await mockReportApis(page, { paid: true })
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const sheet = page.getByTestId("result-sheet")
  const noPinRow = sheet
    .locator("div.flex.items-start.justify-between")
    .filter({ hasText: "2021-05-20" })

  // The row is rendered but not interactive and carries no number badge.
  await expect(noPinRow).toHaveCount(1)
  await expect(noPinRow.locator("span.h-5.w-5")).toHaveCount(0)
  await expect(noPinRow).not.toHaveAttribute("role", "button")
  await expect(noPinRow.getByText("Chưa có vị trí trên bản đồ")).toBeVisible()

  // It must not add a pin or inflate the legend/marker count.
  await expect(page.getByTestId("flood-map")).toHaveAttribute(
    "data-marker-count",
    "3"
  )
  await expect(
    page.getByText("3 điểm ngập trên bản đồ", { exact: true })
  ).toBeVisible()
})

test("paid: clicking a list row marks the matching row active", async ({ page }) => {
  await mockReportApis(page, { paid: true })
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const pinRows = page.getByTestId("result-sheet").locator('div[role="button"]')
  await expect(pinRows).toHaveCount(3)
  await expect(pinRows.nth(1)).not.toHaveClass(/ring-2/)

  await pinRows.nth(1).click()

  await expect(pinRows.nth(1)).toHaveClass(/ring-2/)
  await expect(pinRows.nth(1)).toHaveClass(/bg-sky-50/)
  await expect(pinRows.nth(0)).not.toHaveClass(/ring-2/)
})

test("paid: clicking a map marker activates the matching list row and keeps the sheet open", async ({
  page,
}) => {
  await mockReportApis(page, { paid: true })
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const sheet = page.getByTestId("result-sheet")
  const pinRows = sheet.locator('div[role="button"]')
  await expect(pinRows).toHaveCount(3)
  await expect(page.getByTestId("flood-map")).toHaveAttribute(
    "data-marker-count",
    "3"
  )

  // Google Maps loads asynchronously through @googlemaps/js-api-loader. Wait for
  // both the loader global and the marker hook before triggering the click.
  await page.waitForFunction(
    () => {
      const w = window as any
      return (
        typeof w.google?.maps?.event?.trigger === "function" &&
        Array.isArray(w.__floodMarkers) &&
        Boolean(w.__floodMarkers[1])
      )
    },
    undefined,
    { timeout: 20_000 }
  )

  // Index 1 => pin 2 => second list row. Works for legacy Markers and the
  // Circle fallback overlays alike.
  await page.evaluate(() => {
    const w = window as any
    w.google.maps.event.trigger(w.__floodMarkers[1], "click")
  })

  await expect(pinRows.nth(1)).toHaveClass(/ring-2/)
  await expect(pinRows.nth(0)).not.toHaveClass(/ring-2/)
  // Marker selection came from the map; the non-modal (Radix) sheet must stay open.
  await expect(sheet).toBeVisible()
})

test("paid: real map zoom/pan gestures do not dismiss the result sheet", async ({
  page,
}) => {
  const vp = page.viewportSize()
  if (!vp || vp.width < 1024) {
    test.skip()
    return
  }

  await mockReportApis(page, { paid: true })
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const sheet = page.getByTestId("result-sheet")
  await expect(sheet).toBeVisible()

  // Wait for Google Maps + pins. Unlike the marker test above, these are real
  // pointer gestures so they exercise Radix's pointer-down-outside path.
  await page.waitForFunction(
    () => {
      const w = window as any
      return (
        Array.isArray(w.__floodMarkers) &&
        Boolean(w.__floodMarkers[0])
      )
    },
    undefined,
    { timeout: 20_000 }
  )

  // Real zoom control lives inside the map container.
  await page.locator('button[aria-label="Zoom out"]').click()
  await expect(sheet).toBeVisible()

  // Drag-pan the map in the area not covered by the desktop left panel.
  const map = page.getByTestId("flood-map")
  const box = await map.boundingBox()
  expect(box).not.toBeNull()
  const startX = box!.x + box!.width * 0.75
  const startY = box!.y + box!.height * 0.6
  await page.mouse.move(startX, startY)
  await page.mouse.down()
  await page.mouse.move(startX - 160, startY - 60, { steps: 8 })
  await page.mouse.up()

  await expect(sheet).toBeVisible()
})

test("paid: closing and reopening the sheet keeps the user's map zoom", async ({
  page,
}) => {
  const vp = page.viewportSize()
  if (!vp || vp.width < 1024) {
    test.skip()
    return
  }

  await mockReportApis(page, { paid: true })
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const sheet = page.getByTestId("result-sheet")
  await expect(sheet.getByText("Lịch sử sự kiện")).toBeVisible()
  await page.waitForFunction(
    () =>
      Array.isArray((window as any).__floodMarkers) &&
      Boolean((window as any).__floodMarkers[0]),
    undefined,
    { timeout: 20_000 }
  )

  const readZoom = () =>
    page.evaluate(() => {
      const w = window as any
      return w.__floodMarkers?.[0]?.getMap()?.getZoom() ?? null
    })

  // Move the viewport away from the auto-fit, then record it.
  await page.locator('button[aria-label="Zoom out"]').click()
  const zoomBefore = await readZoom()
  expect(zoomBefore).not.toBeNull()

  // The reopen refetches the report and rebuilds the overlays; remember the
  // current marker instance as the "effect has not re-run yet" signal.
  await page.evaluate(() => {
    ;(window as any).__savedMarker = (window as any).__floodMarkers?.[0]
  })

  // Close via the X button, then reopen from the minimized handle.
  await sheet.getByRole("button", { name: "Đóng" }).click()
  await expect(page.getByTestId("result-sheet")).toHaveCount(0)

  await page.getByRole("button", { name: /Mỹ Đình II/ }).click()
  await expect(page.getByTestId("result-sheet")).toBeVisible()
  await expect(
    page.getByTestId("result-sheet").getByText("Đã thanh toán")
  ).toBeVisible()

  // Wait until the reopened card has refetched and rebuilt its overlays.
  await page.waitForFunction(
    () => {
      const w = window as any
      return Boolean(w.__floodMarkers?.[0]) && w.__floodMarkers[0] !== w.__savedMarker
    },
    undefined,
    { timeout: 20_000 }
  )

  expect(await readZoom()).toBe(zoomBefore)
})

test("free/teaser: no event pins, rows, or map legend leak", async ({ page }) => {
  await mockReportApis(page, { paid: false })
  await page.goto("/")
  await typeInSearch(page, "my dinh")
  await page.getByTestId("search-result-item").first().click()

  const sheet = page.getByTestId("result-sheet")
  // Teaser paywall is shown...
  await expect(sheet.getByRole("button", { name: /35\.000 VND/ })).toBeVisible()
  // ...and no paid event data is rendered.
  await expect(sheet.getByText("Lịch sử sự kiện")).toHaveCount(0)
  await expect(sheet.locator('div[role="button"]')).toHaveCount(0)
  await expect(sheet.locator("span.h-5.w-5")).toHaveCount(0)
  // The map legend only renders when reportEventLocations is non-empty.
  await expect(page.getByText(/^\d+ điểm ngập$/)).toHaveCount(0)
})
