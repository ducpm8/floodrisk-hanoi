const R = 6371000 // Earth radius in metres

function toRad(deg: number) {
  return (deg * Math.PI) / 180
}

export function haversineMetres(
  lat1: number,
  lng1: number,
  lat2: number,
  lng2: number,
): number {
  const dLat = toRad(lat2 - lat1)
  const dLng = toRad(lng2 - lng1)
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.sqrt(a))
}

export function formatDistance(metres: number): string {
  // FIR-78: events resolved to ward centroid → distance ≈ 0 from anchor.
  // Show "Cùng phường" instead of misleading "0 m".
  if (metres < 50) return "Cùng phường"
  if (metres < 1000) return `${Math.round(metres)} m`
  return `${(metres / 1000).toFixed(1)} km`
}
