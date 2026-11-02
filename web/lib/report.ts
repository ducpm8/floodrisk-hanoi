/**
 * Flood report API calls for FIR-12.
 * teaser: free, no auth.
 * fullReport: requires paid session — server returns 402 if not paid.
 */

import { getOrCreateSessionId } from "./session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface TeaserData {
  location: string;
  has_flood_data: boolean;
  data_year_range: string | null;
}

export async function fetchTeaser(lat: number, lng: number): Promise<TeaserData> {
  const res = await fetch(`${API}/api/flood/teaser?lat=${lat}&lng=${lng}`);
  if (!res.ok) {
    throw new Error(`Teaser fetch failed: ${res.status}`);
  }
  return res.json();
}

export interface FloodEventDetail {
  event_date: string;
  severity: string;
  depth_cm: number | null;
  duration_hours: number | null;
  source: string | null;
  source_url: string | null;
  ward: string | null;
  location_name: string | null;
  lat: number | null;
  lng: number | null;
}

export interface ReportSummary {
  total_events: number;
  events_last_5_years: number;
  most_severe: string | null;
  avg_depth_cm: number | null;
}

export interface FullReportData {
  location: string;
  flood_events: FloodEventDetail[];
  summary: ReportSummary;
}

export async function fetchFullReport(
  lat: number,
  lng: number,
  locationName?: string,
): Promise<FullReportData> {
  const sessionId = getOrCreateSessionId();
  const res = await fetch(`${API}/api/flood/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      lat,
      lng,
      location_name: locationName ?? null,
    }),
  });

  if (res.status === 402) {
    throw new Error("PAYMENT_REQUIRED");
  }

  if (!res.ok) {
    throw new Error(`Report fetch failed: ${res.status}`);
  }

  return res.json();
}
