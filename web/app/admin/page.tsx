"use client"

import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  IngestApiError,
  MAX_FILE_BYTES,
  MAX_TEXT_CHARS,
  ingestFile,
  ingestText,
  type IngestResponse,
  type IngestStats,
} from "@/lib/admin-ingest"

type SubmitState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "success"; result: IngestResponse }
  | { kind: "error"; message: string }

const STATUS_LABEL: Record<string, string> = {
  processed: "Đã xử lý",
  queued: "Đang chờ xử lý",
  error: "Lỗi xử lý",
}

const STAT_LABELS: ReadonlyArray<readonly [keyof IngestStats, string]> = [
  ["total", "Sự kiện trích xuất"],
  ["saved", "Đã lưu"],
  ["duplicate", "Trùng lặp"],
  ["ward_error", "Lỗi phường"],
  ["db_error", "Lỗi CSDL"],
  ["low_confidence", "Tin cậy thấp"],
  ["no_depth", "Thiếu độ sâu"],
]

const INPUT_CLASS =
  "w-full border border-slate-200 rounded-xl px-3 py-2 text-sm text-slate-800 bg-white outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-100"

const ALLOWED_EXT_RE = /\.(txt|html|htm)$/i

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function describeError(err: unknown): string {
  if (err instanceof IngestApiError) {
    switch (err.status) {
      case 403:
        return `Không có quyền truy cập (403). Kiểm tra NEXT_PUBLIC_ADMIN_TOKEN.${err.detail ? ` Chi tiết: ${err.detail}` : ""}`
      case 404:
        return "Không tìm thấy endpoint (404). API có thể chưa bật ENABLE_CRAWL_ROUTERS=true."
      case 422:
        return `Dữ liệu không hợp lệ (422).${err.detail ? ` ${err.detail}` : ""}`
      default:
        return `Lỗi ${err.status}.${err.detail ? ` ${err.detail}` : ""}`
    }
  }
  if (err instanceof TypeError) {
    return "Không thể kết nối tới API. Kiểm tra máy chủ đang chạy tại NEXT_PUBLIC_API_URL."
  }
  return err instanceof Error ? err.message : "Lỗi không xác định."
}

export default function AdminIngestPage() {
  // --- Paste text form -----------------------------------------------------
  const [content, setContent] = useState("")
  const [textSourceUrl, setTextSourceUrl] = useState("")
  const [textEventDate, setTextEventDate] = useState("")
  const [textState, setTextState] = useState<SubmitState>({ kind: "idle" })

  const textLoading = textState.kind === "loading"
  const contentTooLong = content.length > MAX_TEXT_CHARS
  const textCanSubmit = content.trim().length > 0 && !contentTooLong && !textLoading

  async function handleTextSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!textCanSubmit) return
    setTextState({ kind: "loading" })
    try {
      const result = await ingestText({
        content,
        ...(textSourceUrl.trim() ? { source_url: textSourceUrl.trim() } : {}),
        ...(textEventDate ? { event_date: textEventDate } : {}),
      })
      setTextState({ kind: "success", result })
      setContent("")
    } catch (err) {
      setTextState({ kind: "error", message: describeError(err) })
    }
  }

  // --- Upload file form ----------------------------------------------------
  const [file, setFile] = useState<File | null>(null)
  const [fileSourceUrl, setFileSourceUrl] = useState("")
  const [fileEventDate, setFileEventDate] = useState("")
  const [fileState, setFileState] = useState<SubmitState>({ kind: "idle" })
  const fileInputRef = useRef<HTMLInputElement>(null)

  const fileLoading = fileState.kind === "loading"
  const fileTooBig = file !== null && file.size > MAX_FILE_BYTES
  const fileBadExt = file !== null && !ALLOWED_EXT_RE.test(file.name)
  const fileIssue = fileTooBig
    ? `Tệp vượt quá giới hạn ${formatBytes(MAX_FILE_BYTES)}.`
    : fileBadExt
      ? "Chỉ chấp nhận tệp .txt, .html hoặc .htm."
      : ""
  const fileCanSubmit = file !== null && !fileTooBig && !fileBadExt && !fileLoading

  async function handleFileSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!file || !fileCanSubmit) return
    setFileState({ kind: "loading" })
    try {
      const result = await ingestFile(file, {
        ...(fileSourceUrl.trim() ? { source_url: fileSourceUrl.trim() } : {}),
        ...(fileEventDate ? { event_date: fileEventDate } : {}),
      })
      setFileState({ kind: "success", result })
      setFile(null)
      if (fileInputRef.current) fileInputRef.current.value = ""
    } catch (err) {
      setFileState({ kind: "error", message: describeError(err) })
    }
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
        <span className="text-sm font-medium text-slate-800">Nhập dữ liệu thủ công</span>
      </header>

      <div className="max-w-2xl mx-auto px-4 py-6 space-y-4">
        <div>
          <h1 className="text-lg font-semibold text-slate-900">Nhập dữ liệu ngập thủ công</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Dán văn bản hoặc tải tệp để trích xuất sự kiện ngập vào hàng chờ staging.
          </p>
          <a
            href="/admin/import"
            className="inline-block mt-1 text-xs text-blue-600 underline"
          >
            Nhập hàng loạt địa điểm bằng CSV →
          </a>
        </div>

        {/* 1. Paste text */}
        <section aria-labelledby="paste-heading" className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-6 h-6 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center text-xs font-semibold">
              1
            </span>
            <h2 id="paste-heading" className="text-sm font-semibold text-slate-900">
              Dán văn bản
            </h2>
          </div>

          <form onSubmit={handleTextSubmit} className="space-y-3" noValidate>
            <div>
              <label htmlFor="paste-content" className="text-xs font-medium text-slate-700 mb-1.5 block">
                Nội dung bài viết <span className="text-red-500">*</span>
              </label>
              <textarea
                id="paste-content"
                rows={7}
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="Dán nội dung bài báo hoặc mô tả ngập tại đây..."
                className={`${INPUT_CLASS} resize-y`}
                aria-invalid={contentTooLong || undefined}
                aria-describedby={
                  contentTooLong ? "paste-content-help paste-content-count paste-content-error" : "paste-content-help paste-content-count"
                }
              />
              <div className="flex items-start justify-between gap-2 mt-1">
                <p id="paste-content-help" className="text-[10px] text-slate-400">
                  Tối đa {MAX_TEXT_CHARS.toLocaleString("vi-VN")} ký tự. Hỗ trợ tiếng Việt có dấu.
                </p>
                <p
                  id="paste-content-count"
                  className={`text-[10px] shrink-0 ${contentTooLong ? "text-red-600 font-medium" : "text-slate-400"}`}
                >
                  {content.length.toLocaleString("vi-VN")} / {MAX_TEXT_CHARS.toLocaleString("vi-VN")}
                </p>
              </div>
              {contentTooLong && (
                <p id="paste-content-error" role="alert" className="text-xs text-red-600 mt-1">
                  Nội dung vượt quá {MAX_TEXT_CHARS.toLocaleString("vi-VN")} ký tự. Vui lòng rút gọn hoặc chia nhỏ.
                </p>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="paste-source-url" className="text-xs font-medium text-slate-700 mb-1.5 block">
                  URL nguồn (không bắt buộc)
                </label>
                <input
                  id="paste-source-url"
                  type="url"
                  value={textSourceUrl}
                  onChange={(e) => setTextSourceUrl(e.target.value)}
                  placeholder="https://..."
                  className={INPUT_CLASS}
                />
              </div>
              <div>
                <label htmlFor="paste-event-date" className="text-xs font-medium text-slate-700 mb-1.5 block">
                  Ngày sự kiện (không bắt buộc)
                </label>
                <input
                  id="paste-event-date"
                  type="date"
                  value={textEventDate}
                  onChange={(e) => setTextEventDate(e.target.value)}
                  className={INPUT_CLASS}
                />
              </div>
            </div>

            {textState.kind === "error" && (
              <p id="paste-submit-error" role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-xl px-3 py-2">
                {textState.message}
              </p>
            )}

            <div className="flex items-center gap-2 pt-1">
              <Button
                type="submit"
                variant="primary"
                disabled={!textCanSubmit}
                aria-describedby={textState.kind === "error" ? "paste-submit-error" : undefined}
                className="rounded-xl"
              >
                {textLoading ? "Đang gửi..." : "Gửi văn bản"}
              </Button>
              {!textLoading && content.trim().length === 0 && (
                <span className="text-[10px] text-slate-400">Nhập nội dung để bật nút gửi.</span>
              )}
            </div>
          </form>

          <ResultPanel state={textState} />
        </section>

        {/* 2. Upload file */}
        <section aria-labelledby="upload-heading" className="bg-white border border-slate-200 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-3">
            <span className="w-6 h-6 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center text-xs font-semibold">
              2
            </span>
            <h2 id="upload-heading" className="text-sm font-semibold text-slate-900">
              Tải tệp lên
            </h2>
          </div>

          <form onSubmit={handleFileSubmit} className="space-y-3" noValidate>
            <div>
              <label htmlFor="upload-file" className="text-xs font-medium text-slate-700 mb-1.5 block">
                Tệp văn bản <span className="text-red-500">*</span>
              </label>
              <input
                ref={fileInputRef}
                id="upload-file"
                type="file"
                accept=".txt,.html,.htm"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null)
                  setFileState({ kind: "idle" })
                }}
                className="block w-full text-xs text-slate-600 border border-slate-200 rounded-xl bg-white px-3 py-2 file:mr-3 file:rounded-lg file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-blue-600 hover:file:bg-blue-100"
                aria-invalid={fileIssue ? true : undefined}
                aria-describedby={fileIssue ? "upload-file-help upload-file-error" : "upload-file-help"}
              />
              <p id="upload-file-help" className="text-[10px] text-slate-400 mt-1">
                {file
                  ? `${file.name} · ${formatBytes(file.size)}`
                  : `Chấp nhận .txt, .html, .htm · tối đa ${formatBytes(MAX_FILE_BYTES)}.`}
              </p>
              {fileIssue && (
                <p id="upload-file-error" role="alert" className="text-xs text-red-600 mt-1">
                  {fileIssue}
                </p>
              )}
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="upload-source-url" className="text-xs font-medium text-slate-700 mb-1.5 block">
                  URL nguồn (không bắt buộc)
                </label>
                <input
                  id="upload-source-url"
                  type="url"
                  value={fileSourceUrl}
                  onChange={(e) => setFileSourceUrl(e.target.value)}
                  placeholder="https://..."
                  className={INPUT_CLASS}
                />
              </div>
              <div>
                <label htmlFor="upload-event-date" className="text-xs font-medium text-slate-700 mb-1.5 block">
                  Ngày sự kiện (không bắt buộc)
                </label>
                <input
                  id="upload-event-date"
                  type="date"
                  value={fileEventDate}
                  onChange={(e) => setFileEventDate(e.target.value)}
                  className={INPUT_CLASS}
                />
              </div>
            </div>

            {fileState.kind === "error" && (
              <p id="upload-submit-error" role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-xl px-3 py-2">
                {fileState.message}
              </p>
            )}

            <div className="flex items-center gap-2 pt-1">
              <Button
                type="submit"
                variant="primary"
                disabled={!fileCanSubmit}
                aria-describedby={fileState.kind === "error" ? "upload-submit-error" : undefined}
                className="rounded-xl"
              >
                {fileLoading ? "Đang tải lên..." : "Tải lên và xử lý"}
              </Button>
              {!fileLoading && !file && (
                <span className="text-[10px] text-slate-400">Chọn tệp để bật nút tải lên.</span>
              )}
            </div>
          </form>

          <ResultPanel state={fileState} />
        </section>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

function ResultPanel({ state }: { state: SubmitState }) {
  if (state.kind !== "success") return null

  const { result } = state
  const isError = result.status === "error"
  const statusLabel = STATUS_LABEL[result.status] ?? result.status
  const statEntries: Array<{ label: string; value: number }> = []
  for (const [key, label] of STAT_LABELS) {
    const value = result.stats?.[key]
    if (typeof value === "number") statEntries.push({ label, value })
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={`mt-3 rounded-xl border px-3 py-3 ${isError ? "bg-amber-50 border-amber-200" : "bg-emerald-50 border-emerald-200"}`}
    >
      <div className="flex items-center gap-2 flex-wrap">
        <p className={`text-sm font-semibold ${isError ? "text-amber-800" : "text-emerald-800"}`}>
          {isError ? "Đã nhận dữ liệu nhưng trích xuất lỗi" : result.status === "queued" ? "Đã xếp hàng xử lý" : "Đã nhận dữ liệu"}
        </p>
        <span
          className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${isError ? "bg-amber-100 text-amber-700" : "bg-emerald-100 text-emerald-700"}`}
        >
          {statusLabel}
        </span>
      </div>

      <p className="text-xs text-slate-600 mt-1">
        Mã staging: <span className="font-mono text-slate-800 break-all">{result.staging_id}</span>
      </p>

      {isError && result.stats?.error && (
        <p className="text-xs text-amber-800 mt-1">Chi tiết: {result.stats.error}</p>
      )}

      {!isError && statEntries.length > 0 && (
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
          {statEntries.map(({ label, value }) => (
            <div key={label} className="bg-white/70 border border-emerald-100 rounded-lg px-2.5 py-1.5">
              <dt className="text-[10px] text-slate-500">{label}</dt>
              <dd className="text-sm font-semibold text-slate-800">{value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}
