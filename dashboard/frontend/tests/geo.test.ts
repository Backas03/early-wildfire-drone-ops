// 실행: npm test
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { parseLatLon } from '../src/lib/geo.ts'

test('parseLatLon: 위도, 경도 (쉼표·공백)', () => {
  assert.deepEqual(parseLatLon('35.8964, 128.8495'), { lat: 35.8964, lon: 128.8495 })
  assert.deepEqual(parseLatLon(' 35.8964 128.8495 '), { lat: 35.8964, lon: 128.8495 })
  assert.deepEqual(parseLatLon('35.8964 , 128.8495'), { lat: 35.8964, lon: 128.8495 })
  assert.deepEqual(parseLatLon('-33.86,151.2'), { lat: -33.86, lon: 151.2 })
})

test('parseLatLon: 경도, 위도 순서도 받는다', () => {
  assert.deepEqual(parseLatLon('128.8495, 35.8964'), { lat: 35.8964, lon: 128.8495 })
})

test('parseLatLon: 좌표가 아니면 null', () => {
  assert.equal(parseLatLon('대구대학교'), null)
  assert.equal(parseLatLon('경산시 진량읍 1'), null)
  assert.equal(parseLatLon('35.8964'), null)
  assert.equal(parseLatLon('95, 200'), null) // 범위 밖
  assert.equal(parseLatLon('35.1, 128.2, 3'), null)
})
