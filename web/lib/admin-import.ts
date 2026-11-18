import { apiFetch, apiUrl } from "./api"

const ADMIN_TOKEN = process.env.NEXT_PUBLIC_ADMIN_TOKEN ?? ""

// Mirrors api/routers/admin_import.py limits so the UI can fail fast.
export const MAX_FILE_BYTES = 1_000_000
export const MAX_ROWS = 2_000

function authHeaders(): HeadersInit {
  return ADMIN_TOKEN ? { "X-Admin-Token": ADMIN_TOKEN } : {}
}

export interface ImportRowError {
  row: number
  field: string
  message: string
}

export type ImportRowStatus = "inserted" | "duplicate" | "error"

export interface ImportRowResult {
  row: number
  location_name: string
  event_date: string
  severity: string
  ward: string | null
  district: string | null
  status: ImportRowStatus
  lat?: string | null
  lng?: string | null
  depth_cm?: string | null
  duration_hours?: string | null
  source?: string | null
  source_url?: string | null
  cause?: string | null
}

export interface ImportResponse {
  inserted: number
  duplicates: number
  skipped: number
  errors: ImportRowError[]
  rows: ImportRowResult[]
  dry_run: boolean
}

export class ImportApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "ImportApiError"
    this.status = status
  }
}

function detailToMessage(detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail
  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail)
    } catch {
      /* ignore */
    }
  }
  return ""
}

export async function downloadTemplate(): Promise<void> {
  const res = await apiFetch(apiUrl("/api/admin/flood-import/template"), {
    headers: authHeaders(),
  })
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => null)
    const detail =
      body && typeof body === "object"
        ? detailToMessage((body as { detail?: unknown }).detail)
        : ""
    throw new ImportApiError(res.status, detail || `HTTP ${res.status}`)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = "flood_locations_template.csv"
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export async function importCsv(
  file: File,
  opts: { dryRun?: boolean } = {},
): Promise<ImportResponse> {
  const form = new FormData()
  form.append("file", file)
  form.append("dry_run", opts.dryRun ? "true" : "false")

  const res = await apiFetch(apiUrl("/api/admin/flood-import"), {
    method: "POST",
    // No Content-Type: browser adds the multipart boundary.
    headers: authHeaders(),
    body: form,
  })
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const detail =
      body && typeof body === "object"
        ? detailToMessage((body as { detail?: unknown }).detail)
        : ""
    throw new ImportApiError(res.status, detail || `HTTP ${res.status}`)
  }
  return body as ImportResponse
}

/** Build a CSV of rejected rows (plus the backend status) for re-upload. */
export function errorsCsv(result: ImportResponse): Blob {
  const header = [
    "location_name",
    "event_date",
    "severity",
    "ward",
    "district",
    "lat",
    "lng",
    "depth_cm",
    "duration_hours",
    "source",
    "source_url",
    "cause",
    "error",
  ]
  const rows = result.rows
    .filter((row) => row.status === "error")
    .map((row) => {
      const messages = result.errors
        .filter((err) => err.row === row.row)
        .map((err) => `${err.field}: ${err.message}`)
        .join("; ")
      const cells = header.map((key) => {
        if (key === "location_name") return row.location_name
        if (key === "event_date") return row.event_date
        if (key === "severity") return row.severity
        if (key === "ward") return row.ward ?? ""
        if (key === "district") return row.district ?? ""
        if (key === "error") return messages
        const value = row[key as keyof ImportRowResult]
        return typeof value === "string" ? value : ""
      })
      return cells.map(escapeCsv).join(",")
    })
  return new Blob(["﻿" + [header.join(","), ...rows].join("\r\n")], {
    type: "text/csv;charset=utf-8",
  })
}

function escapeCsv(value: string): string {
  if (/[",\r\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`
  }
  return value
}
