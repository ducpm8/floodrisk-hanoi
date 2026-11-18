/**
 * Single source of truth for API URLs and fetch error handling.
 *
 * By default requests are same-origin ("") so they go through the Next.js
 * rewrite in next.config.js (/api/* -> API_BASE_URL). That works on
 * localhost, on 127.0.0.1 and from other devices on the LAN
 * (e.g. http://192.168.31.83:3000) without any CORS configuration.
 *
 * Set NEXT_PUBLIC_API_URL only when the API is served from a different
 * origin than the web app. Never point it at a LAN IP that the API is not
 * actually bound to — uvicorn listening on 127.0.0.1 cannot be reached that
 * way and every browser fetch fails with "TypeError: Failed to fetch".
 */

const RAW_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "")
  .trim()
  .replace(/\/+$/, "")

/** Base URL for API calls. Empty string means same-origin. */
export const API_BASE = RAW_BASE

/** Build an absolute or same-origin URL for an API path. */
export function apiUrl(path: string): string {
  const suffix = path.startsWith("/") ? path : `/${path}`
  return `${API_BASE}${suffix}`
}

/**
 * Normalised API failure. `status === 0` means the request never reached the
 * server (offline, DNS, CORS, connection refused), as opposed to an HTTP error.
 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string | null

  constructor(status: number, message: string, detail: string | null = null) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }
}

function detailFromBody(body: unknown): string | null {
  if (!body || typeof body !== "object") return null
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === "string" && detail.trim()) return detail
  if (detail) {
    try {
      return JSON.stringify(detail)
    } catch {
      return null
    }
  }
  return null
}

/**
 * `fetch` wrapper that turns network failures into a typed ApiError instead of
 * a raw TypeError. Callers can therefore distinguish "server unreachable"
 * (status 0) from a real HTTP error and decide whether to degrade gracefully.
 */
export async function apiFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  try {
    return await fetch(apiUrl(path), init)
  } catch (cause) {
    // Preserve cancellation semantics for callers using AbortController.
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause
    }
    throw new ApiError(
      0,
      "Không thể kết nối tới máy chủ API.",
      cause instanceof Error ? cause.message : null,
    )
  }
}

/** Fetch a path and parse JSON, throwing ApiError on network or HTTP failure. */
export async function apiJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await apiFetch(path, init)
  const body: unknown = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = detailFromBody(body)
    throw new ApiError(res.status, detail ?? `HTTP ${res.status}`, detail)
  }
  return body as T
}
