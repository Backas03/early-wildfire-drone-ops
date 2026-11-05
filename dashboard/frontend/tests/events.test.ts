// 실행: npm test
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { eventSummary, isOpen, sortEvents } from '../src/lib/events.ts'
import type { FireEvent } from '../src/protocol.ts'

const ev = (id: string, status: FireEvent['status'], last_seen: number): FireEvent => ({
  event_id: id,
  drone_id: 'drone1',
  status,
  classes: ['fire', 'smoke'],
  max_conf: 0.829,
  first_seen: 0,
  last_seen,
  lat: 35.9,
  lon: 128.85,
  snapshot_url: null,
  detection_count: 5,
  check_confirmed: 2,
  check_rejected: 1,
  radius_m: 3,
})

test('sortEvents: 신규 → 확인함 → 끝난 것, 같은 상태는 최근에 본 것 먼저', () => {
  const got = sortEvents([ev('a', 'resolved', 9), ev('b', 'acknowledged', 5), ev('c', 'new', 1), ev('d', 'new', 3), ev('e', 'false_alarm', 7)])
  assert.deepEqual(
    got.map((e) => e.event_id),
    ['d', 'c', 'b', 'a', 'e'],
  )
  assert.deepEqual([ev('x', 'new', 0), ev('y', 'false_alarm', 0)].map(isOpen), [true, false])
})

test('eventSummary: 종류·최고 신뢰도(내림)·서버 확인', () => {
  assert.equal(eventSummary(ev('a', 'new', 0)), '불·연기 의심 · 최고 82% · 서버 확인 2/3')
  assert.equal(eventSummary({ ...ev('a', 'new', 0), check_confirmed: 0, check_rejected: 0 }), '불·연기 의심 · 최고 82%')
})
