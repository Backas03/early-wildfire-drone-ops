import assert from 'node:assert/strict'
import { test } from 'node:test'

import { makeProjection } from '../src/lib/geo.ts'
import { checkMission, DEFAULT_ASSUMPTIONS, missionStats, type PlanItem } from '../src/lib/missionCheck.ts'

const HOME = { lat: 35.8964, lon: 128.8495, alt_msl: 100 }
const proj = makeProjection(HOME)
const wp = (x: number, y: number, alt = 30, extra: Partial<PlanItem> = {}): PlanItem => ({
  command: 'waypoint',
  ...proj.toLatLon({ x, y }),
  alt,
  frame: 'relative',
  hold_s: 0,
  speed: null,
  ...extra,
})
const cmd = (command: PlanItem['command'], extra: Partial<PlanItem> = {}): PlanItem => ({
  command,
  lat: null,
  lon: null,
  alt: null,
  frame: 'relative',
  hold_s: 0,
  speed: null,
  ...extra,
})

const A = DEFAULT_ASSUMPTIONS // 5 m/s, 상승 2.5, 하강 1.5

test('왕복 거리와 시간', () => {
  const items = [cmd('takeoff', { alt: 30 }), wp(500, 0), cmd('rtl')]
  const st = missionStats(HOME, items, A)
  assert.ok(Math.abs(st.distance - 1000) < 2)
  // 이륙 30/2.5=12 + 500/5=100 + 귀환 500/5=100 + 착륙 (20/1.5 + 10/0.5)=33.3
  assert.ok(Math.abs(st.seconds - 245.3) < 1, `seconds=${st.seconds}`)
  assert.equal(st.endIndex, 2)
  assert.ok(Math.abs((st.maxFromHome ?? 0) - 500) < 1)
  assert.deepEqual(checkMission('왕복', HOME, items, st, A), [])
})

test('속도 변경이 시간에 반영됨', () => {
  const items = [cmd('takeoff', { alt: 30 }), cmd('change_speed', { speed: 10 }), wp(1000, 0)]
  const st = missionStats(HOME, items, A)
  assert.ok(Math.abs(st.seconds - (12 + 100)) < 1)
})

test('오류: 고도 초과, 위치 없음, 이름 없음', () => {
  const items = [cmd('takeoff', { alt: 30 }), wp(10, 0, 200), cmd('waypoint', { alt: 30 }), cmd('rtl')]
  const issues = checkMission(' ', HOME, items, missionStats(HOME, items, A), A)
  const errors = issues.filter((i) => i.level === 'error').map((i) => i.text)
  assert.equal(errors.length, 3)
  assert.ok(errors.some((t) => t.includes('이름')))
  assert.ok(errors.some((t) => t.startsWith('2번') && t.includes('150')))
  assert.ok(errors.some((t) => t.startsWith('3번') && t.includes('위치')))
})

test('경고: 이륙 없음, 귀환 없음, 배터리 부족', () => {
  const items = [wp(3000, 0), wp(0, 3000)]
  const issues = checkMission('먼 곳', HOME, items, missionStats(HOME, items, A), A)
  const warns = issues.filter((i) => i.level === 'warn').map((i) => i.text)
  assert.ok(warns.some((t) => t.includes('이륙')))
  assert.ok(warns.some((t) => t.includes('RTL')))
  assert.ok(warns.some((t) => t.includes('배터리')))
})

test('RTL 뒤 항목은 실행 안 됨 경고', () => {
  const items = [cmd('takeoff', { alt: 30 }), cmd('rtl'), wp(100, 0)]
  const st = missionStats(HOME, items, A)
  assert.equal(st.endIndex, 1)
  const issues = checkMission('x', HOME, items, st, A)
  assert.ok(issues.some((i) => i.text.includes('실행되지 않아요')))
})
