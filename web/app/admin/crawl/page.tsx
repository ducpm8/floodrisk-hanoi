"use client"

import { useEffect, useState } from "react"
import {
  type CrawlSource,
  createSource,
  deleteSource,
  fetchCrawlerClasses,
  fetchSources,
  patchSource,
  triggerCrawl,
} from "@/lib/admin-crawl"
import { fetchSettings, patchMapMode, type MapMode } from "@/lib/admin-settings"

const TYPE_LABEL: Record<string, string> = {
  governance: "🏛️ Chính phủ",
  news: "📰 Tin tức",
  social: "📱 Mạng xã hội",
}

const TYPE_BADGE: Record<string, string> = {
  governance: "bg-blue-50 text-blue-600",
  news: "bg-amber-50 text-amber-700",
  social: "bg-purple-50 text-purple-600",
}

const STATUS_BADGE: Record<string, string> = {
  success: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-600",
  running: "bg-blue-50 text-blue-600",
  partial: "bg-yellow-50 text-yellow-700",
}

export default function AdminCrawlPage() {
  const [sources, setSources] = useState<CrawlSource[]>([])
  const [crawlerClasses, setCrawlerClasses] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [mapMode, setMapMode] = useState<MapMode>("text")
  const [mapModeLoading, setMapModeLoading] = useState(false)
  const [mapModeError, setMapModeError] = useState("")

  // running state per source
  const [running, setRunning] = useState<Record<string, boolean>>({})
  const [runError, setRunError] = useState<Record<string, string>>({})

  // add form
  const [showAdd, setShowAdd] = useState(false)
  const [addForm, setAddForm] = useState({
    source_name: "",
    source_type: "news",
    source_url: "",
    crawler_class: "",
    is_enabled: true,
  })
  const [addError, setAddError] = useState("")
  const [addLoading, setAddLoading] = useState(false)

  // delete confirm
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [deleteLoading, setDeleteLoading] = useState(false)

  async function load() {
    try {
      const [srcs, classes, settings] = await Promise.all([fetchSources(), fetchCrawlerClasses(), fetchSettings()])
      setSources(srcs)
      setCrawlerClasses(classes)
      setMapMode(settings.map_mode)
      // sync running state from server
      const r: Record<string, boolean> = {}
      srcs.forEach((s) => { if (s.last_run_status === "running") r[s.source_name] = true })
      setRunning(r)
    } catch (e) {
      setError("Không thể tải danh sách nguồn.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  async function handleMapModeToggle() {
    const next: MapMode = mapMode === "text" ? "google" : "text"
    setMapModeLoading(true)
    setMapModeError("")
    try {
      const updated = await patchMapMode(next)
      setMapMode(updated.map_mode)
    } catch (e: unknown) {
      setMapModeError(e instanceof Error ? e.message : "Lỗi")
    } finally {
      setMapModeLoading(false)
    }
  }

  async function handleToggle(name: string, current: boolean) {
    try {
      const updated = await patchSource(name, { is_enabled: !current })
      setSources((prev) => prev.map((s) => s.source_name === name ? { ...s, is_enabled: updated.is_enabled } : s))
    } catch { /* ignore */ }
  }

  async function handleTrigger(name: string) {
    setRunning((r) => ({ ...r, [name]: true }))
    setRunError((e) => ({ ...e, [name]: "" }))
    try {
      await triggerCrawl(name)
      // poll after 3s then reload
      setTimeout(() => load(), 3000)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Lỗi"
      setRunning((r) => ({ ...r, [name]: false }))
      setRunError((err) => ({ ...err, [name]: msg }))
    }
  }

  async function handleAdd() {
    setAddError("")
    if (!addForm.source_name || !addForm.source_url || !addForm.crawler_class) {
      setAddError("Vui lòng điền đầy đủ các trường bắt buộc.")
      return
    }
    setAddLoading(true)
    try {
      await createSource(addForm)
      setShowAdd(false)
      setAddForm({ source_name: "", source_type: "news", source_url: "", crawler_class: "", is_enabled: true })
      await load()
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : "Lỗi khi thêm nguồn")
    } finally {
      setAddLoading(false)
    }
  }

  async function handleDelete(name: string) {
    setDeleteLoading(true)
    try {
      await deleteSource(name)
      setConfirmDelete(null)
      await load()
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Lỗi khi xóa nguồn")
    } finally {
      setDeleteLoading(false)
    }
  }

  const grouped = ["governance", "news", "social"].map((type) => ({
    type,
    sources: sources.filter((s) => s.source_type === type),
  }))

  const enabledCount = sources.filter((s) => s.is_enabled).length
  const failedCount = sources.filter((s) => s.last_run_status === "failed").length

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-sm text-slate-500">
        Đang tải...
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-4 py-3 flex items-center gap-2">
        <span className="text-xl">🌊</span>
        <span className="text-sm text-slate-500">Flood Risk HN</span>
        <svg className="w-3 h-3 text-slate-400" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
          <path d="m9 18 6-6-6-6" />
        </svg>
        <span className="text-sm font-medium text-slate-800">Quản lý crawl</span>
      </header>

      <div className="max-w-2xl mx-auto px-4 py-6 space-y-4">
        {/* Page title + add button */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Quản lý Crawl Dữ Liệu</h1>
            <p className="text-xs text-slate-500 mt-0.5">Kích hoạt thủ công sau mưa lớn</p>
          </div>
          <button
            onClick={() => setShowAdd(true)}
            className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-2 rounded-xl shadow-sm transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
            Thêm nguồn
          </button>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-2">
          <div className="bg-white border border-slate-100 rounded-xl p-3">
            <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-0.5">Tổng nguồn</p>
            <p className="text-sm font-semibold text-slate-800">{sources.length}</p>
          </div>
          <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-3">
            <p className="text-[10px] text-emerald-600 uppercase tracking-wide mb-0.5">Đang bật</p>
            <p className="text-sm font-semibold text-emerald-700">{enabledCount}</p>
          </div>
          <div className="bg-red-50 border border-red-100 rounded-xl p-3">
            <p className="text-[10px] text-red-400 uppercase tracking-wide mb-0.5">Lỗi gần nhất</p>
            <p className="text-sm font-semibold text-red-600">{failedCount} nguồn</p>
          </div>
        </div>

        {/* Map mode toggle */}
        <div className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-slate-900">Chế độ bản đồ</p>
              <p className="text-[11px] text-slate-400 mt-0.5">
                {mapMode === "google" ? "🗺️ Google Maps đang bật" : "📋 Danh sách văn bản đang bật"}
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              <span className="text-xs text-slate-500">Văn bản</span>
              <button
                onClick={handleMapModeToggle}
                disabled={mapModeLoading}
                className={`w-11 h-6 rounded-full flex items-center px-0.5 transition-colors disabled:opacity-50 ${mapMode === "google" ? "bg-blue-600" : "bg-slate-200"}`}
                aria-label="Toggle map mode"
              >
                <div className={`w-5 h-5 rounded-full bg-white shadow transition-transform ${mapMode === "google" ? "translate-x-5" : ""}`} />
              </button>
              <span className="text-xs text-slate-500">Google Maps</span>
            </div>
          </div>
          {mapModeError && (
            <p className="text-xs text-red-600 mt-2">{mapModeError}</p>
          )}
        </div>

        {error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl px-4 py-3">
            {error}
          </div>
        )}

        {/* Source groups */}
        {grouped.map(({ type, sources: group }) => (
          group.length === 0 ? null : (
            <div key={type}>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                  {TYPE_LABEL[type]}
                </span>
                <div className="flex-1 h-px bg-slate-200" />
                <span className="text-[10px] text-slate-400">{group.length} nguồn</span>
              </div>

              <div className="space-y-2">
                {group.map((src) => (
                  <SourceRow
                    key={src.source_name}
                    src={src}
                    typeBadge={TYPE_BADGE[src.source_type] ?? ""}
                    isRunning={running[src.source_name] ?? false}
                    runErr={runError[src.source_name] ?? ""}
                    onToggle={() => handleToggle(src.source_name, src.is_enabled)}
                    onTrigger={() => handleTrigger(src.source_name)}
                    onDelete={() => setConfirmDelete(src.source_name)}
                  />
                ))}
              </div>
            </div>
          )
        ))}
      </div>

      {/* Add source sheet */}
      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-end bg-black/40" onClick={() => setShowAdd(false)}>
          <div
            className="w-full bg-white rounded-t-2xl pb-8 shadow-2xl max-w-2xl mx-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex justify-center pt-3 pb-1">
              <div className="w-10 h-1 rounded-full bg-slate-200" />
            </div>
            <div className="px-4 pt-2 pb-4">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-base font-semibold text-slate-900">Thêm nguồn crawl</h2>
                <button onClick={() => setShowAdd(false)} className="w-7 h-7 rounded-full bg-slate-100 flex items-center justify-center">
                  <svg className="w-3.5 h-3.5 text-slate-500" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                    <path d="M18 6 6 18M6 6l12 12" />
                  </svg>
                </button>
              </div>

              <div className="space-y-3">
                <Field label="Tên nguồn *">
                  <input
                    type="text"
                    placeholder="vd: baomoi"
                    value={addForm.source_name}
                    onChange={(e) => setAddForm((f) => ({ ...f, source_name: e.target.value }))}
                    className="input"
                  />
                </Field>

                <Field label="Loại nguồn *">
                  <select
                    value={addForm.source_type}
                    onChange={(e) => setAddForm((f) => ({ ...f, source_type: e.target.value }))}
                    className="input"
                  >
                    <option value="governance">🏛️ Chính phủ</option>
                    <option value="news">📰 Tin tức</option>
                    <option value="social">📱 Mạng xã hội</option>
                  </select>
                </Field>

                <Field label="URL nguồn *">
                  <input
                    type="url"
                    placeholder="vd: https://baomoi.com/lu-lut"
                    value={addForm.source_url}
                    onChange={(e) => setAddForm((f) => ({ ...f, source_url: e.target.value }))}
                    className="input"
                  />
                  <p className="text-[10px] text-slate-400 mt-1">URL gốc truyền vào crawler khi chạy</p>
                </Field>

                <Field label="Crawler *">
                  <select
                    value={addForm.crawler_class}
                    onChange={(e) => setAddForm((f) => ({ ...f, crawler_class: e.target.value }))}
                    className="input"
                  >
                    <option value="">Chọn crawler...</option>
                    {crawlerClasses.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                  <p className="text-[10px] text-slate-400 mt-1">Python class xử lý trang web này</p>
                </Field>

                <div className="flex items-center gap-3 py-1">
                  <button
                    type="button"
                    onClick={() => setAddForm((f) => ({ ...f, is_enabled: !f.is_enabled }))}
                    className={`w-9 h-5 rounded-full flex items-center px-0.5 transition-colors ${addForm.is_enabled ? "bg-emerald-500" : "bg-slate-200"}`}
                  >
                    <div className={`w-4 h-4 rounded-full bg-white shadow transition-transform ${addForm.is_enabled ? "translate-x-4" : ""}`} />
                  </button>
                  <label className="text-xs font-medium text-slate-700">Bật ngay sau khi thêm</label>
                </div>
              </div>

              {addError && <p className="text-xs text-red-600 mt-2">{addError}</p>}

              <div className="flex gap-2 mt-5">
                <button
                  onClick={() => setShowAdd(false)}
                  className="flex-1 border border-slate-200 text-slate-600 text-sm font-medium py-2.5 rounded-xl hover:bg-slate-50"
                >
                  Hủy
                </button>
                <button
                  onClick={handleAdd}
                  disabled={addLoading}
                  className="flex-1 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold py-2.5 rounded-xl disabled:opacity-60"
                >
                  {addLoading ? "Đang thêm..." : "Thêm nguồn"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirm */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-6">
          <div className="w-full max-w-sm bg-white rounded-2xl p-5 shadow-xl">
            <div className="w-10 h-10 rounded-full bg-red-50 flex items-center justify-center mx-auto mb-3">
              <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
              </svg>
            </div>
            <h3 className="text-sm font-semibold text-slate-900 text-center mb-1">Xóa nguồn này?</h3>
            <p className="text-xs text-slate-500 text-center mb-4">
              Xóa <span className="font-mono font-medium">{confirmDelete}</span>. Hành động này không thể hoàn tác.
            </p>
            <div className="flex gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="flex-1 border border-slate-200 text-slate-600 text-sm font-medium py-2.5 rounded-xl"
              >
                Hủy
              </button>
              <button
                onClick={() => handleDelete(confirmDelete)}
                disabled={deleteLoading}
                className="flex-1 bg-red-600 hover:bg-red-700 text-white text-sm font-semibold py-2.5 rounded-xl disabled:opacity-60"
              >
                {deleteLoading ? "Đang xóa..." : "Xóa nguồn"}
              </button>
            </div>
          </div>
        </div>
      )}

      <style jsx>{`
        .input {
          width: 100%;
          border: 1px solid #e2e8f0;
          border-radius: 0.75rem;
          padding: 0.625rem 0.75rem;
          font-size: 0.875rem;
          color: #1e293b;
          background: white;
          outline: none;
        }
        .input:focus {
          ring: 2px;
          border-color: transparent;
          box-shadow: 0 0 0 2px #3b82f6;
        }
      `}</style>
    </div>
  )
}

// ---------------------------------------------------------------------------

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs font-medium text-slate-700 mb-1.5 block">{label}</label>
      {children}
    </div>
  )
}

function SourceRow({
  src,
  typeBadge,
  isRunning,
  runErr,
  onToggle,
  onTrigger,
  onDelete,
}: {
  src: CrawlSource
  typeBadge: string
  isRunning: boolean
  runErr: string
  onToggle: () => void
  onTrigger: () => void
  onDelete: () => void
}) {
  const statusBadge = src.last_run_status ? STATUS_BADGE[src.last_run_status] : ""
  const statusLabel: Record<string, string> = {
    success: "Thành công",
    failed: "Thất bại",
    running: "Đang chạy",
    partial: "Một phần",
  }

  return (
    <div className={`bg-white border rounded-2xl p-3 shadow-sm ${src.last_run_status === "failed" ? "border-red-200" : "border-slate-200"}`}>
      {/* Running indicator */}
      {isRunning && (
        <div className="bg-blue-50 border border-blue-100 rounded-xl px-3 py-2 mb-2.5 flex items-center gap-2">
          <div className="h-3.5 w-3.5 rounded-full border-2 border-blue-500 border-t-transparent animate-spin shrink-0" />
          <p className="text-[10px] text-blue-700 font-medium">Đang crawl...</p>
        </div>
      )}

      {/* Row header */}
      <div className="flex items-start justify-between gap-2 mb-2.5">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`text-sm font-semibold ${src.is_enabled ? "text-slate-900" : "text-slate-500"}`}>
              {src.source_name}
            </span>
            <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${typeBadge}`}>
              {src.source_type}
            </span>
          </div>
          <p className="text-[10px] text-slate-400 mt-0.5 truncate">🔗 {src.source_url.replace(/^https?:\/\//, "")}</p>
        </div>

        {/* Toggle */}
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-[10px] text-slate-500">{src.is_enabled ? "Bật" : "Tắt"}</span>
          <button
            onClick={onToggle}
            className={`w-9 h-5 rounded-full flex items-center px-0.5 transition-colors ${src.is_enabled ? "bg-emerald-500" : "bg-slate-200"}`}
          >
            <div className={`w-4 h-4 rounded-full bg-white shadow transition-transform ${src.is_enabled ? "translate-x-4" : ""}`} />
          </button>
        </div>
      </div>

      <div className="h-px bg-slate-100 mb-2.5" />

      {/* Footer */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          {src.last_run_status && (
            <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium shrink-0 ${statusBadge}`}>
              {statusLabel[src.last_run_status] ?? src.last_run_status}
            </span>
          )}
          {src.last_crawled_at && (
            <span className="text-[10px] text-slate-400 truncate">
              {new Date(src.last_crawled_at).toLocaleDateString("vi-VN")}
              {src.last_run_records != null ? ` · ${src.last_run_records} bản ghi` : ""}
            </span>
          )}
          {!src.last_crawled_at && (
            <span className="text-[10px] text-slate-400">— Chưa crawl lần nào</span>
          )}
          {runErr && <span className="text-[10px] text-red-500 truncate">{runErr}</span>}
        </div>

        <div className="flex items-center gap-1.5 shrink-0">
          {!isRunning && (
            <button
              onClick={onDelete}
              className="w-7 h-7 rounded-lg flex items-center justify-center text-slate-400 hover:text-red-500 hover:bg-red-50 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
              </svg>
            </button>
          )}

          {isRunning ? (
            <button disabled className="text-xs font-medium text-blue-300 bg-blue-50 px-3 py-1.5 rounded-xl cursor-not-allowed flex items-center gap-1.5">
              <div className="h-3 w-3 rounded-full border-2 border-blue-300 border-t-transparent animate-spin" />
              Đang chạy...
            </button>
          ) : src.is_enabled ? (
            <button
              onClick={onTrigger}
              className="text-xs font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 px-3 py-1.5 rounded-xl transition-colors flex items-center gap-1 whitespace-nowrap"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5.636 5.636a9 9 0 1 0 12.728 0M12 3v9" />
              </svg>
              Chạy ngay
            </button>
          ) : (
            <button disabled className="text-xs font-medium text-slate-300 bg-slate-50 px-3 py-1.5 rounded-xl cursor-not-allowed flex items-center gap-1">
              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth="2.5" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5.636 5.636a9 9 0 1 0 12.728 0M12 3v9" />
              </svg>
              Chạy ngay
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
