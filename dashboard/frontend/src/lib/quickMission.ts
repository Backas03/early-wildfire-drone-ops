// 실시간 관제 지도에서 바로 만드는 간단한 미션: 찍은 점들(웨이포인트) 또는 영역 순찰.
// 이륙 → 웨이포인트들 → (귀환). 이미 떠 있으면 FC가 이륙 항목을 바로 넘긴다.

import { distance, type LatLon } from './geo.ts'
import { cameraSwath, longestEdgeBearing, patrolGrid } from './grid.ts'
import type { MissionItem, MissionPlan } from '../protocol.ts'

export type QuickEnd = 'rtl' | 'hold'

export const QUICK_END_LABEL: Record<QuickEnd, string> = {
  rtl: '끝나면 귀환 (RTL)',
  hold: '마지막 지점에서 대기',
}

const item = (seq: number, fields: Partial<MissionItem> & Pick<MissionItem, 'command'>): MissionItem => ({
  seq,
  lat: null,
  lon: null,
  alt: null,
  frame: 'relative',
  hold_s: 0,
  speed: null,
  ...fields,
})

export function quickPlan(name: string, points: LatLon[], alt: number, end: QuickEnd): MissionPlan {
  const items: MissionItem[] = [item(0, { command: 'takeoff', alt })]
  for (const p of points) items.push(item(items.length, { command: 'waypoint', lat: p.lat, lon: p.lon, alt }))
  if (end === 'rtl') items.push(item(items.length, { command: 'rtl' }))
  return { mission_id: null, name, home: null, items }
}

/** 영역(꼭짓점)을 지그재그로 훑는 점들. 줄 방향은 가장 긴 변, 시작은 드론에서 가까운 모서리 */
export function areaPoints(poly: LatLon[], spacing: number, start: LatLon | null): LatLon[] {
  if (poly.length < 3) return []
  return patrolGrid(poly, { spacing, angleDeg: longestEdgeBearing(poly), start }).points
}

/** 카메라 폭의 80% (옆 줄과 20% 겹치게), 5 m 단위 */
export function defaultSpacing(alt: number): number {
  return Math.max(5, Math.round((cameraSwath(alt) * 0.8) / 5) * 5)
}

/** 드론 위치 → 점들을 차례로 → (귀환이면 홈까지) 거리 m */
export function routeLength(from: LatLon | null, points: LatLon[], back: LatLon | null): number {
  const path = [...(from ? [from] : []), ...points, ...(back && points.length ? [back] : [])]
  let sum = 0
  for (let i = 1; i < path.length; i++) sum += distance(path[i - 1], path[i])
  return sum
}

export function timeLabel(d = new Date()): string {
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
