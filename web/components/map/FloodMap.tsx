"use client"

import { useEffect, useRef, useState } from "react"
import { Loader } from "@googlemaps/js-api-loader"
import type { SearchResult } from "@/lib/search"

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FloodZone {
  id: string
  lat: number
  lng: number
  severity: "severe" | "moderate" | "minor"
  ward: string
  radius_m: number
}

interface FloodMapProps {
  /**
   * When true (dropdown open), flood zone circles are hidden.
   * Prevents teaser data from being visible before payment.
   */
  isDropdownOpen: boolean
  /** Ward selected by user — map pans here and shows 500m radius circle. */
  selectedWard: Pick<SearchResult, "lat" | "lng" | "ward" | "location_name" | "location_type"> | null
  /** Event lat/lngs (+ optional name) from paid report — map fits bounds and draws streets. */
  reportEventLocations?: { lat: number; lng: number; location_name?: string }[]
  /** When true, all background zone circles are hidden (e.g. result sheet open — prevent teaser leak). */
  hideZones?: boolean
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const HANOI_CENTER = { lat: 21.0285, lng: 105.8542 }
const DEFAULT_ZOOM = 12
const WARD_ZOOM = 15
const WARD_RADIUS_METERS = 500

/** FIR-10: severity → fill colour (red=severe, orange=moderate, yellow=minor) */
const SEVERITY_COLORS: Record<string, string> = {
  severe: "#ef4444",    // red-500
  moderate: "#f97316",  // orange-500
  minor: "#eab308",     // yellow-500
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FloodMap({ isDropdownOpen, selectedWard, reportEventLocations, hideZones }: FloodMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<google.maps.Map | null>(null)
  const wardCircleRef = useRef<google.maps.Circle | null>(null)
  const zoneCirclesRef = useRef<google.maps.Circle[]>([])
  const eventMarkersRef = useRef<google.maps.Circle[]>([])
  const eventPolylinesRef = useRef<google.maps.Polyline[]>([])
  const loaderRef = useRef<Loader | null>(null)
  const mapReadyRef = useRef(false)

  const [isLoading, setIsLoading] = useState(true)
  const [mapError, setMapError] = useState<string | null>(null)
  const [zonesReady, setZonesReady] = useState(false)
  const pendingZonesRef = useRef<FloodZone[]>([])

  // -------------------------------------------------------------------------
  // Fetch flood zones from backend
  // -------------------------------------------------------------------------
  useEffect(() => {
    fetch("/api/flood-zones")
      .then((r) => r.json())
      .then((data: { zones: FloodZone[] }) => {
        pendingZonesRef.current = data.zones ?? []
        setZonesReady(true)
      })
      .catch(() => {
        // Non-fatal: map still works without zone overlays
        pendingZonesRef.current = []
        setZonesReady(true)
      })
  }, [])

  // -------------------------------------------------------------------------
  // Initialise Google Maps once
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!containerRef.current) return
    if (mapRef.current) return

    const apiKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY
    if (!apiKey) {
      setIsLoading(false)
      return
    }

    // Error boundary for invalid / restricted API keys
    const handleAuthFailure = () => {
      setMapError(
        "Google Maps API key không hợp lệ. Kiểm tra NEXT_PUBLIC_GOOGLE_MAPS_API_KEY trong .env.local"
      )
      setIsLoading(false)
    }
    window.addEventListener("gm-authfailure", handleAuthFailure)

    loaderRef.current = new Loader({
      apiKey,
      version: "weekly",
      libraries: ["geometry"],
    })

    loaderRef.current
      .load()
      .then(() => {
        if (!containerRef.current) return

        mapRef.current = new google.maps.Map(containerRef.current, {
          center: HANOI_CENTER,
          zoom: DEFAULT_ZOOM,
          disableDefaultUI: true,
          zoomControl: true,
          gestureHandling: "greedy",
          styles: [
            // Subtle style so severity circles stand out
            { featureType: "poi", stylers: [{ visibility: "off" }] },
            { featureType: "transit", stylers: [{ visibility: "off" }] },
          ],
        })

        mapReadyRef.current = true
        setIsLoading(false)
      })
      .catch(() => {
        setMapError("Không tải được Google Maps. Kiểm tra kết nối mạng và API key.")
        setIsLoading(false)
      })

    return () => {
      window.removeEventListener("gm-authfailure", handleAuthFailure)
    }
  }, [])

  // -------------------------------------------------------------------------
  // Draw flood zone circles once both map AND zones are ready
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!mapReadyRef.current || !mapRef.current) return
    if (!zonesReady) return

    // Clear any existing zone circles
    zoneCirclesRef.current.forEach((c) => c.setMap(null))
    zoneCirclesRef.current = []

    const fillColor = (s: string) => SEVERITY_COLORS[s] ?? SEVERITY_COLORS.minor

    pendingZonesRef.current.forEach((zone) => {
      const color = fillColor(zone.severity)
      const circle = new google.maps.Circle({
        map: mapRef.current!,
        center: { lat: zone.lat, lng: zone.lng },
        radius: zone.radius_m,
        fillColor: color,
        fillOpacity: 0.25,
        strokeColor: color,
        strokeOpacity: 0.7,
        strokeWeight: 1.5,
        clickable: false,
      })
      zoneCirclesRef.current.push(circle)
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zonesReady, isLoading])

  // -------------------------------------------------------------------------
  // When report events load: hide background zone circles, try to draw each
  // event as an OSM street polyline (via Overpass); fall back to a dot circle.
  // When events are cleared: restore zone circles.
  // -------------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false

    // Clear previous event overlays
    eventMarkersRef.current.forEach((c) => c.setMap(null))
    eventMarkersRef.current = []
    eventPolylinesRef.current.forEach((p) => p.setMap(null))
    eventPolylinesRef.current = []

    if (!reportEventLocations || reportEventLocations.length === 0) {
      zoneCirclesRef.current.forEach((c) => c.setVisible(!hideZones))
      return () => { cancelled = true }
    }

    zoneCirclesRef.current.forEach((c) => c.setVisible(false))

    if (!mapRef.current) return () => { cancelled = true }

    // Fit bounds immediately using the centroid coords we already have
    if (selectedWard) {
      const bounds = new google.maps.LatLngBounds()
      bounds.extend({ lat: selectedWard.lat, lng: selectedWard.lng })
      reportEventLocations.forEach((loc) => bounds.extend(loc))
      mapRef.current.fitBounds(bounds, 80)
    }

    const drawCircle = (loc: { lat: number; lng: number }) => {
      if (cancelled || !mapRef.current) return
      const c = new google.maps.Circle({
        map: mapRef.current,
        center: loc,
        radius: 80,
        fillColor: "#ef4444",
        fillOpacity: 0.7,
        strokeColor: "#ef4444",
        strokeOpacity: 1,
        strokeWeight: 2,
        clickable: false,
      })
      eventMarkersRef.current.push(c)
    }

    // Step 1: draw a circle for every event immediately so map is never blank
    reportEventLocations.forEach((loc) => drawCircle(loc))

    // Step 2: for each unique location name, try Overpass to get the full street
    // geometry and draw it as a polyline on top (circles stay as backstop)
    const VIET_PREFIX = /^(phố|đường|ngõ|ngách|hẻm|phường|quận|tổ)\s+/iu
    const seen = new Set<string>()

    reportEventLocations.forEach((loc) => {
      const name = loc.location_name?.trim()
      if (!name || seen.has(name)) return
      seen.add(name)

      const coreName = name.replace(VIET_PREFIX, "").trim()
      const query =
        `[out:json][timeout:10];` +
        `way["name"~"${coreName}"](20.8,105.6,21.2,106.0);` +
        `out geom;`

      fetch(`https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`)
        .then((r) => r.json())
        .then((data: { elements?: { geometry?: { lat: number; lon: number }[] }[] }) => {
          if (cancelled || !mapRef.current) return
          const ways = (data.elements ?? []).filter((e) => e.geometry && e.geometry.length > 1)
          ways.forEach((way) => {
            const path = (way.geometry ?? []).map((pt) => ({ lat: pt.lat, lng: pt.lon }))
            const poly = new google.maps.Polyline({
              map: mapRef.current!,
              path,
              strokeColor: "#ef4444",
              strokeOpacity: 0.9,
              strokeWeight: 5,
              clickable: false,
            })
            eventPolylinesRef.current.push(poly)
          })
        })
        .catch(() => {}) // circles already drawn — nothing extra needed
    })

    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportEventLocations, isLoading])

  // -------------------------------------------------------------------------
  // Hide/show flood zone circles based on dropdown + hideZones prop
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (eventMarkersRef.current.length > 0 || eventPolylinesRef.current.length > 0) return
    zoneCirclesRef.current.forEach((c) => c.setVisible(!isDropdownOpen && !hideZones))
  }, [isDropdownOpen, hideZones])

  // -------------------------------------------------------------------------
  // Pan + ward radius circle when ward selected
  // For alias types (landmark, apartment, street…), geocode the display name
  // via the Maps Geocoder to get the exact pin Google Maps would show.
  // Falls back to ward centroid if geocoding fails or returns nothing.
  // Depends on isLoading so it re-fires once the map finishes initialising.
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (!mapRef.current || !selectedWard) return

    const applyCenter = (pos: { lat: number; lng: number }) => {
      if (!mapRef.current) return
      mapRef.current.panTo(pos)
      mapRef.current.setZoom(WARD_ZOOM)
      wardCircleRef.current?.setMap(null)
      wardCircleRef.current = new google.maps.Circle({
        map: mapRef.current!,
        center: pos,
        radius: WARD_RADIUS_METERS,
        fillColor: "#3b82f6",
        fillOpacity: 0.08,
        strokeColor: "#3b82f6",
        strokeOpacity: 0.6,
        strokeWeight: 2,
        clickable: false,
      })
    }

    const fallback = { lat: selectedWard.lat, lng: selectedWard.lng }

    // For non-ward aliases (landmarks, apartments, streets…), geocode the
    // human-readable name so the pin matches exactly what Google Maps shows.
    if (selectedWard.location_type !== "ward" && selectedWard.location_name) {
      const geocoder = new google.maps.Geocoder()
      geocoder.geocode(
        { address: `${selectedWard.location_name} Hà Nội`, region: "VN" },
        (results, status) => {
          if (status === "OK" && results && results[0]) {
            const loc = results[0].geometry.location
            applyCenter({ lat: loc.lat(), lng: loc.lng() })
          } else {
            applyCenter(fallback)
          }
        }
      )
    } else {
      applyCenter(fallback)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedWard, isLoading])

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------
  const noApiKey = !process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY

  return (
    <div className={`flood-map relative h-full w-full bg-slate-100 transition-opacity duration-200 ${isDropdownOpen ? "opacity-45" : "opacity-100"}`}>
      {/* Map container — always mounted so containerRef is available */}
      <div ref={containerRef} className="h-full w-full" />

      {/* Loading spinner */}
      {isLoading && !noApiKey && !mapError && (
        <div className="absolute inset-0 flex items-center justify-center bg-slate-100">
          <div className="flex flex-col items-center gap-3">
            <div className="h-10 w-10 animate-spin rounded-full border-4 border-slate-300 border-t-blue-500" />
            <p className="text-sm text-slate-500">Đang tải bản đồ…</p>
          </div>
        </div>
      )}

      {/* Error boundary — invalid key or network failure */}
      {mapError && (
        <div className="absolute inset-0 flex items-center justify-center bg-slate-100">
          <div className="mx-4 max-w-sm rounded-lg border border-red-200 bg-white p-5 text-center shadow">
            <p className="text-sm font-semibold text-red-600">Lỗi bản đồ</p>
            <p className="mt-1 text-xs text-slate-500">{mapError}</p>
          </div>
        </div>
      )}

      {/* Dev / CI placeholder when no API key configured */}
      {noApiKey && !mapError && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="rounded-lg border border-slate-200 bg-white p-4 text-center shadow">
            <p className="text-sm font-medium text-slate-600">Bản đồ Hà Nội</p>
            <p className="mt-1 text-xs text-slate-400">
              Thêm NEXT_PUBLIC_GOOGLE_MAPS_API_KEY vào .env.local
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
