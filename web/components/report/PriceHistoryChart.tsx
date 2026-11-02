"use client";

/**
 * FIR-13: Price history line chart for a ward.
 * Fetches /api/price/history?ward=... and renders a Recharts LineChart.
 * Tabs filter by property_type (apartment, house, land).
 */

import { useEffect, useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { fetchPriceHistory, type PricePoint } from "@/lib/price";

const PROPERTY_TYPES = ["apartment", "house", "land"] as const;
type PropertyType = (typeof PROPERTY_TYPES)[number];

const TYPE_LABELS: Record<PropertyType, string> = {
  apartment: "Chung cư",
  house: "Nhà phố",
  land: "Đất nền",
};

const TYPE_COLORS: Record<PropertyType, string> = {
  apartment: "#3b82f6",
  house: "#10b981",
  land: "#f59e0b",
};

interface Props {
  ward: string;
}

export function PriceHistoryChart({ ward }: Props) {
  const [records, setRecords] = useState<PricePoint[]>([]);
  const [activeType, setActiveType] = useState<PropertyType>("apartment");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!ward) return;
    setLoading(true);
    setError(false);
    fetchPriceHistory(ward)
      .then((d) => setRecords(d.records))
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, [ward]);

  const filtered = records.filter((r) => r.property_type === activeType);

  if (loading) {
    return (
      <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
        Đang tải dữ liệu giá...
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-48 flex items-center justify-center text-sm text-muted-foreground">
        Không thể tải dữ liệu giá.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        {PROPERTY_TYPES.map((t) => (
          <button
            key={t}
            onClick={() => setActiveType(t)}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              activeType === t
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-gray-600 border-gray-200 hover:border-blue-300"
            }`}
          >
            {TYPE_LABELS[t]}
          </button>
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="h-40 flex items-center justify-center text-sm text-muted-foreground">
          Chưa có dữ liệu loại bất động sản này.
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={filtered} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis
              dataKey="month"
              tick={{ fontSize: 11 }}
              tickFormatter={(v: string) => v.slice(0, 7)}
            />
            <YAxis
              tick={{ fontSize: 11 }}
              tickFormatter={(v: number) => `${v}M`}
              domain={["auto", "auto"]}
            />
            <Tooltip
              formatter={(v: unknown) => [`${v} triệu/m²`, "Giá"]}
              labelFormatter={(l: unknown) => `Tháng ${l}`}
            />
            <Line
              type="monotone"
              dataKey="price_per_m2_millions"
              stroke={TYPE_COLORS[activeType]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}

      <p className="text-xs text-muted-foreground">
        Đơn vị: triệu đồng/m² · Nguồn: BDS.vn
      </p>
    </div>
  );
}
