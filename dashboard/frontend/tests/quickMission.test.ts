// 실행: npm test
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { areaPoints, defaultSpacing, quickPlan, routeLength } from '../src/lib/quickMission.ts'

const A = { lat: 35.8964, lon: 128.8495 }
const B = { lat: 35.8973, lon: 128.8495 } // A에서 북쪽 약 100 m

test('quickPlan: 이륙 → 웨이포인트 → 귀환, seq는 0부터', () => {
  const plan = quickPlan('시험', [A, B], 30, 'rtl')
  assert.deepEqual(
    plan.items.map((it) => [it.seq, it.command, it.alt]),
    [
      [0, 'takeoff', 30],
      [1, 'waypoint', 30],
      [2, 'waypoint', 30],
      [3, 'rtl', null],
    ],
  )
  assert.equal(plan.items[2].lat, B.lat)
  assert.equal(quickPlan('시험', [A], 30, 'hold').items.at(-1)?.command, 'waypoint')
})

test('areaPoints: 꼭짓점 3개 미만이면 빈 경로, 사각형은 지그재그', () => {
  assert.deepEqual(areaPoints([A, B], 20, null), [])
  const E = { lat: A.lat, lon: 128.8506 } // 동쪽 약 100 m
  const F = { lat: B.lat, lon: E.lon }
  const pts = areaPoints([A, B, F, E], 25, A)
  assert.ok(pts.length >= 6 && pts.length % 2 === 0, `점 ${pts.length}개`)
})

test('defaultSpacing: 카메라 폭의 80%를 5 m 단위로, 최소 5 m', () => {
  assert.equal(defaultSpacing(30), 30) // 폭 39 m × 0.8 = 31 → 30
  assert.equal(defaultSpacing(2), 5)
})

test('routeLength: 드론 → 점들 → 홈', () => {
  const one = routeLength(A, [B], null)
  assert.ok(Math.abs(one - 100) < 2, `${one}`)
  assert.ok(Math.abs(routeLength(A, [B], A) - 2 * one) < 0.01)
  assert.equal(routeLength(A, [], A), 0)
})
