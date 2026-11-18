/**
 * Price history API calls for FIR-13.
 */

import { apiFetch } from "./api";

export interface PricePoint {
  month: string;              // "YYYY-MM"
  price_per_m2_millions: number;
  last_updated: string;
  property_type: string;
}

export interface PriceHistory {
  ward: string;
  records: PricePoint[];
  last_updated: string | null;
}

export async function fetchPriceHistory(ward: string): Promise<PriceHistory> {
  const res = await apiFetch(
    `/api/price/history?ward=${encodeURIComponent(ward)}`
  );
  if (!res.ok) {
    throw new Error(`Price history fetch failed: ${res.status}`);
  }
  return res.json();
}
