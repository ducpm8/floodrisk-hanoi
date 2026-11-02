"use client"

import { useEffect, useState } from "react"
import { haversineMetres, formatDistance } from "@/lib/geo"
import type { SearchResult } from "@/lib/search"

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"

interface FloodZone {
  id: string
  lat: number
  lng: number
  severity: string
  ward: string
  ward_vi: string | null
  district: string | null
  event_date: string
  cause: string | null
}

interface NearbyZone extends FloodZone {
  distanceM: number
}

const SEVERITY_LABEL: Record<string, string> = {
  severe: "Nghiêm trọng",
  moderate: "Trung bình",
  minor: "Nhẹ",
}

const SEVERITY_DOT: Record<string, string> = {
  severe: "bg-red-500",
  moderate: "bg-orange-500",
  minor: "bg-yellow-400",
}

const SEVERITY_ROW: Record<string, string> = {
  severe: "border-red-100 bg-red-50",
  moderate: "border-orange-100 bg-orange-50",
  minor: "border-yellow-100 bg-yellow-50",
}

const TOP_N = 5
const RADIUS_M = 3000

export function ProximityList({ ward }: { ward: SearchResult | null }) {
  const [zones, setZones] = useState<FloodZone[]>([])
  const [loading, setLoading] = useState(false)
  const [fetchError, setFetchError] = useState(false)
  const [retryNonce, setRetryNonce] = useState(0)

  useEffect(() => {
    setLoading(true)
    setFetchError(false)
    fetch(`${API}/api/flood-zones`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((data) => setZones(data.zones ?? []))
      .catch(() => {
        setZones([])
        setFetchError(true)
      })
      .finally(() => setLoading(false))
  }, [retryNonce])

  // FIR-77: explicit error state with retry CTA when API is unreachable.
  if (fetchError) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-3 px-4 text-center">
        <svg className="w-8 h-8 text-amber-500" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
        </svg>
        <p className="text-sm font-medium text-slate-800">Không thể tải dữ liệu lụt</p>
        <p className="text-xs text-slate-500">Kiểm tra kết nối mạng rồi thử lại.</p>
        <button
          type="button"
          onClick={() => setRetryNonce((n) => n + 1)}
          className="mt-1 rounded-full bg-sky-600 px-4 py-1.5 text-xs font-semibold text-white hover:bg-sky-700 transition-colors"
        >
          Thử lại
        </button>
      </div>
    )
  }

  if (!ward) {
    return null
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10 text-sm text-slate-400">
        Đang tải...
      </div>
    )
  }

  const nearby: NearbyZone[] = zones
    .filter((z) => z.lat != null && z.lng != null)
    .map((z) => ({
      ...z,
      distanceM: haversineMetres(ward.lat, ward.lng, z.lat, z.lng),
    }))
    .filter((z) => z.distanceM <= RADIUS_M)
    .sort((a, b) => a.distanceM - b.distanceM)
    .slice(0, TOP_N)

  if (nearby.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-10 gap-2 text-slate-400">
        <svg className="w-8 h-8 opacity-30" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
        </svg>
        <p className="text-sm">Không có điểm ngập lụt trong bán kính 3 km</p>
      </div>
    )
  }

  return (
    <div className="space-y-2 px-1">
      <p className="text-[11px] text-slate-400 uppercase tracking-wide font-medium mb-3">
        {nearby.length} điểm ngập gần nhất · bán kính 3 km
      </p>
      {nearby.map((z) => (
        <div
          key={z.id}
          className={`border rounded-xl px-3 py-2.5 flex items-start gap-3 ${SEVERITY_ROW[z.severity] ?? "border-slate-100 bg-white"}`}
        >
          <div className="mt-1 shrink-0">
            <span className={`inline-block w-2.5 h-2.5 rounded-full ${SEVERITY_DOT[z.severity] ?? "bg-slate-300"}`} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-slate-800 truncate">
              {z.ward_vi ?? z.ward}
            </p>
            <p className="text-[11px] text-slate-500 mt-0.5">
              {z.district && z.district !== "Unknown" ? z.district : "Hà Nội"} · {new Date(z.event_date).toLocaleDateString("vi-VN")}
            </p>
            {z.cause && (
              <p className="text-[10px] text-slate-400 mt-0.5 truncate" title={z.cause}>
                {z.cause}
              </p>
            )}
          </div>
          <div className="text-right shrink-0">
            <p className="text-xs font-semibold text-slate-700">{formatDistance(z.distanceM)}</p>
            <p className={`text-[10px] font-medium mt-0.5 ${z.severity === "severe" ? "text-red-600" : z.severity === "moderate" ? "text-orange-600" : "text-yellow-600"}`}>
              {SEVERITY_LABEL[z.severity] ?? z.severity}
            </p>
          </div>
        </div>
      ))}
      <p className="text-[10px] text-slate-400 pt-1 leading-snug">
        Chỉ hiển thị sự kiện ngập có ghi nhận độ sâu. Tần suất ngập thực tế có thể cao hơn.
      </p>
    </div>
  )
}
