/**
 * Client-side equivalent of api/utils/text.py normalize_ward().
 *
 * Strips Vietnamese diacritics to ASCII lowercase so that
 * "Đống Đa", "dong da", "DONG DA" all produce "dong da".
 *
 * Must stay in sync with the Python implementation.
 */
export function normalizeVi(s: string): string {
  // đ/Đ (U+0111/U+0110) do not decompose via NFD — handle before normalization
  s = s.replace(/Đ/g, "D").replace(/đ/g, "d")
  // Strip combining diacritics
  s = s.normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  // Lowercase, strip non-alphanumeric, collapse whitespace
  return s
    .toLowerCase()
    .replace(/[^a-z0-9 ]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}
