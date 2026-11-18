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
import type { ReportEventLocation } from "@/lib/report"

interface ResultSheetProps {
  ward: SearchResult | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onReportLoaded?: (locations: ReportEventLocation[]) => void
  activeEventIdx?: number | null
  onEventSelect?: (index: number) => void
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
  activeEventIdx,
  onEventSelect,
}: ResultSheetProps) {
  const isDesktop = useIsDesktop()

  if (!ward) return null

  return (
    <Sheet open={open} onOpenChange={onOpenChange} modal={false}>
      <SheetContent
        side={isDesktop ? "left" : "bottom"}
        data-testid="result-sheet"
        className="overflow-y-auto"
        // Non-modal sheet: map/InfoWindow focus must not dismiss the sheet.
        onFocusOutside={(e) => e.preventDefault()}
        // Desktop: zoom/pan gestures on the map must never dismiss the panel.
        // Phone keeps the original behaviour (tap map minimises to the handle).
        onPointerDownOutside={(e) => {
          if (isDesktop) e.preventDefault()
        }}
        onInteractOutside={(e) => {
          if (isDesktop) e.preventDefault()
        }}
      >
        {/* Ward header */}
        <div className="mb-3 pr-6">
          <SheetTitle data-testid="sheet-ward-name">{ward.location_name}</SheetTitle>
          <SheetDescription>
            {ward.location_type !== "ward" && `${ward.ward} · `}
            {ward.district ? ward.district : "Hà Nội"}
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
          activeEventIdx={activeEventIdx}
          onEventSelect={onEventSelect}
        />
      </SheetContent>
    </Sheet>
  )
}
