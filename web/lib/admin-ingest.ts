import { apiFetch, apiUrl } from "./api"

const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_TOKEN ?? ""

// Mirrors api/routers/admin_ingest.py limits so the UI can fail fast.
export const MAX_TEXT_CHARS = 100_000
export const MAX_FILE_BYTES = 100_000

function authHeaders(): HeadersInit {
  return ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}
}

function jsonHeaders(): HeadersInit {
  return {
    "Content-Type": "application/json",
    ...authHeaders(),
  }
}

export interface IngestStats {
  total?: number
  saved?: number
  duplicate?: number
  ward_error?: number
  db_error?: number
  low_confidence?: number
  no_depth?: number
  error?: string
}

export interface IngestResponse {
  staging_id: string
  // "processed" | "error" for the manual endpoints; "queued" is kept for the
  // async youtube-ingest shape so this helper can be reused later.
  status: string
  stats: IngestStats
}

export interface IngestPayload {
  content: string
  source_url?: string
  event_date?: string
}

export class IngestApiError extends Error {
  readonly status: number
  readonly detail: string | null

  constructor(status: number, detail: string | null) {
    super(detail ?? `HTTP ${status}`)
    this.name = "IngestApiError"
    this.status = status
    this.detail = detail
  }
}

function detailToMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (item && typeof item === "object") {
        const entry = item as { loc?: unknown; msg?: unknown }
        const loc = Array.isArray(entry.loc)
          ? entry.loc.filter((part) => part !== "body").join(".")
          : ""
        const msg = typeof entry.msg === "string" ? entry.msg : JSON.stringify(item)
        return loc ? `${loc}: ${msg}` : msg
      }
      return String(item)
    })
    return parts.length > 0 ? parts.join("; ") : null
  }
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail)
    } catch {
      return null
    }
  }
  return null
}

async function readResponse(res: Response): Promise<IngestResponse> {
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const detail =
      body && typeof body === "object"
        ? detailToMessage((body as { detail?: unknown }).detail)
        : null
    throw new IngestApiError(res.status, detail)
  }
  return body as IngestResponse
}

export async function ingestText(payload: IngestPayload): Promise<IngestResponse> {
  const res = await apiFetch(apiUrl("/api/admin/manual-ingest"), {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
  })
  return readResponse(res)
}

export async function ingestFile(
  file: File,
  opts: { source_url?: string; event_date?: string } = {},
): Promise<IngestResponse> {
  const form = new FormData()
  form.append("file", file)
  if (opts.source_url) form.append("source_url", opts.source_url)
  if (opts.event_date) form.append("event_date", opts.event_date)

  const res = await apiFetch(apiUrl("/api/admin/manual-ingest/upload"), {
    method: "POST",
    // Do not set Content-Type: the browser adds the multipart boundary.
    headers: authHeaders(),
    body: form,
  })
  return readResponse(res)
}
