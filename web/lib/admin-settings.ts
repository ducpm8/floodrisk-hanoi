import { ApiError, apiFetch, apiUrl } from "./api"

const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_TOKEN ?? ""

export type MapMode = "text" | "google"

export interface AppSettings {
  map_mode: MapMode
}

/**
 * Read app settings for the homepage. Never throws: if the API is
 * unreachable or returns an error, fall back to text mode so a network
 * blip can never crash the landing page with "Failed to fetch".
 */
export async function fetchSettings(): Promise<AppSettings> {
  try {
    const res = await apiFetch("/api/admin/settings", { cache: "no-store" })
    if (!res.ok) return { map_mode: "text" }
    return await res.json()
  } catch {
    return { map_mode: "text" }
  }
}

export async function patchMapMode(mode: MapMode): Promise<AppSettings> {
  const res = await apiFetch("/api/admin/settings", {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": ADMIN_TOKEN,
    },
    body: JSON.stringify({ map_mode: mode }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new ApiError(res.status, err.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}
