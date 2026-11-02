/**
 * Search API client + Fuse.js local cache.
 *
 * Data flow:
 *   normalizeVi(q) → Fuse.js (instant local results)
 *                  → GET /api/search (server results, debounced 300ms)
 *   Both result sets are merged; server results win on ward_normalized key.
 */

import Fuse from "fuse.js"

export interface SearchResult {
  ward: string
  ward_normalized: string
  location_name: string
  location_type: string
  district: string
  lat: number
  lng: number
  has_flood_data: boolean
  data_year_range: string | null
}

export interface SearchResponse {
  results: SearchResult[]
  total: number
}

// ---------------------------------------------------------------------------
// Fuse.js local index (populated from first successful API response)
// ---------------------------------------------------------------------------

let _fuse: Fuse<SearchResult> | null = null
let _fuseData: SearchResult[] = []

function updateFuseIndex(results: SearchResult[]) {
  const incoming = results.filter(
    (r) => !_fuseData.some((e) => e.ward_normalized === r.ward_normalized)
  )
  if (incoming.length === 0) return
  _fuseData = [..._fuseData, ...incoming]
  _fuse = new Fuse(_fuseData, {
    keys: ["ward_normalized", "location_name", "district"],
    threshold: 0.35,
    includeScore: true,
  })
}

export function fuseSearch(q: string): SearchResult[] {
  if (!_fuse || !q) return []
  return _fuse.search(q).map((r) => r.item)
}

// ---------------------------------------------------------------------------
// API fetch
// ---------------------------------------------------------------------------

export async function fetchSearch(
  q: string,
  signal?: AbortSignal
): Promise<SearchResult[]> {
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`, { signal })
  if (!res.ok) throw new Error(`Search failed: ${res.status}`)
  const data: SearchResponse = await res.json()
  updateFuseIndex(data.results)
  return data.results
}
