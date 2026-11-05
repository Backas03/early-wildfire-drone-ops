// 순찰 영역(다각형) → 지그재그 순찰 경로 (잔디깎기 패턴)
//
// 1. 다각형을 평면 좌표로 바꾸고, 줄 방향이 가로가 되도록 돌린다.
// 2. 일정 간격의 가로줄과 다각형 변의 교점을 구해 줄마다 구간을 만든다.
// 3. 줄마다 방향을 바꿔 가며 이어 붙인다. 시작점은 홈(또는 직전 지점)에서 가장 가까운 모서리.
//
// 오목한 다각형이면 한 줄이 여러 구간으로 나뉘고, 구간 사이 이동이 영역 밖을 지날 수 있다.

import { centroid, makeProjection, type LatLon } from './geo.ts'

export interface GridOptions {
  /** 줄 간격 m */
  spacing: number
  /** 줄 방향 방위각 (0 = 남북 방향 줄, 90 = 동서 방향 줄) */
  angleDeg: number
  /** 이 지점에서 가장 가까운 모서리부터 시작 */
  start?: LatLon | null
}

export interface GridResult {
  points: LatLon[]
  lines: number
  /** 경로 길이 m (시작점까지 이동은 제외) */
  length: number
}

interface UV {
  u: number // 줄 방향 좌표
  v: number // 줄과 수직 좌표
}

const EMPTY: GridResult = { points: [], lines: 0, length: 0 }

function rotation(angleDeg: number) {
  const th = (angleDeg * Math.PI) / 180
  const s = Math.sin(th)
  const c = Math.cos(th)
  // [u, v] = M [x, y], M = [[s, c], [c, -s]] 은 자기 자신이 역행렬이다
  return {
    toUV: (x: number, y: number): UV => ({ u: x * s + y * c, v: x * c - y * s }),
    toXY: (p: UV) => ({ x: p.u * s + p.v * c, y: p.u * c - p.v * s }),
  }
}

/** 가장 긴 변의 방위각 (0~179). 이 방향으로 줄을 그으면 방향 전환이 줄어든다. */
export function longestEdgeBearing(poly: LatLon[]): number {
  if (poly.length < 2) return 0
  const proj = makeProjection(centroid(poly))
  let best = 0
  let bestLen = -1
  for (let i = 0; i < poly.length; i++) {
    const a = proj.toLocal(poly[i])
    const b = proj.toLocal(poly[(i + 1) % poly.length])
    const len = Math.hypot(b.x - a.x, b.y - a.y)
    if (len > bestLen) {
      bestLen = len
      best = (Math.atan2(b.x - a.x, b.y - a.y) * 180) / Math.PI
    }
  }
  return Math.round(((best % 180) + 180) % 180)
}

export function patrolGrid(poly: LatLon[], opt: GridOptions): GridResult {
  if (poly.length < 3 || !(opt.spacing > 0)) return EMPTY
  const proj = makeProjection(centroid(poly))
  const rot = rotation(opt.angleDeg)
  const pts = poly.map((p) => {
    const l = proj.toLocal(p)
    return rot.toUV(l.x, l.y)
  })

  const vmin = Math.min(...pts.map((p) => p.v))
  const vmax = Math.max(...pts.map((p) => p.v))
  // 줄 수를 정하고 남는 폭을 양쪽에 나눠 줄을 가운데로 모은다
  // (1e-6: 폭이 간격의 정확한 배수일 때 부동소수점 오차로 한 줄 더 생기는 것 방지)
  const n = Math.max(1, Math.ceil((vmax - vmin) / opt.spacing - 1e-6))
  if (n > 2000) return EMPTY // 간격이 너무 좁음
  const offset = (vmax - vmin - (n - 1) * opt.spacing) / 2

  const rows: [number, number][][] = [] // 줄마다 [u 시작, u 끝] 구간들
  const rowV: number[] = []
  for (let k = 0; k < n; k++) {
    const v = vmin + offset + k * opt.spacing
    const xs: number[] = []
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i]
      const b = pts[(i + 1) % pts.length]
      // 반열린 구간 규칙: 꼭짓점을 두 번 세지 않는다
      if ((a.v <= v && b.v > v) || (b.v <= v && a.v > v)) {
        xs.push(a.u + ((v - a.v) / (b.v - a.v)) * (b.u - a.u))
      }
    }
    xs.sort((p, q) => p - q)
    const segs: [number, number][] = []
    for (let i = 0; i + 1 < xs.length; i += 2) {
      if (xs[i + 1] - xs[i] > 0.01) segs.push([xs[i], xs[i + 1]])
    }
    if (segs.length) {
      rows.push(segs)
      rowV.push(v)
    }
  }
  if (!rows.length) return EMPTY

  // 꼭짓점 근처에서 생기는 아주 짧은 구간은 점 하나로 (거의 같은 자리에 웨이포인트 두 개가 생기지 않게)
  const minSeg = Math.min(5, opt.spacing / 4)
  const build = (reverseRows: boolean, flipFirst: boolean): UV[] => {
    const order = rows.map((_, i) => i)
    if (reverseRows) order.reverse()
    const out: UV[] = []
    let flip = flipFirst
    for (const r of order) {
      const v = rowV[r]
      const segs = flip ? [...rows[r]].reverse().map(([a, b]) => [b, a] as [number, number]) : rows[r]
      for (const [u0, u1] of segs) {
        if (Math.abs(u1 - u0) < minSeg) out.push({ u: (u0 + u1) / 2, v })
        else out.push({ u: u0, v }, { u: u1, v })
      }
      flip = !flip
    }
    return out
  }

  let route = build(false, false)
  if (opt.start) {
    const l = proj.toLocal(opt.start)
    const s = rot.toUV(l.x, l.y)
    let bestD = Infinity
    for (const [rr, ff] of [
      [false, false],
      [false, true],
      [true, false],
      [true, true],
    ] as const) {
      const cand = build(rr, ff)
      const d = Math.hypot(cand[0].u - s.u, cand[0].v - s.v)
      if (d < bestD) {
        bestD = d
        route = cand
      }
    }
  }

  let length = 0
  for (let i = 1; i < route.length; i++) {
    length += Math.hypot(route[i].u - route[i - 1].u, route[i].v - route[i - 1].v)
  }
  return {
    points: route.map((p) => proj.toLatLon(rot.toXY(p))),
    lines: rows.length,
    length,
  }
}

/** Camera Module 3 (표준 렌즈, 가로 화각 66°)가 수직 아래를 볼 때 지면 촬영 폭 m */
export function cameraSwath(altM: number, hfovDeg = 66): number {
  return 2 * altM * Math.tan(((hfovDeg / 2) * Math.PI) / 180)
}
