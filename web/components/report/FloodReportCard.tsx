"use client";

/**
 * FIR-12: Flood report card shown in the bottom sheet after ward selection.
 *
 * States:
 *   loading   — skeleton while fetching teaser
 *   teaser    — free preview, blurred summary, pay button
 *   paying    — spinner while initiating payment
 *   paid      — full report data unlocked
 *   error     — fetch/network failure
 *
 * Payment flow:
 *   1. User clicks "Xem báo cáo đầy đủ (35,000 VND)"
 *   2. POST /api/payment/initiate → redirect to payment_url
 *   3. /payment/return?order_id=... → auto-confirm in demo mode
 *   4. On return, parent re-mounts with lat/lng → fetchFullReport() → paid state
 */

import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { fetchTeaser, fetchFullReport, type TeaserData, type FullReportData } from "@/lib/report";
import { initiatePayment, checkSessionReport } from "@/lib/payment";

const SEVERITY_LABELS: Record<string, string> = {
  minor: "Nhẹ",
  moderate: "Trung bình",
  severe: "Nghiêm trọng",
};

interface Props {
  lat: number;
  lng: number;
  ward: string;
  locationName?: string;
  onReportLoaded?: (locations: { lat: number; lng: number; location_name?: string }[]) => void;
}

type State = "loading" | "teaser" | "paying" | "paid" | "error";

function extractDomain(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

export function FloodReportCard({ lat, lng, ward, locationName, onReportLoaded }: Props) {
  const [state, setState] = useState<State>("loading");
  const [teaser, setTeaser] = useState<TeaserData | null>(null);
  const [report, setReport] = useState<FullReportData | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    setState("loading");
    setTeaser(null);
    setReport(null);

    // Check if session already has a paid report for this location
    checkSessionReport(lat, lng)
      .then(async (check) => {
        if (check.has_paid_report) {
          try {
            const full = await fetchFullReport(lat, lng, locationName);
            setReport(full);
            setState("paid");
            onReportLoaded?.(
              full.flood_events
                .filter((e) => e.lat != null && e.lng != null)
                .map((e) => ({ lat: e.lat!, lng: e.lng!, location_name: e.location_name ?? undefined }))
            );
            return;
          } catch {
            // Fall through to teaser
          }
        }

        try {
          const t = await fetchTeaser(lat, lng);
          setTeaser(t);
          setState("teaser");
        } catch {
          setErrorMsg("Không thể tải dữ liệu lũ lụt.");
          setState("error");
        }
      })
      .catch(() => {
        // API unreachable — show error instead of crashing
        setErrorMsg("Không kết nối được máy chủ. Vui lòng thử lại.");
        setState("error");
      });
  }, [lat, lng, locationName]);

  async function handlePay() {
    setState("paying");
    try {
      const result = await initiatePayment(lat, lng);
      // Redirect to payment URL (demo mode goes straight to /payment/return)
      window.location.href = result.payment_url;
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : "Lỗi thanh toán.");
      setState("error");
    }
  }

  if (state === "loading") {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="p-4 text-sm text-red-600">
        {errorMsg || "Đã có lỗi xảy ra."}
      </div>
    );
  }

  if (state === "paying") {
    return (
      <div className="p-4 flex items-center gap-2 text-sm text-muted-foreground">
        <div className="h-4 w-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
        Đang chuyển đến trang thanh toán...
      </div>
    );
  }

  if (state === "paid" && report) {
    return (
      <div className="space-y-4 p-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-sm">{report.location}</h3>
          <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-green-50 text-green-600 border border-green-200">
            Đã thanh toán
          </span>
        </div>

        {/* Summary */}
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div className="rounded-lg bg-gray-50 p-3">
            <p className="text-muted-foreground text-xs mb-1">Tổng sự kiện</p>
            <p className="font-semibold">{report.summary.total_events}</p>
          </div>
          <div className="rounded-lg bg-gray-50 p-3">
            <p className="text-muted-foreground text-xs mb-1">5 năm gần đây</p>
            <p className="font-semibold">{report.summary.events_last_5_years}</p>
          </div>
          {report.summary.most_severe && (
            <div className="rounded-lg bg-gray-50 p-3">
              <p className="text-muted-foreground text-xs mb-1">Mức nghiêm trọng nhất</p>
              <p className="font-semibold">
                {SEVERITY_LABELS[report.summary.most_severe] ?? report.summary.most_severe}
              </p>
            </div>
          )}
          {report.summary.avg_depth_cm !== null && (
            <div className="rounded-lg bg-gray-50 p-3">
              <p className="text-muted-foreground text-xs mb-1">Độ sâu TB</p>
              <p className="font-semibold">{report.summary.avg_depth_cm} cm</p>
            </div>
          )}
        </div>

        {/* Event list */}
        {report.flood_events.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Lịch sử sự kiện
            </p>
            <div className="space-y-2">
              {report.flood_events.map((e, i) => (
                <div
                  key={i}
                  className="flex items-start justify-between text-sm border-b pb-2 last:border-0"
                >
                  <div>
                    {(e.location_name || e.ward) && (
                      <p className="font-medium">{e.location_name ?? e.ward}</p>
                    )}
                    <p className="text-xs text-muted-foreground">{e.event_date}</p>
                    {e.depth_cm && (
                      <p className="text-xs text-muted-foreground">
                        Sâu {e.depth_cm} cm
                        {e.duration_hours ? ` · ${e.duration_hours}h` : ""}
                      </p>
                    )}
                    {e.source_url && (
                      <a
                        href={e.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-blue-500 underline"
                      >
                        {extractDomain(e.source_url)}
                      </a>
                    )}
                  </div>
                  <span
                    className={`shrink-0 text-xs font-medium px-2 py-0.5 rounded-full border ${
                      e.severity === "severe"
                        ? "border-red-300 text-red-600 bg-red-50"
                        : e.severity === "moderate"
                        ? "border-yellow-300 text-yellow-600 bg-yellow-50"
                        : "border-gray-200 text-gray-500 bg-gray-50"
                    }`}
                  >
                    {SEVERITY_LABELS[e.severity] ?? e.severity}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    );
  }

  // Teaser state
  if (!teaser) return null;

  if (!teaser.has_flood_data) {
    return (
      <div className="p-4">
        <p className="text-sm text-muted-foreground">
          Không có sự kiện ngập lụt nào được ghi nhận tại khu vực này. Dữ liệu có thể chưa đầy đủ.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <div className="space-y-1">
        <h3 className="font-semibold text-sm">{teaser.location}</h3>
        <p className="text-sm text-green-600">✓ Chúng tôi có dữ liệu cho khu vực này</p>
        {teaser.data_year_range && (
          <p className="text-xs text-muted-foreground">
            Dữ liệu ghi nhận từ {teaser.data_year_range}
          </p>
        )}
      </div>

      {/* Blurred full-report teaser */}
      <div className="relative rounded-lg border overflow-hidden">
        <div className="p-3 blur-sm select-none pointer-events-none space-y-2 text-sm">
          <p>Tổng số sự kiện: ██ lần trong 10 năm</p>
          <p>Độ sâu trung bình: ██ cm</p>
          <p>Năm cao nhất: 20██ — ██ sự kiện</p>
          <p>Giá BĐS: ██ triệu/m² (chung cư)</p>
        </div>
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-white/60 backdrop-blur-sm gap-3">
          <p className="text-sm font-medium text-center px-4">
            Xem báo cáo đầy đủ với lịch sử 10 năm và dữ liệu giá BĐS
          </p>
          <Button
            size="sm"
            onClick={handlePay}
            className="bg-blue-600 hover:bg-blue-700 text-white"
          >
            Mở khóa báo cáo · 35.000 VND
          </Button>
        </div>
      </div>
    </div>
  );
}
