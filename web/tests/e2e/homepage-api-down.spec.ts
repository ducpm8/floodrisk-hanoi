/**
 * Regression test for the homepage "Unhandled Runtime Error: Failed to fetch"
 * caused by an unreachable API origin (lib/admin-settings.ts).
 *
 * The settings request is part of the very first render. A failed fetch must
 * degrade to text mode, never produce an uncaught exception / error overlay.
 *
 * Run:
 *   cd web && npx playwright test tests/e2e/homepage-api-down.spec.ts \
 *     --project=chromium-desktop
 */

import { test, expect } from "@playwright/test"

const HERO = "Nhà bạn có đang trong vùng ngập?"

test("homepage still renders when the settings request is unreachable", async ({
  page,
}) => {
  const pageErrors: string[] = []
  page.on("pageerror", (err) => pageErrors.push(String(err)))

  // Simulates the production failure: DNS/connection refused/CORS.
  await page.route("**/api/admin/settings", (route) => route.abort("failed"))

  await page.goto("/")

  await expect(page.getByText(HERO)).toBeVisible()
  await expect(page.getByTestId("search-input")).toBeVisible()
  // The old code surfaced "TypeError: Failed to fetch" here.
  expect(pageErrors).toEqual([])
})

test("homepage still renders when the settings endpoint returns 500", async ({
  page,
}) => {
  const pageErrors: string[] = []
  page.on("pageerror", (err) => pageErrors.push(String(err)))

  await page.route("**/api/admin/settings", (route) =>
    route.fulfill({ status: 500, contentType: "text/plain", body: "boom" }),
  )

  await page.goto("/")

  await expect(page.getByText(HERO)).toBeVisible()
  expect(pageErrors).toEqual([])
})
