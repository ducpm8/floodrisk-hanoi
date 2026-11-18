"use client"

import { useEffect, useRef, useState } from "react"
import { Loader } from "@googlemaps/js-api-loader"
import type { SearchResult } from "@/lib/search"
import type { ReportEventLocation } from "@/lib/report"

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
  selectedWard: Pick<SearchResult, "lat" | "lng" | "ward" | "location_name" | "location_type" | "coords_precise"> | null
  /** Paid-report events with coords — numbered pins + street polylines; index matches list row. */
  reportEventLocations?: ReportEventLocation[]
  /** When true, all background zone circles are hidden (e.g. result sheet open — prevent teaser leak). */
  hideZones?: boolean
  /** Index (into reportEventLocations) of the pin selected from the list. */
  activeEventIdx?: number | null
  /** Called with the pin index when a map marker is clicked. */
  onEventSelect?: (index: number) => void
}

/**
 * Per-event overlay, index-aligned with `reportEventLocations`.
 * Position `i` is always assigned (never conditionally), so pin numbers and
 * list-row numbers cannot shift when one marker fails to construct.
 */
type EventOverlayEntry =
  | {
      kind: "marker"
      marker: google.maps.Marker
      severity: string
      position: google.maps.LatLngLiteral
      content: string
    }
  | {
      kind: "fallback"
      circle: google.maps.Circle
      severity: string
      position: google.maps.LatLngLiteral
      content: string
    }
  | null

declare global {
  interface Window {
    /**
     * Dev/test-only hook (never set in production): the concrete Google Maps
     * overlay for each paid-report event, in `reportEventLocations` index order.
     * A slot is `null` when neither a Marker nor a fallback Circle could be
     * constructed, so indices stay aligned with the report/list rows.
     */
    __floodMarkers?: Array<google.maps.Marker | google.maps.Circle | null>
  }
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Test hooks are dev-only; production markup/bundle must not expose them. */
const IS_DEV = process.env.NODE_ENV !== "production"

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

const SEVERITY_LABELS: Record<string, string> = {
  severe: "Nghiêm trọng",
  moderate: "Trung bình",
  minor: "Nhẹ",
}

const SEVERITY_RANK: Record<string, number> = {
  minor: 1,
  moderate: 2,
  severe: 3,
}

const NORMAL_PIN_SCALE = 13
const ACTIVE_PIN_SCALE = 17

function severityColor(severity: string | null | undefined): string {
  return SEVERITY_COLORS[severity ?? "minor"] ?? SEVERITY_COLORS.minor
}

/** Numbered circular pin; the active pin is enlarged. */
function pinIcon(severity: string, active: boolean): google.maps.Symbol {
  return {
    path: google.maps.SymbolPath.CIRCLE,
    fillColor: severityColor(severity),
    fillOpacity: 1,
    strokeColor: "#ffffff",
    strokeWeight: 2,
    scale: active ? ACTIVE_PIN_SCALE : NORMAL_PIN_SCALE,
  }
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (ch) => {
    switch (ch) {
      case "&":
        return "&amp;"
      case "<":
        return "&lt;"
      case ">":
        return "&gt;"
      case '"':
        return "&quot;"
      default:
        return "&#39;"
    }
  })
}

function infoWindowContent(loc: ReportEventLocation): string {
  const name = loc.location_name?.trim() || "Điểm ngập"
  const depthCm = loc.depth_cm == null ? null : Number(loc.depth_cm)
  const depth =
    depthCm != null && Number.isFinite(depthCm)
      ? `<div style="font-size:11px;color:#334155;margin-top:4px;">Sâu ${depthCm} cm</div>`
      : ""
  const label = SEVERITY_LABELS[loc.severity] ?? loc.severity
  return (
    `<div style="font-family:ui-sans-serif,system-ui,sans-serif;max-width:220px;">` +
    `<div style="font-size:13px;font-weight:600;color:#0f172a;">${escapeHtml(name)}</div>` +
    `<div style="font-size:11px;color:#64748b;margin-top:2px;">${escapeHtml(loc.event_date)}</div>` +
    depth +
    `<div style="font-size:11px;font-weight:600;margin-top:4px;color:${severityColor(loc.severity)};">${escapeHtml(label)}</div>` +
    `</div>`
  )
}

/**
 * fitBounds padding so pins are not hidden behind the result sheet.
 * Mobile: bottom sheet covers the lower part of the screen.
 * Desktop: left sheet covers the left part.
 */
function getSheetPadding(): google.maps.Padding {
  if (typeof document === "undefined" || typeof window === "undefined") {
    return { top: 80, bottom: 360, left: 40, right: 40 }
  }

  const sheet = document.querySelector('[data-testid="result-sheet"]')
  if (sheet instanceof HTMLElement) {
    const rect = sheet.getBoundingClientRect()
    if (rect.width > 0 && rect.height > 0) {
      // Desktop left sheet (narrow, anchored to the left edge)
      if (rect.left <= 8 && rect.width < window.innerWidth * 0.6) {
        return { top: 80, bottom: 80, left: rect.width + 40, right: 40 }
      }
      // Mobile bottom sheet (full-width, anchored to the bottom)
      if (rect.left <= 8) {
        return {
          top: 80,
          bottom: Math.min(rect.height, window.innerHeight * 0.7) + 24,
          left: 40,
          right: 40,
        }
      }
    }
  }

  return { top: 80, bottom: 360, left: 40, right: 40 }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function FloodMap({
  isDropdownOpen,
  selectedWard,
  reportEventLocations,
  hideZones,
  activeEventIdx,
  onEventSelect,
}: FloodMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<google.maps.Map | null>(null)
  const wardCircleRef = useRef<google.maps.Circle | null>(null)
  const zoneCirclesRef = useRef<google.maps.Circle[]>([])
  /**
   * Index-aligned with reportEventLocations: entry i is the marker/fallback for
   * event i, or null if nothing could be drawn. Never conditionally pushed.
   */
  const eventMarkersRef = useRef<EventOverlayEntry[]>([])
  const eventPolylinesRef = useRef<google.maps.Polyline[]>([])
  const eventBoundsRef = useRef<google.maps.LatLngBounds | null>(null)
  const infoWindowRef = useRef<google.maps.InfoWindow | null>(null)
  const loaderRef = useRef<Loader | null>(null)
  const mapReadyRef = useRef(false)
  /**
   * Identity of the data the map was last auto-fitted to (selected ward +
   * event coordinates). Re-renders with an unchanged key (e.g. sheet reopen
   * refetching the same report) must not move the user's viewport.
   */
  const lastFitKeyRef = useRef<string | null>(null)
  /** True once the user has zoomed/panned; suppresses automatic re-fits. */
  const userMovedRef = useRef(false)
  const onEventSelectRef = useRef<((index: number) => void) | undefined>(onEventSelect)
  const activeEventIdxRef = useRef<number | null>(activeEventIdx ?? null)
  /** True when the current active index came from clicking a map marker. */
  const selectionFromMarkerRef = useRef(false)
  const isDropdownOpenRef = useRef(isDropdownOpen)
  const hideZonesRef = useRef(hideZones)
  const hasPaidEventsRef = useRef(Boolean(reportEventLocations && reportEventLocations.length > 0))

  const [isLoading, setIsLoading] = useState(true)
  const [mapError, setMapError] = useState<string | null>(null)
  const [zonesReady, setZonesReady] = useState(false)
  /** Number of paid-event overlays actually drawn (markers or fallback dots). */
  const [markerCount, setMarkerCount] = useState(0)
  const pendingZonesRef = useRef<FloodZone[]>([])

  // Keep callback / active index in refs so recreating markers does not depend on them.
  useEffect(() => {
    onEventSelectRef.current = onEventSelect
  }, [onEventSelect])

  useEffect(() => {
    activeEventIdxRef.current = activeEventIdx ?? null
  }, [activeEventIdx])

  useEffect(() => {
    isDropdownOpenRef.current = isDropdownOpen
  }, [isDropdownOpen])

  useEffect(() => {
    hideZonesRef.current = hideZones
  }, [hideZones])

  useEffect(() => {
    hasPaidEventsRef.current = Boolean(reportEventLocations && reportEventLocations.length > 0)
  }, [reportEventLocations])

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
  // Track user-initiated gestures (zoom control, drag-pan, wheel, pinch,
  // double-click) so later re-fits never override the user's viewport.
  // Listeners live on the container (which also holds the zoom control), and
  // programmatic `fitBounds`/`panTo` do not emit these DOM events.
  // -------------------------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const markMoved = () => {
      userMovedRef.current = true
    }

    container.addEventListener("pointerdown", markMoved)
    container.addEventListener("wheel", markMoved, { passive: true })
    container.addEventListener("touchstart", markMoved, { passive: true })
    container.addEventListener("dblclick", markMoved)

    return () => {
      container.removeEventListener("pointerdown", markMoved)
      container.removeEventListener("wheel", markMoved)
      container.removeEventListener("touchstart", markMoved)
      container.removeEventListener("dblclick", markMoved)
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
    // RISK 4: zones must never cover paid event pins — even if /api/flood-zones
    // resolves after the paid report. Teaser no-leak rules still apply.
    // Read live state from refs so toggles don't recreate every circle; the
    // visibility effect below handles later changes.
    const initialVisible =
      !isDropdownOpenRef.current && !hideZonesRef.current && !hasPaidEventsRef.current

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
        visible: initialVisible,
      })
      zoneCirclesRef.current.push(circle)
    })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zonesReady, isLoading])

  // -------------------------------------------------------------------------
  // When report events load (paid state): hide background zone circles, draw
  // one numbered severity-coloured marker per event, and try to draw each
  // unique street as an Overpass polyline in the same colour.
  // When events are cleared: restore zone circles.
  // -------------------------------------------------------------------------
  useEffect(() => {
    let cancelled = false

    const clearOverlays = () => {
      eventMarkersRef.current.forEach((entry) => {
        if (!entry) return
        if (entry.kind === "marker") entry.marker.setMap(null)
        else entry.circle.setMap(null)
      })
      eventMarkersRef.current = []
      eventPolylinesRef.current.forEach((p) => p.setMap(null))
      eventPolylinesRef.current = []
      eventBoundsRef.current = null
      infoWindowRef.current?.close()
      infoWindowRef.current = null
      // Test hooks must never outlive the overlays they describe.
      setMarkerCount(0)
      if (IS_DEV) window.__floodMarkers = undefined
    }

    const cleanup = () => {
      cancelled = true
      clearOverlays()
    }

    // Clear previous event overlays
    clearOverlays()

    if (!reportEventLocations || reportEventLocations.length === 0) {
      // New search / report cleared: the next report must fit its own pins.
      lastFitKeyRef.current = null
      userMovedRef.current = false
      zoneCirclesRef.current.forEach((c) =>
        c.setVisible(!isDropdownOpenRef.current && !hideZones)
      )
      return cleanup
    }

    zoneCirclesRef.current.forEach((c) => c.setVisible(false))

    if (!mapRef.current) return cleanup

    // Fit bounds (ward centroid + events), padded so the result sheet does not hide pins
    const bounds = new google.maps.LatLngBounds()
    if (selectedWard) bounds.extend({ lat: selectedWard.lat, lng: selectedWard.lng })
    reportEventLocations.forEach((loc) => bounds.extend({ lat: loc.lat, lng: loc.lng }))
    eventBoundsRef.current = bounds

    // Only auto-fit when the data actually changed. A sheet reopen remounts
    // FloodReportCard and refetches the same report (new array identity), which
    // must keep the user's zoom/pan instead of resetting it.
    const fitKey = [
      selectedWard ? `${selectedWard.lat},${selectedWard.lng}` : "",
      ...reportEventLocations.map((loc) => `${loc.lat},${loc.lng}`),
    ].join("|")
    if (fitKey !== lastFitKeyRef.current) {
      mapRef.current.fitBounds(bounds, getSheetPadding())
      lastFitKeyRef.current = fitKey
      // A genuine data change owns the viewport; the user has not moved yet.
      userMovedRef.current = false
    }

    // One shared InfoWindow for all pins
    const infoWindow = new google.maps.InfoWindow()
    infoWindowRef.current = infoWindow

    // Step 1: numbered marker per event (small circle fallback if Marker fails).
    // RISK 2: `entries` is index-aligned with reportEventLocations; every index
    // is assigned, so a failed marker cannot shift later pin numbers.
    const entries: EventOverlayEntry[] = reportEventLocations.map(() => null)

    reportEventLocations.forEach((loc, index) => {
      if (cancelled || !mapRef.current) return

      const position = { lat: loc.lat, lng: loc.lng }
      const active = index === activeEventIdxRef.current
      const content = infoWindowContent(loc)

      try {
        const marker = new google.maps.Marker({
          map: mapRef.current,
          position,
          icon: pinIcon(loc.severity, active),
          label: {
            text: String(index + 1),
            color: "#ffffff",
            fontSize: "11px",
            fontWeight: "700",
          },
          title: loc.location_name ?? `Điểm ngập ${index + 1}`,
          zIndex: active ? 1000 : index,
        })

        marker.addListener("click", () => {
          if (!mapRef.current) return
          // Same-index clicks don't re-run the selection effect, so only flag
          // the selection when it actually changes.
          selectionFromMarkerRef.current = activeEventIdxRef.current !== index
          infoWindow.setContent(content)
          infoWindow.open({ map: mapRef.current, anchor: marker })
          onEventSelectRef.current?.(index)
        })

        entries[index] = { kind: "marker", marker, severity: loc.severity, position, content }
      } catch {
        // Marker construction failed — keep a small severity-coloured dot at
        // the same index; the active-pin effect can still pan/open its popup.
        try {
          const circle = new google.maps.Circle({
            map: mapRef.current,
            center: position,
            radius: 80,
            fillColor: severityColor(loc.severity),
            fillOpacity: 0.7,
            strokeColor: severityColor(loc.severity),
            strokeOpacity: 1,
            strokeWeight: 2,
            clickable: true,
          })

          circle.addListener("click", () => {
            if (!mapRef.current) return
            selectionFromMarkerRef.current = activeEventIdxRef.current !== index
            infoWindow.setContent(content)
            infoWindow.open({ map: mapRef.current, anchor: circle })
            onEventSelectRef.current?.(index)
          })

          entries[index] = {
            kind: "fallback",
            circle,
            severity: loc.severity,
            position,
            content,
          }
        } catch {
          // Nothing more we can draw for this event — keep a null placeholder
          entries[index] = null
        }
      }
    })

    eventMarkersRef.current = entries

    // Publish drawn-overlay count + concrete overlays for E2E assertions.
    setMarkerCount(entries.filter((entry) => entry !== null).length)
    if (IS_DEV) {
      // Index-aligned with `reportEventLocations` (null slots preserved) so a
      // test can trigger event i and expect list row i to activate.
      window.__floodMarkers = entries.map((entry) =>
        entry === null ? null : entry.kind === "marker" ? entry.marker : entry.circle
      )
    }

    // Step 2: for each unique location name, try Overpass to get the full street
    // geometry and draw it as a polyline coloured by the worst severity on that street
    const VIET_PREFIX = /^(phố|đường|ngõ|ngách|hẻm|phường|quận|tổ)\s+/iu

    const streetSeverity = new Map<string, string>()
    reportEventLocations.forEach((loc) => {
      const name = loc.location_name?.trim()
      if (!name) return
      const prev = streetSeverity.get(name)
      if (!prev || (SEVERITY_RANK[loc.severity] ?? 0) > (SEVERITY_RANK[prev] ?? 0)) {
        streetSeverity.set(name, loc.severity)
      }
    })

    streetSeverity.forEach((severity, name) => {
      const color = severityColor(severity)
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
              strokeColor: color,
              strokeOpacity: 0.9,
              strokeWeight: 5,
              clickable: false,
            })
            eventPolylinesRef.current.push(poly)
          })
        })
        .catch(() => {}) // markers already drawn — nothing extra needed
    })

    return cleanup
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reportEventLocations, isLoading])

  // -------------------------------------------------------------------------
  // RISK 1: re-fit bounds when the result sheet finishes sliding in / resizes.
  // The sheet height is not final when pins are first drawn, so measure it via
  // ResizeObserver and debounce re-fits into one animation frame.
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (typeof window === "undefined" || typeof ResizeObserver === "undefined") return
    if (!eventBoundsRef.current) return

    const sheet = document.querySelector('[data-testid="result-sheet"]')
    if (!(sheet instanceof HTMLElement)) return

    let frame: number | null = null
    const initialRect = sheet.getBoundingClientRect()
    let lastWidth = initialRect.width
    let lastHeight = initialRect.height

    const refit = () => {
      frame = null
      if (!sheet.isConnected) return
      if (!mapRef.current || !eventBoundsRef.current) return
      // Never override a viewport the user has adjusted themselves.
      if (userMovedRef.current) return
      mapRef.current.fitBounds(eventBoundsRef.current, getSheetPadding())
    }

    const observer = new ResizeObserver((observed) => {
      const rect = observed[0]?.contentRect
      if (!rect) return
      // Coalesce layout jitter: only react to real size changes.
      if (Math.abs(rect.width - lastWidth) < 1 && Math.abs(rect.height - lastHeight) < 1) {
        return
      }
      lastWidth = rect.width
      lastHeight = rect.height
      if (frame !== null) return
      frame = requestAnimationFrame(refit)
    })

    observer.observe(sheet)

    return () => {
      observer.disconnect()
      if (frame !== null) cancelAnimationFrame(frame)
    }
  }, [reportEventLocations, isLoading])

  // -------------------------------------------------------------------------
  // Re-fit bounds when the active pin is cleared (e.g. sheet reopened) so
  // pins stay visible with the sheet's current height.
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (activeEventIdx != null) return
    if (!mapRef.current || !eventBoundsRef.current) return
    if (userMovedRef.current) return
    mapRef.current.fitBounds(eventBoundsRef.current, getSheetPadding())
  }, [activeEventIdx])

  // -------------------------------------------------------------------------
  // Highlight the active pin, pan to it and open its InfoWindow.
  // -------------------------------------------------------------------------
  useEffect(() => {
    const entries = eventMarkersRef.current
    const idx = activeEventIdx ?? null

    entries.forEach((entry, i) => {
      if (!entry) return
      const active = i === idx
      if (entry.kind === "marker") {
        entry.marker.setIcon(pinIcon(entry.severity, active))
        entry.marker.setZIndex(active ? 1000 : i)
      } else {
        // Fallback dots cannot change icon/label — just emphasise them.
        entry.circle.setOptions({
          fillOpacity: active ? 0.95 : 0.7,
          strokeWeight: active ? 4 : 2,
          zIndex: active ? 1000 : i,
        })
      }
    })

    const fromMarker = selectionFromMarkerRef.current
    selectionFromMarkerRef.current = false

    const entry = idx != null ? entries[idx] : null
    if (entry && mapRef.current) {
      mapRef.current.panTo(entry.position)
      // Marker clicks already opened the InfoWindow above. List-row clicks must
      // not open it: moving focus into the map overlay makes the non-modal
      // result sheet dismiss itself (Radix focus-outside).
      if (!fromMarker) infoWindowRef.current?.close()
    } else {
      infoWindowRef.current?.close()
    }
  }, [activeEventIdx])

  // -------------------------------------------------------------------------
  // Hide/show flood zone circles based on dropdown + hideZones prop
  // -------------------------------------------------------------------------
  useEffect(() => {
    if (
      eventMarkersRef.current.some((entry) => entry !== null) ||
      eventPolylinesRef.current.length > 0
    ) {
      return
    }
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
    // Skip when the backend already supplied a precise point for the alias.
    if (
      selectedWard.location_type !== "ward" &&
      selectedWard.location_name &&
      !selectedWard.coords_precise
    ) {
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
      <div
        ref={containerRef}
        className="h-full w-full"
        {...(IS_DEV
          ? { "data-testid": "flood-map", "data-marker-count": markerCount }
          : {})}
      />

      {/* Legend — paid state only (reportEventLocations is empty in teaser).
          Anchored top-right: the result sheet covers the map's top-left on
          desktop, which would hide a left-anchored legend. */}
      {reportEventLocations && reportEventLocations.length > 0 && !mapError && (
        <div className="pointer-events-none absolute right-3 top-14 z-10 rounded-lg bg-white/90 px-3 py-2 shadow-md backdrop-blur-sm">
          <p className="text-xs font-semibold text-slate-700">
            {reportEventLocations.length} điểm ngập trên bản đồ
          </p>
          <ul className="mt-1 space-y-1">
            <li className="flex items-center gap-1.5 text-[11px] text-slate-600">
              <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: SEVERITY_COLORS.severe }} />
              Nghiêm trọng
            </li>
            <li className="flex items-center gap-1.5 text-[11px] text-slate-600">
              <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: SEVERITY_COLORS.moderate }} />
              Trung bình
            </li>
            <li className="flex items-center gap-1.5 text-[11px] text-slate-600">
              <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: SEVERITY_COLORS.minor }} />
              Nhẹ
            </li>
          </ul>
        </div>
      )}

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
