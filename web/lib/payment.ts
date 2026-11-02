/**
 * Payment API calls for FIR-14.
 * All amounts in VND. order_id is a UUID.
 */

import { getOrCreateSessionId } from "./session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface InitiatePaymentResult {
  order_id: string;
  payment_url: string;
  amount_vnd: number;
}

export async function initiatePayment(
  lat: number,
  lng: number
): Promise<InitiatePaymentResult> {
  const sessionId = getOrCreateSessionId();
  const idempotencyKey = crypto.randomUUID();

  const res = await fetch(`${API}/api/payment/initiate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      lat,
      lng,
      idempotency_key: idempotencyKey,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err?.detail ?? `Payment initiation failed: ${res.status}`);
  }

  return res.json();
}

export interface PaymentStatus {
  order_id: string;
  status: "pending" | "paid" | "failed";
  paid_at: string | null;
}

export async function checkPaymentStatus(orderId: string): Promise<PaymentStatus> {
  const sessionId = getOrCreateSessionId();
  const res = await fetch(
    `${API}/api/payment/status?order_id=${encodeURIComponent(orderId)}&session_id=${encodeURIComponent(sessionId)}`
  );

  if (!res.ok) {
    throw new Error(`Status check failed: ${res.status}`);
  }

  return res.json();
}

export async function devConfirmPayment(orderId: string): Promise<PaymentStatus> {
  const sessionId = getOrCreateSessionId();
  const res = await fetch(
    `${API}/api/payment/dev-confirm?order_id=${encodeURIComponent(orderId)}&session_id=${encodeURIComponent(sessionId)}`,
    { method: "POST" }
  );

  if (!res.ok) {
    throw new Error(`Dev confirm failed: ${res.status}`);
  }

  return res.json();
}

export interface SessionReportCheck {
  has_paid_report: boolean;
  order_id: string | null;
}

export async function checkSessionReport(
  lat: number,
  lng: number
): Promise<SessionReportCheck> {
  const sessionId = getOrCreateSessionId();
  const res = await fetch(
    `${API}/api/payment/check?session_id=${encodeURIComponent(sessionId)}&lat=${lat}&lng=${lng}`
  );

  if (!res.ok) {
    return { has_paid_report: false, order_id: null };
  }

  return res.json();
}
