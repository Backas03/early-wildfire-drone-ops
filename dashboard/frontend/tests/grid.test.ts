// 실행: npm test  (Node 24가 TypeScript를 직접 실행)
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { distance, makeProjection, type LatLon } from '../src/lib/geo.ts'
import { longestEdgeBearing, patrolGrid } from '../src/lib/grid.ts'

const ORIGIN = { lat: 35.8964, lon: 128.8495 }
const proj = makeProjection(ORIGIN)
const at = (x: number, y: number) => proj.toLatLon({ x, y })

// 동서 200 m × 남북 100 m 직사각형
const RECT: LatLon[] = [at(0, 0), at(200, 0), at(200, 100), at(0, 100)]

test('distance: 동쪽 100 m', () => {
  assert.ok(Math.abs(distance(at(0, 0), at(100, 0)) - 100) < 0.5)
})

test('직사각형: 동서 방향 줄', () => {
  const g = patrolGrid(RECT, { spacing: 25, angleDeg: 90 })
  assert.equal(g.lines, 4) // 높이 100 / 간격 25
  assert.equal(g.points.length, 8)
  const local = g.points.map((p) => proj.toLocal(p))
  // 줄은 y = 12.5, 37.5, 62.5, 87.5 (가운데 정렬)
  const ys = [...new Set(local.map((l) => Math.round(l.y * 10) / 10))].sort((a, b) => a - b)
  assert.deepEqual(ys, [12.5, 37.5, 62.5, 87.5])
  // 줄 끝은 직사각형 가장자리
  for (const l of local) assert.ok(Math.abs(l.x) < 0.5 || Math.abs(l.x - 200) < 0.5)
  // 지그재그: 4줄 × 200 m + 줄 사이 3 × 25 m
  assert.ok(Math.abs(g.length - (4 * 200 + 3 * 25)) < 2)
})

test('시작점에서 가장 가까운 모서리부터 시작', () => {
  const start = at(210, 110) // 북동쪽 모서리 바깥
  const g = patrolGrid(RECT, { spacing: 25, angleDeg: 90, start })
  const first = proj.toLocal(g.points[0])
  assert.ok(Math.abs(first.x - 200) < 0.5 && Math.abs(first.y - 87.5) < 0.5)
})

test('오목한 다각형(ㄷ자)은 한 줄이 두 구간으로 나뉨', () => {
  const U: LatLon[] = [at(0, 0), at(300, 0), at(300, 100), at(200, 100), at(200, 50), at(100, 50), at(100, 100), at(0, 100)]
  const g = patrolGrid(U, { spacing: 20, angleDeg: 90 })
  const local = g.points.map((p) => proj.toLocal(p))
  // 위쪽(y > 50) 줄의 점들은 가운데 빈 곳(100 < x < 200) 안에 없어야 함
  for (const l of local) if (l.y > 50) assert.ok(!(l.x > 100.5 && l.x < 199.5))
})

test('뾰족한 꼭짓점 근처에서 겹치는 점이 생기지 않음', () => {
  const tri: LatLon[] = [at(0, 0), at(400, 0), at(200, 300)]
  for (const angleDeg of [90, 30, 0]) {
    const g = patrolGrid(tri, { spacing: 20, angleDeg })
    for (let i = 1; i < g.points.length; i++) {
      assert.ok(distance(g.points[i - 1], g.points[i]) > 1, `angle ${angleDeg}: ${i - 1}번과 ${i}번이 겹침`)
    }
  }
})

test('잘못된 입력은 빈 결과', () => {
  assert.equal(patrolGrid(RECT.slice(0, 2), { spacing: 20, angleDeg: 0 }).points.length, 0)
  assert.equal(patrolGrid(RECT, { spacing: 0, angleDeg: 0 }).points.length, 0)
})

test('가장 긴 변 방향', () => {
  assert.equal(longestEdgeBearing(RECT), 90) // 동서 200 m 변
  const tall = [at(0, 0), at(50, 0), at(50, 300), at(0, 300)]
  assert.equal(longestEdgeBearing(tall), 0)
})
