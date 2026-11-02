const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_TOKEN ?? ""

export type MapMode = "text" | "google"

export interface AppSettings {
  map_mode: MapMode
}

export async function fetchSettings(): Promise<AppSettings> {
  const res = await fetch(`${API}/api/admin/settings`, { cache: "no-store" })
  if (!res.ok) return { map_mode: "text" }
  return res.json()
}

export async function patchMapMode(mode: MapMode): Promise<AppSettings> {
  const res = await fetch(`${API}/api/admin/settings`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": ADMIN_TOKEN,
    },
    body: JSON.stringify({ map_mode: mode }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  return res.json()
}
