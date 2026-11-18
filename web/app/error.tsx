"use client"

import { useEffect } from "react"

/**
 * Route-level error boundary. Network failures should be handled in the data
 * helpers (see lib/api.ts / fetchSettings), so reaching this boundary means a
 * real rendering bug. It still gives users a recoverable screen instead of a
 * blank page, and logs the error for the console/telemetry.
 */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error("Unhandled UI error:", error)
  }, [error])

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-slate-900 px-4 text-center text-white">
      <p className="text-xl font-semibold">Đã xảy ra lỗi</p>
      <p className="max-w-sm text-sm text-white/60">
        Không thể tải trang này. Vui lòng thử lại — dữ liệu của bạn không bị ảnh
        hưởng.
      </p>
      <button
        type="button"
        onClick={reset}
        className="rounded-full bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-500"
      >
        Thử lại
      </button>
    </div>
  )
}
