// 드론 카메라 관련 계산 (컴포넌트가 아닌 것)
import { makeProjection, type LatLon } from '../lib/geo.ts'
import type { Detection, Telemetry } from '../protocol.ts'

export const CLS_LABEL: Record<Detection['cls'], string> = { fire: '불', smoke: '연기' }

/** 카메라가 내려다보는 땅 범위 (카메라 모듈 3 화각, 바로 아래를 본다고 가정) */
export function cameraFootprint(t: Telemetry, hfov = 66, vfov = 41): LatLon[] {
  const alt = Math.max(t.position.alt_rel, 1)
  const w = alt * Math.tan(((hfov / 2) * Math.PI) / 180)
  const h = alt * Math.tan(((vfov / 2) * Math.PI) / 180)
  const a = (t.velocity.heading * Math.PI) / 180
  const proj = makeProjection({ lat: t.position.lat, lon: t.position.lon })
  // 화면 기준 (오른쪽, 앞쪽) m → 동·북 m
  return [
    [-w, h],
    [w, h],
    [w, -h],
    [-w, -h],
  ].map(([r, f]) => proj.toLatLon({ x: r * Math.cos(a) + f * Math.sin(a), y: -r * Math.sin(a) + f * Math.cos(a) }))
}
