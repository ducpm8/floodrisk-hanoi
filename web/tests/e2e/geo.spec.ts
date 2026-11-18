/**
 * FIR-69: unit coverage for the distance helpers in web/lib/geo.ts.
 *
 * These run on the repo's existing Playwright runner (the only test runner
 * configured in web/package.json). They are pure unit tests: no page/browser
 * fixture is used, so no browser is launched.
 *
 * Run just these:
 *   cd web && CI=1 npx playwright test tests/e2e/geo.spec.ts --project=chromium-desktop
 */

import { test, expect } from "@playwright/test"
import { formatDistance, haversineMetres } from "../../lib/geo"

// ---------------------------------------------------------------------------
// haversineMetres
// ---------------------------------------------------------------------------

test.describe("haversineMetres", () => {
  test("identical points are exactly 0 metres", () => {
    expect(haversineMetres(21.0285, 105.8542, 21.0285, 105.8542)).toBe(0)
  })

  test("is symmetric", () => {
    const ab = haversineMetres(21.0285, 105.8542, 21.0385, 105.8642)
    const ba = haversineMetres(21.0385, 105.8642, 21.0285, 105.8542)
    expect(ab).toBeCloseTo(ba, 6)
  })

  test("matches the known one-degree latitude distance", () => {
    // R = 6,371,000 m → 1° of latitude = π * R / 180 ≈ 111,194.93 m
    expect(haversineMetres(21, 105, 22, 105)).toBeCloseTo(111194.93, 0)
  })

  test("a ~22 m offset falls under the 50 m ward-centroid threshold", () => {
    const metres = haversineMetres(21.0285, 105.8542, 21.0287, 105.8542)
    expect(metres).toBeGreaterThan(0)
    expect(metres).toBeLessThan(50)
    expect(formatDistance(metres)).toBe("Cùng phường")
  })
})

// ---------------------------------------------------------------------------
// formatDistance
// ---------------------------------------------------------------------------

test.describe("formatDistance", () => {
  test("0 m renders 'Cùng phường', never a literal '0 m'", () => {
    expect(formatDistance(0)).toBe("Cùng phường")
    expect(formatDistance(0)).not.toBe("0 m")
  })

  test("every distance under 50 m renders 'Cùng phường'", () => {
    for (const metres of [0.4, 1, 25, 49, 49.999]) {
      expect(formatDistance(metres), `distance ${metres} m`).toBe("Cùng phường")
    }
  })

  test("50 m is the first distance that renders as metres", () => {
    expect(formatDistance(50)).toBe("50 m")
  })

  test("50-999 m rounds to the nearest metre", () => {
    expect(formatDistance(50.4)).toBe("50 m")
    expect(formatDistance(123.4)).toBe("123 m")
    expect(formatDistance(954.6)).toBe("955 m")
    expect(formatDistance(999)).toBe("999 m")
  })

  test("1000 m and above renders kilometres with one decimal", () => {
    expect(formatDistance(1000)).toBe("1.0 km")
    expect(formatDistance(1234)).toBe("1.2 km")
    expect(formatDistance(15000)).toBe("15.0 km")
  })
})
