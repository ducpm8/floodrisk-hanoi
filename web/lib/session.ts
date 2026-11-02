/**
 * Guest session management.
 * Stores a UUID in localStorage — this is the user's identity for payment gating.
 * Sent as X-Session-ID header on all API calls that require it.
 */

const SESSION_KEY = "flood_session_id";

export function getOrCreateSessionId(): string {
  if (typeof window === "undefined") return "";
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

export function getSessionHeaders(): Record<string, string> {
  const id = getOrCreateSessionId();
  return id ? { "X-Session-ID": id } : {};
}
