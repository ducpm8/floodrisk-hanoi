"use client"

import { useEffect, useState } from "react"
import { FloodMap } from "@/components/map/FloodMap"
import { SearchSection } from "@/components/search/SearchSection"
import { ResultSheet } from "@/components/search/ResultSheet"
import { fetchSettings, type MapMode } from "@/lib/admin-settings"
import type { SearchResult } from "@/lib/search"

const PENDING_WARD_KEY = "pending_ward"

export default function HomePage() {
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [selectedWard, setSelectedWard] = useState<SearchResult | null>(null)
  const [isSheetOpen, setIsSheetOpen] = useState(false)
  const [mapMode, setMapMode] = useState<MapMode>("text")
  const [reportEventLocations, setReportEventLocations] = useState<{ lat: number; lng: number; location_name?: string }[]>([])
  const [hasSearched, setHasSearched] = useState(false)

  useEffect(() => {
    fetchSettings().then((s) => setMapMode(s.map_mode))
  }, [])

  // Restore ward after payment redirect
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get("payment") === "success") {
      const raw = localStorage.getItem(PENDING_WARD_KEY)
      if (raw) {
        try {
          const ward: SearchResult = JSON.parse(raw)
          setSelectedWard(ward)
          setIsSheetOpen(true)
          setHasSearched(true)
        } catch {}
        localStorage.removeItem(PENDING_WARD_KEY)
      }
      window.history.replaceState({}, "", window.location.pathname)
    }
  }, [])

  function handleSelectWard(ward: SearchResult) {
    localStorage.setItem(PENDING_WARD_KEY, JSON.stringify(ward))
    setSelectedWard(ward)
    setReportEventLocations([])
    setIsSheetOpen(true)
    setIsDropdownOpen(false)
  }

  function handleGoHome() {
    setIsSheetOpen(false)
    setSelectedWard(null)
    setReportEventLocations([])
    setHasSearched(false)
  }

  return (
    <div className="relative h-screen w-full overflow-hidden">
      {/* Layer 0: Map background */}
      {mapMode === "google" ? (
        <div className="absolute inset-0 z-0">
          <FloodMap
            isDropdownOpen={isDropdownOpen}
            selectedWard={selectedWard}
            reportEventLocations={reportEventLocations}
            hideZones={isSheetOpen}
          />
        </div>
      ) : (
        <div className="absolute inset-0 bg-slate-900" />
      )}

      {/* Layer 1: Dark overlay — only on true homepage (no ward selected) */}
      {!selectedWard && (
        <div className="absolute inset-0 z-10 bg-black/55" />
      )}

      {/* Layer 3: Nav bar */}
      <nav className="absolute left-0 right-0 top-0 z-30 flex h-12 items-center justify-between px-6">
        <span className="flex items-center gap-1.5 text-sm font-bold text-white/90">
          🌊 Flood Risk Hà Nội
        </span>
        {isSheetOpen ? (
          <button
            type="button"
            onClick={handleGoHome}
            className="flex items-center gap-1.5 rounded-full border border-white/20 bg-white/10 px-3 py-1 text-xs font-medium text-white/80 backdrop-blur-sm transition-colors hover:bg-white/20"
          >
            ← Trang chủ
          </button>
        ) : (
          <span className="rounded-full border border-white/15 bg-white/10 px-2.5 py-0.5 text-xs text-white/60">
            Beta
          </span>
        )}
      </nav>

      {/* Layer 4: Hero — only visible on homepage */}
      {!isSheetOpen && (
        <div className="absolute inset-0 z-20 flex items-start justify-center overflow-y-auto px-4 pb-10 pt-16">
          <div className="w-full max-w-[600px] py-8 text-center">
            {!hasSearched && (
              <>
                <div className="mb-6 inline-flex items-center gap-1.5 rounded-full border border-sky-400/30 bg-sky-400/14 px-3 py-1 text-xs font-semibold tracking-wide text-sky-300">
                  <span className="h-1.5 w-1.5 rounded-full bg-sky-400" />
                  Hà Nội · 579 phường đang được theo dõi
                </div>

                <h1 className="mb-5 text-[2.5rem] font-extrabold leading-tight tracking-tight text-white">
                  Nhà bạn có đang trong vùng ngập?
                </h1>

                <p className="mx-auto mb-8 max-w-[460px] text-base leading-relaxed text-white/70">
                  Tra cứu lịch sử ngập lụt của bất kỳ phường nào tại Hà Nội — trước khi xuống tiền.
                </p>
              </>
            )}

            <SearchSection
              onSelectWard={handleSelectWard}
              onDropdownOpenChange={setIsDropdownOpen}
              onSearchStart={() => setHasSearched(true)}
              variant="hero"
            />

            <p className="mt-3 text-xs text-white/40">
              Nhập tên phường, ngõ hoặc tên đường bất kỳ tại Hà Nội
            </p>
          </div>
        </div>
      )}

      {/* Layer 5: Minimized sheet handle — tap to reopen */}
      {selectedWard && !isSheetOpen && (
        <div className="absolute bottom-0 left-0 right-0 z-40 pointer-events-none">
          <button
            type="button"
            onClick={() => setIsSheetOpen(true)}
            className="mx-auto block max-w-lg w-full cursor-pointer focus:outline-none pointer-events-auto"
          >
          <div className="rounded-t-2xl bg-white px-4 pb-4 pt-2.5 shadow-[0_-4px_24px_rgba(0,0,0,0.18)]">
            <div className="mx-auto mb-2.5 h-1 w-10 rounded-full bg-gray-300" />
            <div className="flex items-center justify-between">
              <div className="text-left">
                <p className="text-sm font-semibold text-gray-900">{selectedWard.location_name}</p>
                <p className="text-xs text-gray-500">
                  {selectedWard.location_type !== "ward" && `${selectedWard.ward} · `}
                  {selectedWard.district && selectedWard.district !== "Unknown" ? selectedWard.district : "Hà Nội"}
                </p>
              </div>
              <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
              </svg>
            </div>
          </div>
          </button>
        </div>
      )}

      {/* Layer 6: ResultSheet */}
      <ResultSheet
        ward={selectedWard}
        open={isSheetOpen}
        onOpenChange={setIsSheetOpen}
        onReportLoaded={setReportEventLocations}
      />
    </div>
  )
}
