import { apiFetch, apiUrl } from "./api"

const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_TOKEN ?? ""

function headers(): HeadersInit {
  return {
    "Content-Type": "application/json",
    ...(ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}),
  }
}

export interface CrawlSource {
  source_name: string
  source_type: "governance" | "news" | "social"
  source_url: string
  crawler_class: string
  is_enabled: boolean
  last_crawled_at: string | null
  last_run_status: "running" | "success" | "partial" | "failed" | null
  last_run_records: number | null
}

export interface CreateSourcePayload {
  source_name: string
  source_type: string
  source_url: string
  crawler_class: string
  is_enabled: boolean
  notes?: string
}

export async function fetchSources(): Promise<CrawlSource[]> {
  const res = await apiFetch(apiUrl("/api/admin/crawl-sources"), { headers: headers() })
  if (!res.ok) throw new Error(`${res.status}`)
  return res.json()
}

export async function fetchCrawlerClasses(): Promise<string[]> {
  const res = await apiFetch(apiUrl("/api/admin/crawler-classes"), {
    headers: headers(),
  })
  if (!res.ok) throw new Error(`${res.status}`)
  return res.json()
}

export async function createSource(payload: CreateSourcePayload): Promise<CrawlSource> {
  const res = await apiFetch(apiUrl("/api/admin/crawl-sources"), {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `${res.status}`)
  }
  return res.json()
}

export async function patchSource(
  sourceName: string,
  patch: { is_enabled?: boolean; source_url?: string; notes?: string },
): Promise<CrawlSource> {
  const res = await apiFetch(apiUrl(`/api/admin/crawl-sources/${sourceName}`), {
    method: "PATCH",
    headers: headers(),
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`${res.status}`)
  return res.json()
}

export async function deleteSource(sourceName: string): Promise<void> {
  const res = await apiFetch(apiUrl(`/api/admin/crawl-sources/${sourceName}`), {
    method: "DELETE",
    headers: headers(),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `${res.status}`)
  }
}

export async function triggerCrawl(sourceName: string): Promise<{ run_id: string }> {
  const res = await apiFetch(apiUrl(`/api/admin/crawl-trigger/${sourceName}`), {
    method: "POST",
    headers: headers(),
  })
  if (res.status === 409) throw new Error("Crawl already running for this source")
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `${res.status}`)
  }
  return res.json()
}
