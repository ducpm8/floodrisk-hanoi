"use client"

import { useEffect, useState } from "react"
import {
  Sheet,
  SheetContent,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { Separator } from "@/components/ui/separator"
import { FloodReportCard } from "@/components/report/FloodReportCard"
import type { SearchResult } from "@/lib/search"

interface ResultSheetProps {
  ward: SearchResult | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onReportLoaded?: (locations: { lat: number; lng: number; location_name?: string }[]) => void
}

/** Returns true when viewport width ≥ 1024px (Tailwind lg). */
function useIsDesktop() {
  const [isDesktop, setIsDesktop] = useState(false)
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)")
    setIsDesktop(mq.matches)
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches)
    mq.addEventListener("change", handler)
    return () => mq.removeEventListener("change", handler)
  }, [])
  return isDesktop
}

export function ResultSheet({
  ward,
  open,
  onOpenChange,
  onReportLoaded,
}: ResultSheetProps) {
  const isDesktop = useIsDesktop()

  if (!ward) return null

  return (
    <Sheet open={open} onOpenChange={onOpenChange} modal={false}>
      <SheetContent
        side={isDesktop ? "left" : "bottom"}
        data-testid="result-sheet"
        className="overflow-y-auto"
      >
        {/* Ward header */}
        <div className="mb-3 pr-6">
          <SheetTitle data-testid="sheet-ward-name">{ward.location_name}</SheetTitle>
          <SheetDescription>
            {ward.location_type !== "ward" && `${ward.ward} · `}
            {ward.district && ward.district !== "Unknown" ? ward.district : "Hà Nội"}
          </SheetDescription>
        </div>

        <Separator className="mb-2" />

        {/* Flood report card — teaser + payment gate + full report */}
        <FloodReportCard
          lat={ward.lat}
          lng={ward.lng}
          ward={ward.ward_normalized}
          locationName={ward.location_type !== "ward" ? ward.location_name : undefined}
          onReportLoaded={onReportLoaded}
        />
      </SheetContent>
    </Sheet>
  )
}
