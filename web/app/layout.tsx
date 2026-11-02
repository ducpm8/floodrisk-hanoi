import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "Kiểm tra ngập lụt Hà Nội",
  description: "Tra cứu rủi ro ngập lụt và lịch sử giá nhà đất theo phường tại Hà Nội",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="vi">
      <body>{children}</body>
    </html>
  )
}
