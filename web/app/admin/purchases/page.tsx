"use client"

import { useEffect, useState } from "react"
import {
  type PurchaseItem,
  type StatusFilter,
  exportPurchasesCSV,
  fetchPurchases,
} from "@/lib/admin-purchases"

const STATUS_BADGE: Record<string, string> = {
  paid: "bg-emerald-50 text-emerald-700",
  pending: "bg-yellow-50 text-yellow-700",
  failed: "bg-red-50 text-red-600",
}

const STATUS_LABEL: Record<string, string> = {
  paid: "Đã thanh toán",
  pending: "Chờ thanh toán",
  failed: "Thất bại",
}

const GATEWAY_LABEL: Record<string, string> = {
  momo: "MoMo",
  vnpay: "VNPay",
}

const PAGE_SIZE = 50

export default function AdminPurchasesPage() {
  const [items, setItems] = useState<PurchaseItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<StatusFilter>("all")
  const [q, setQ] = useState("")
  const [qInput, setQInput] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [exportLoading, setExportLoading] = useState(false)
  const [selected, setSelected] = useState<PurchaseItem | null>(null)

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  async function load(p: number, s: StatusFilter, search: string) {
    setLoading(true)
    setError("")
    try {
      const data = await fetchPurchases(p, PAGE_SIZE, s, search)
      setItems(data.items)
      setTotal(data.total)
    } catch {
      setError("Không thể tải dữ liệu.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(page, status, q)
  }, [page, status, q])

  function handleStatusChange(s: StatusFilter) {
    setStatus(s)
    setPage(1)
  }

  function handleSearch() {
    setQ(qInput)
    setPage(1)
  }

  async function handleExport() {
    setExportLoading(true)
    try {
      await exportPurchasesCSV(status)
    } catch {
      alert("Xuất CSV thất bại.")
    } finally {
      setExportLoading(false)
    }
  }

  const paidCount = items.filter((i) => i.status === "paid").length
  const pendingCount = items.filter((i) => i.status === "pending").length
  const failedCount = items.filter((i) => i.status === "failed").length
  const totalRevenue = items
    .filter((i) => i.status === "paid")
    .reduce((sum, i) => sum + i.amount_vnd, 0)

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-4 py-3 flex items-center gap-2">
        <span className="text-xl">🌊</span>
        <span className="text-sm text-slate-500">Flood Risk HN</span>
        <svg className="w-3 h-3 text-slate-400" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
          <path d="m9 18 6-6-6-6" />
        </svg>
        <span className="text-sm font-medium text-slate-800">Báo cáo mua báo cáo</span>
      </header>

      <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
        {/* Title + Export */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Lịch sử mua báo cáo</h1>
            <p className="text-xs text-slate-500 mt-0.5">Tổng {total} giao dịch</p>
          </div>
          <button
            onClick={handleExport}
            disabled={exportLoading}
            className="flex items-center gap-1.5 bg-slate-800 hover:bg-slate-900 text-white text-xs font-semibold px-3 py-2 rounded-xl shadow-sm transition-colors disabled:opacity-60"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
            </svg>
            {exportLoading ? "Đang xuất..." : "Xuất CSV"}
          </button>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <div className="bg-white border border-slate-100 rounded-xl p-3">
            <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">Tổng giao dịch</p>
            <p className="text-sm font-semibold text-slate-800">{total}</p>
          </div>
          <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-3">
            <p className="text-[10px] text-emerald-600 uppercase tracking-wide mb-0.5">Doanh thu (trang hiện tại)</p>
            <p className="text-sm font-semibold text-emerald-700">{totalRevenue.toLocaleString("vi-VN")}đ</p>
          </div>
          <div className="bg-yellow-50 border border-yellow-100 rounded-xl p-3">
            <p className="text-[10px] text-yellow-600 uppercase tracking-wide mb-0.5">Chờ thanh toán</p>
            <p className="text-sm font-semibold text-yellow-700">{pendingCount}</p>
          </div>
          <div className="bg-red-50 border border-red-100 rounded-xl p-3">
            <p className="text-[10px] text-red-400 uppercase tracking-wide mb-0.5">Thất bại</p>
            <p className="text-sm font-semibold text-red-600">{failedCount}</p>
          </div>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap gap-2">
          <div className="flex gap-1 bg-white border border-slate-200 rounded-xl p-1">
            {(["all", "paid", "pending", "failed"] as StatusFilter[]).map((s) => (
              <button
                key={s}
                onClick={() => handleStatusChange(s)}
                className={`text-xs font-medium px-3 py-1.5 rounded-lg transition-colors ${
                  status === s
                    ? "bg-slate-800 text-white"
                    : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                {s === "all" ? "Tất cả" : STATUS_LABEL[s]}
              </button>
            ))}
          </div>

          <div className="flex flex-1 min-w-48 gap-2">
            <input
              type="text"
              placeholder="Tìm email hoặc phường..."
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              className="flex-1 border border-slate-200 rounded-xl px-3 py-1.5 text-xs text-slate-800 bg-white outline-none focus:border-blue-400"
            />
            <button
              onClick={handleSearch}
              className="text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 px-3 py-1.5 rounded-xl transition-colors"
            >
              Tìm
            </button>
          </div>
        </div>

        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-3">
            {error}
          </div>
        )}

        {/* Table */}
        {loading ? (
          <div className="flex items-center justify-center h-40 text-sm text-slate-400">
            Đang tải...
          </div>
        ) : items.length === 0 ? (
          <div className="flex items-center justify-center h-40 text-sm text-slate-400">
            Không có giao dịch nào.
          </div>
        ) : (
          <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-slate-100 bg-slate-50">
                    <th className="text-left px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide text-[10px]">Thời gian</th>
                    <th className="text-left px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide text-[10px]">Email</th>
                    <th className="text-left px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide text-[10px]">Phường</th>
                    <th className="text-left px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide text-[10px]">Gateway</th>
                    <th className="text-right px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide text-[10px]">Số tiền</th>
                    <th className="text-left px-4 py-2.5 font-semibold text-slate-500 uppercase tracking-wide text-[10px]">Trạng thái</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {items.map((item) => (
                    <tr
                      key={item.id}
                      onClick={() => setSelected(item)}
                      className="hover:bg-slate-50 cursor-pointer transition-colors"
                    >
                      <td className="px-4 py-3 text-slate-500 whitespace-nowrap">
                        {item.created_at
                          ? new Date(item.created_at).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" })
                          : "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-800 font-medium max-w-40 truncate">
                        {item.email}
                      </td>
                      <td className="px-4 py-3 text-slate-600 max-w-40 truncate">
                        {item.ward_normalized || item.location_query || "—"}
                      </td>
                      <td className="px-4 py-3 text-slate-500">
                        {item.payment_gateway ? (GATEWAY_LABEL[item.payment_gateway] ?? item.payment_gateway) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right font-medium text-slate-800">
                        {item.amount_vnd.toLocaleString("vi-VN")}đ
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_BADGE[item.status] ?? ""}`}>
                          {STATUS_LABEL[item.status] ?? item.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Trang {page} / {totalPages}</span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1.5 border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50"
              >
                Trước
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="px-3 py-1.5 border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50"
              >
                Sau
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Detail modal */}
      {selected && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
          onClick={() => setSelected(null)}
        >
          <div
            className="w-full max-w-lg bg-white rounded-2xl shadow-xl p-5 max-h-[85vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-semibold text-slate-900">Chi tiết giao dịch</h2>
              <button
                onClick={() => setSelected(null)}
                className="w-7 h-7 rounded-full bg-slate-100 flex items-center justify-center"
              >
                <svg className="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                  <path d="M18 6 6 18M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="space-y-2.5 text-xs">
              <DetailRow label="ID" value={selected.id} mono />
              <DetailRow label="Session ID" value={selected.session_id} mono />
              <DetailRow label="Email" value={selected.email} />
              <DetailRow label="Trạng thái">
                <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${STATUS_BADGE[selected.status] ?? ""}`}>
                  {STATUS_LABEL[selected.status] ?? selected.status}
                </span>
              </DetailRow>
              <DetailRow label="Phường" value={selected.ward_normalized || "—"} />
              <DetailRow label="Địa điểm" value={selected.location_query || "—"} />
              {selected.location_lat && selected.location_lng && (
                <DetailRow label="Tọa độ" value={`${selected.location_lat}, ${selected.location_lng}`} mono />
              )}
              <DetailRow label="Gateway" value={selected.payment_gateway ? (GATEWAY_LABEL[selected.payment_gateway] ?? selected.payment_gateway) : "—"} />
              <DetailRow label="Payment ID" value={selected.payment_id || "—"} mono />
              <DetailRow label="Số tiền" value={`${selected.amount_vnd.toLocaleString("vi-VN")}đ`} />
              {selected.failure_reason && (
                <DetailRow label="Lý do thất bại" value={selected.failure_reason} />
              )}
              <DetailRow label="Tạo lúc" value={selected.created_at ? new Date(selected.created_at).toLocaleString("vi-VN") : "—"} />
              <DetailRow label="Thanh toán lúc" value={selected.paid_at ? new Date(selected.paid_at).toLocaleString("vi-VN") : "—"} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function DetailRow({
  label,
  value,
  mono,
  children,
}: {
  label: string
  value?: string
  mono?: boolean
  children?: React.ReactNode
}) {
  return (
    <div className="flex gap-3">
      <span className="w-32 shrink-0 text-slate-400 font-medium">{label}</span>
      {children ?? (
        <span className={`text-slate-800 break-all ${mono ? "font-mono" : ""}`}>{value}</span>
      )}
    </div>
  )
}
