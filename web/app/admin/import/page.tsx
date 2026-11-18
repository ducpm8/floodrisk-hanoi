"use client"

import { useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  ImportApiError,
  MAX_FILE_BYTES,
  MAX_ROWS,
  downloadTemplate,
  errorsCsv,
  importCsv,
  type ImportResponse,
  type ImportRowResult,
} from "@/lib/admin-import"

type SubmitState =
  | { kind: "idle" }
  | { kind: "loading"; label: string }
  | { kind: "success"; result: ImportResponse }
  | { kind: "error"; message: string }

const FIELD_HELP: ReadonlyArray<readonly [string, string, string]> = [
  ["location_name", "Bắt buộc", "Tên địa điểm tiếng Việt, tối đa 255 ký tự"],
  ["event_date", "Bắt buộc", "YYYY-MM-DD, không ở tương lai"],
  ["severity", "Bắt buộc", "minor / moderate / severe"],
  ["lat", "Bắt buộc", "Số thực, trong khoảng 20.5 – 21.5 (Hà Nội)"],
  ["lng", "Bắt buộc", "Số thực, trong khoảng 105.2 – 106.1 (Hà Nội)"],
  ["ward", "Tùy chọn", "Tự chuẩn hóa trước khi lưu"],
  ["district", "Tùy chọn", "Quận/huyện"],
  ["depth_cm", "Tùy chọn", "Số nguyên ≥ 0"],
  ["duration_hours", "Tùy chọn", "Số nguyên ≥ 0"],
  ["source", "Tùy chọn", "official / news / community (mặc định community)"],
  ["source_url", "Tùy chọn", "URL http(s). Để trống sẽ lưu manual://admin-csv"],
  ["cause", "Tùy chọn", "Nguyên nhân, ví dụ: mưa lớn"],
]

const STATUS_LABEL: Record<string, string> = {
  inserted: "Đã nhập",
  duplicate: "Trùng",
  error: "Lỗi",
}

const STATUS_CLASS: Record<string, string> = {
  inserted: "bg-green-100 text-green-700",
  duplicate: "bg-amber-100 text-amber-700",
  error: "bg-red-100 text-red-700",
}

function describeError(err: unknown): string {
  if (err instanceof ImportApiError) {
    switch (err.status) {
      case 403:
        return `Không có quyền truy cập (403). Kiểm tra NEXT_PUBLIC_ADMIN_TOKEN.${err.message ? ` ${err.message}` : ""}`
      case 422:
        return `File không hợp lệ (422). ${err.message}`
      default:
        return `Lỗi ${err.status}. ${err.message}`
    }
  }
  if (err instanceof TypeError) {
    return "Không thể kết nối tới API. Kiểm tra máy chủ đang chạy tại NEXT_PUBLIC_API_URL."
  }
  return err instanceof Error ? err.message : "Lỗi không xác định."
}

function rowErrors(result: ImportResponse, row: ImportRowResult): string {
  return result.errors
    .filter((err) => err.row === row.row)
    .map((err) => `${err.field}: ${err.message}`)
    .join("; ")
}

export default function AdminImportPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [state, setState] = useState<SubmitState>({ kind: "idle" })

  function selectFile(next: File | null) {
    if (next && next.size > MAX_FILE_BYTES) {
      setFile(null)
      setState({
        kind: "error",
        message: `File vượt quá ${Math.round(MAX_FILE_BYTES / 1000)} KB.`,
      })
      return
    }
    setFile(next)
    setState({ kind: "idle" })
  }

  async function handleTemplate() {
    setState({ kind: "loading", label: "Đang tải mẫu CSV…" })
    try {
      await downloadTemplate()
      setState({ kind: "idle" })
    } catch (err) {
      setState({ kind: "error", message: describeError(err) })
    }
  }

  async function run(dryRun: boolean) {
    if (!file) {
      setState({ kind: "error", message: "Chọn file CSV trước." })
      return
    }
    setState({
      kind: "loading",
      label: dryRun ? "Đang kiểm tra file…" : "Đang nhập dữ liệu…",
    })
    try {
      const result = await importCsv(file, { dryRun })
      setState({ kind: "success", result })
    } catch (err) {
      setState({ kind: "error", message: describeError(err) })
    }
  }

  function handleErrorsDownload(result: ImportResponse) {
    const blob = errorsCsv(result)
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = "flood_locations_errors.csv"
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }

  const result = state.kind === "success" ? state.result : null
  const validCount = result ? result.inserted + result.duplicates : 0

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 space-y-6">
      <header className="space-y-1">
        <a href="/admin" className="text-xs text-blue-600 underline">
          ← Nhập văn bản thủ công
        </a>
        <h1 className="text-2xl font-semibold text-slate-800">
          Nhập địa điểm ngập bằng CSV
        </h1>
        <p className="text-sm text-slate-500">
          Nhập trực tiếp các địa điểm ngập có tọa độ. Không dùng trích xuất AI,
          không qua staging.
        </p>
      </header>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" onClick={handleTemplate}>
            Tải mẫu CSV
          </Button>
          <span className="text-xs text-slate-400">
            Tối đa {MAX_ROWS.toLocaleString("vi-VN")} dòng ·{" "}
            {Math.round(MAX_FILE_BYTES / 1000)} KB · .csv
          </span>
        </div>

        <div className="space-y-2">
          <label className="block text-sm font-medium text-slate-700">
            File CSV
          </label>
          <input
            ref={inputRef}
            type="file"
            accept=".csv,text/csv"
            className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
            onChange={(e) => selectFile(e.target.files?.[0] ?? null)}
          />
        </div>

        <div className="flex flex-wrap gap-3">
          <Button
            variant="outline"
            onClick={() => run(true)}
            disabled={!file || state.kind === "loading"}
          >
            Kiểm tra file
          </Button>
          <Button
            onClick={() => run(false)}
            disabled={
              !file ||
              state.kind === "loading" ||
              (result !== null && result.dry_run === false && validCount === 0)
            }
          >
            Nhập dữ liệu
          </Button>
        </div>

        {state.kind === "loading" && (
          <p className="text-sm text-slate-500">{state.label}</p>
        )}
        {state.kind === "error" && (
          <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">
            {state.message}
          </p>
        )}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-semibold text-slate-700">Định dạng cột</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-4 font-medium">Cột</th>
                <th className="py-1 pr-4 font-medium">Bắt buộc</th>
                <th className="py-1 font-medium">Quy tắc</th>
              </tr>
            </thead>
            <tbody className="text-slate-600">
              {FIELD_HELP.map(([field, required, rule]) => (
                <tr key={field} className="border-t border-slate-100">
                  <td className="py-1.5 pr-4 font-mono">{field}</td>
                  <td className="py-1.5 pr-4">{required}</td>
                  <td className="py-1.5">{rule}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {result && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-700">
              {result.dry_run ? "Kết quả kiểm tra" : "Kết quả nhập"}
            </h2>
            {result.skipped > 0 && (
              <Button variant="outline" onClick={() => handleErrorsDownload(result)}>
                Tải các dòng lỗi
              </Button>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Stat label={result.dry_run ? "Hợp lệ" : "Đã nhập"} value={result.inserted} />
            <Stat label="Trùng" value={result.duplicates} />
            <Stat label="Lỗi" value={result.skipped} />
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-slate-500">
                <tr>
                  <th className="py-1 pr-3 font-medium">Dòng</th>
                  <th className="py-1 pr-3 font-medium">Địa điểm</th>
                  <th className="py-1 pr-3 font-medium">Ngày</th>
                  <th className="py-1 pr-3 font-medium">Mức độ</th>
                  <th className="py-1 pr-3 font-medium">Phường / Quận</th>
                  <th className="py-1 font-medium">Trạng thái</th>
                </tr>
              </thead>
              <tbody className="text-slate-600">
                {result.rows.map((row) => (
                  <tr key={`${row.row}-${row.location_name}`} className="border-t border-slate-100">
                    <td className="py-1.5 pr-3">{row.row}</td>
                    <td className="py-1.5 pr-3">{row.location_name || "—"}</td>
                    <td className="py-1.5 pr-3">{row.event_date || "—"}</td>
                    <td className="py-1.5 pr-3">{row.severity || "—"}</td>
                    <td className="py-1.5 pr-3">
                      {[row.ward, row.district].filter(Boolean).join(", ") || "—"}
                    </td>
                    <td className="py-1.5">
                      <span
                        className={`rounded-full px-2 py-0.5 font-medium ${
                          STATUS_CLASS[row.status] ?? "bg-slate-100 text-slate-600"
                        }`}
                        title={row.status === "error" ? rowErrors(result, row) : undefined}
                      >
                        {STATUS_LABEL[row.status] ?? row.status}
                      </span>
                      {row.status === "error" && (
                        <span className="ml-2 text-red-600">{rowErrors(result, row)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {result.dry_run && result.inserted > 0 && (
            <p className="text-xs text-slate-500">
              Chưa ghi gì vào cơ sở dữ liệu. Bấm “Nhập dữ liệu” để lưu{" "}
              {result.inserted} dòng hợp lệ.
            </p>
          )}
        </section>
      )}
    </main>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-slate-50 px-3 py-2">
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className="text-lg font-semibold text-slate-800">{value}</p>
    </div>
  )
}
