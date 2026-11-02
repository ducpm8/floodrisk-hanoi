"use client";

/**
 * FIR-14: Payment return URL handler.
 *
 * MoMo/VNPay redirect here after payment. URL params:
 *   order_id  — UUID of the PurchasedReport
 *   demo=1    — present in demo mode (PAYMENT_DEMO_MODE=true)
 *
 * Flow:
 *   1. Read order_id from URL params
 *   2. Poll GET /api/payment/status until status != 'pending' (max 10s)
 *   3. If paid → redirect to / with success toast signal
 *   4. If failed / timeout → show error with retry option
 */

import { useEffect, useState } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { checkPaymentStatus, devConfirmPayment } from "@/lib/payment";
import { Button } from "@/components/ui/button";

type PageState = "checking" | "success" | "failed" | "timeout" | "no_order";

const POLL_INTERVAL_MS = 1000;
const POLL_MAX_ATTEMPTS = 10;

export default function PaymentReturnPage() {
  const params = useSearchParams();
  const router = useRouter();
  const orderId = params.get("order_id");
  const isDemo = params.get("demo") === "1";

  const [state, setState] = useState<PageState>("checking");

  useEffect(() => {
    if (!orderId) {
      setState("no_order");
      return;
    }

    // Demo mode: explicitly confirm via dev endpoint, no polling needed
    if (isDemo) {
      devConfirmPayment(orderId)
        .then(() => {
          setState("success");
          setTimeout(() => router.push("/?payment=success"), 1500);
        })
        .catch(() => setState("failed"));
      return;
    }

    let attempts = 0;

    const poll = async () => {
      attempts++;
      try {
        const status = await checkPaymentStatus(orderId);
        if (status.status === "paid") {
          setState("success");
          setTimeout(() => router.push("/?payment=success"), 1500);
          return;
        }
        if (status.status === "failed") {
          setState("failed");
          return;
        }
        if (attempts >= POLL_MAX_ATTEMPTS) {
          setState("timeout");
          return;
        }
        setTimeout(poll, POLL_INTERVAL_MS);
      } catch {
        setState("failed");
      }
    };

    poll();
  }, [orderId, isDemo, router]);

  if (state === "checking") {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 text-center px-4">
        <div className="h-10 w-10 rounded-full border-4 border-blue-500 border-t-transparent animate-spin" />
        <p className="text-lg font-medium">Đang xác nhận thanh toán...</p>
        <p className="text-sm text-muted-foreground">Vui lòng không đóng trang này.</p>
      </div>
    );
  }

  if (state === "success") {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 text-center px-4">
        <div className="h-16 w-16 rounded-full bg-green-100 flex items-center justify-center">
          <svg
            className="h-8 w-8 text-green-600"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
        </div>
        <p className="text-xl font-semibold">Thanh toán thành công!</p>
        <p className="text-sm text-muted-foreground">Đang chuyển về trang báo cáo...</p>
      </div>
    );
  }

  if (state === "no_order") {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 text-center px-4">
        <p className="text-lg font-medium text-red-600">Không tìm thấy mã đơn hàng.</p>
        <Button variant="outline" onClick={() => router.push("/")}>
          Về trang chủ
        </Button>
      </div>
    );
  }

  // failed or timeout
  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-4 text-center px-4">
      <div className="h-16 w-16 rounded-full bg-red-100 flex items-center justify-center">
        <svg
          className="h-8 w-8 text-red-600"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
        </svg>
      </div>
      <p className="text-xl font-semibold">
        {state === "timeout" ? "Hết thời gian xác nhận" : "Thanh toán thất bại"}
      </p>
      <p className="text-sm text-muted-foreground max-w-xs">
        {state === "timeout"
          ? "Không nhận được xác nhận từ cổng thanh toán. Vui lòng kiểm tra lại sau vài phút."
          : "Giao dịch không thành công. Bạn chưa bị trừ tiền."}
      </p>
      <Button onClick={() => router.push("/")}>Thử lại</Button>
    </div>
  );
}
