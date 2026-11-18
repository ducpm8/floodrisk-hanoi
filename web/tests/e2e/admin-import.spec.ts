/**
 * Admin CSV import E2E — API is mocked, UI behaviour only.
 */

import { test, expect, type Page } from "@playwright/test"

const IMPORT_RESPONSE = {
  inserted: 1,
  duplicates: 0,
  skipped: 1,
  dry_run: true,
  errors: [
    { row: 3, field: "event_date", message: "Định dạng phải là YYYY-MM-DD" },
  ],
  rows: [
    {
      row: 2,
      location_name: "Ngã tư Vành Đai 3",
      event_date: "2025-09-15",
      severity: "moderate",
      ward: "my dinh i",
      district: "Nam Từ Liêm",
      status: "inserted",
    },
    {
      row: 3,
      location_name: "Điểm lỗi",
      event_date: "2025-13-40",
      severity: "severe",
      ward: null,
      district: null,
      status: "error",
    },
  ],
}

const CSV = [
  "location_name,event_date,severity,lat,lng,ward,district,depth_cm,duration_hours,source,source_url,cause",
  "Ngã tư Vành Đai 3,2025-09-15,moderate,21.03,105.78,Mỹ Đình I,Nam Từ Liêm,35,4,community,,",
  "Điểm lỗi,2025-13-40,severe,21.03,105.78,,,,,community,,",
].join("\n")

async function mockApi(page: Page) {
  await page.route("**/admin/flood-import/template", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/csv; charset=utf-8",
      headers: {
        "Content-Disposition": 'attachment; filename="flood_locations_template.csv"',
      },
      body: "﻿" + CSV,
    }),
  )
  await page.route("**/admin/flood-import", async (route) => {
    const body = route.request().postData() ?? ""
    const dryRun = body.includes('name="dry_run"\r\n\r\ntrue') || body.includes("true")
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...IMPORT_RESPONSE, dry_run: dryRun }),
    })
  })
}

async function chooseFile(page: Page) {
  await page.setInputFiles('input[type="file"]', {
    name: "flood.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(CSV, "utf-8"),
  })
}

test("page renders import form and column help", async ({ page }) => {
  await mockApi(page)
  await page.goto("/admin/import")

  await expect(
    page.getByRole("heading", { name: "Nhập địa điểm ngập bằng CSV" }),
  ).toBeVisible()
  await expect(page.getByRole("button", { name: "Tải mẫu CSV" })).toBeVisible()
  await expect(page.getByText("location_name")).toBeVisible()
  await expect(page.getByText("Bắt buộc").first()).toBeVisible()
})

test("template button downloads CSV", async ({ page }) => {
  await mockApi(page)
  await page.goto("/admin/import")

  const downloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "Tải mẫu CSV" }).click()
  const download = await downloadPromise

  expect(download.suggestedFilename()).toBe("flood_locations_template.csv")
})

test("dry run shows per-row statuses and keeps import disabled-free", async ({ page }) => {
  await mockApi(page)
  await page.goto("/admin/import")
  await chooseFile(page)

  await page.getByRole("button", { name: "Kiểm tra file" }).click()

  await expect(page.getByText("Kết quả kiểm tra")).toBeVisible()
  await expect(page.getByText("Hợp lệ", { exact: true })).toBeVisible()
  await expect(page.getByText("Ngã tư Vành Đai 3")).toBeVisible()
  await expect(page.getByText("Đã nhập")).toBeVisible()
  await expect(page.getByText("Lỗi", { exact: true }).first()).toBeVisible()
  await expect(page.getByText("event_date: Định dạng phải là YYYY-MM-DD")).toBeVisible()
  await expect(page.getByText(/Chưa ghi gì vào cơ sở dữ liệu/)).toBeVisible()
})

test("import result shows statuses and error CSV download", async ({ page }) => {
  let callCount = 0
  await page.route("**/admin/flood-import/template", (route) =>
    route.fulfill({ status: 200, contentType: "text/csv", body: "﻿" + CSV }),
  )
  await page.route("**/admin/flood-import", (route) => {
    callCount += 1
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...IMPORT_RESPONSE, dry_run: false }),
    })
  })
  await page.goto("/admin/import")
  await chooseFile(page)

  await page.getByRole("button", { name: "Nhập dữ liệu" }).click()

  await expect(page.getByText("Kết quả nhập")).toBeVisible()
  await expect(page.getByRole("button", { name: "Tải các dòng lỗi" })).toBeVisible()

  const downloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "Tải các dòng lỗi" }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe("flood_locations_errors.csv")
  expect(callCount).toBe(1)
})
