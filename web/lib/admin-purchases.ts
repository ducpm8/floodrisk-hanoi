const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api"
const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_TOKEN ?? ""

function headers(): HeadersInit {
  return {
    "Content-Type": "application/json",
    ...(ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}),
  }
}

export interface PurchaseItem {
  id: string
  email: string
  session_id: string
  ward_normalized: string | null
  location_query: string | null
  location_lat: string | null
  location_lng: string | null
  payment_gateway: string | null
  payment_id: string | null
  amount_vnd: number
  status: "paid" | "pending" | "failed"
  failure_reason: string | null
  paid_at: string | null
  created_at: string | null
}

export interface PurchasesResponse {
  total: number
  page: number
  page_size: number
  items: PurchaseItem[]
}

export type StatusFilter = "all" | "paid" | "pending" | "failed"

export async function fetchPurchases(
  page: number,
  pageSize: number,
  status: StatusFilter,
  q: string,
): Promise<PurchasesResponse> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    status,
    q,
  })
  const res = await fetch(`${BASE}/admin/purchases?${params}`, { headers: headers() })
  if (!res.ok) throw new Error(`${res.status}`)
  return res.json()
}

export async function exportPurchasesCSV(status: StatusFilter): Promise<void> {
  const params = new URLSearchParams({ status })
  const res = await fetch(`${BASE}/admin/purchases/export?${params}`, {
    headers: { ...(ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}) },
  })
  if (!res.ok) throw new Error(`${res.status}`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  const cd = res.headers.get("Content-Disposition") ?? ""
  const match = cd.match(/filename=(.+)/)
  a.download = match ? match[1] : "purchases.csv"
  a.click()
  URL.revokeObjectURL(url)
}
