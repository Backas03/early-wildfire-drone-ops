// 지도 계산 도우미. 미션 범위(수 km)에서는 평면 근사로 충분하다.

export interface LatLon {
  lat: number
  lon: number
}

const R = 6_371_000 // 지구 반지름 m
const RAD = Math.PI / 180

/** 두 지점 사이 거리 (m, 하버사인) */
export function distance(a: LatLon, b: LatLon): number {
  const dLat = (b.lat - a.lat) * RAD
  const dLon = (b.lon - a.lon) * RAD
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * RAD) * Math.cos(b.lat * RAD) * Math.sin(dLon / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)))
}

export function midpoint(a: LatLon, b: LatLon): LatLon {
  return { lat: (a.lat + b.lat) / 2, lon: (a.lon + b.lon) / 2 }
}

export function centroid(points: LatLon[]): LatLon {
  const n = points.length
  return {
    lat: points.reduce((s, p) => s + p.lat, 0) / n,
    lon: points.reduce((s, p) => s + p.lon, 0) / n,
  }
}

/** 기준점 주변 평면 좌표 (x: 동쪽 m, y: 북쪽 m) */
export interface Local {
  x: number
  y: number
}

export function makeProjection(origin: LatLon) {
  const kx = R * RAD * Math.cos(origin.lat * RAD)
  const ky = R * RAD
  return {
    toLocal: (p: LatLon): Local => ({ x: (p.lon - origin.lon) * kx, y: (p.lat - origin.lat) * ky }),
    toLatLon: (l: Local): LatLon => ({ lat: origin.lat + l.y / ky, lon: origin.lon + l.x / kx }),
  }
}

export function formatDistance(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`
}

export function formatDuration(s: number): string {
  const total = Math.round(s)
  const min = Math.floor(total / 60)
  const sec = total % 60
  return min > 0 ? `${min}분 ${sec.toString().padStart(2, '0')}초` : `${sec}초`
}
