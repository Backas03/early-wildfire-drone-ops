import type { PlanItem } from '../lib/missionCheck.ts'

const r = (v: number | null, digits: number) => (v == null ? null : Number(v.toFixed(digits)))

/** 드론을 거쳐 온 값(정수 좌표·float32 고도)과 비교할 수 있게 반올림해서 비교 (drone-agent의 same_items와 같은 기준) */
export function sameItems(a: PlanItem[], b: PlanItem[]): boolean {
  if (a.length !== b.length) return false
  return a.every((x, i) => {
    const y = b[i]
    return (
      x.command === y.command &&
      r(x.lat, 6) === r(y.lat, 6) &&
      r(x.lon, 6) === r(y.lon, 6) &&
      (x.command === 'rtl' || x.command === 'change_speed' || x.command === 'land' || r(x.alt, 1) === r(y.alt, 1)) &&
      r(x.hold_s, 2) === r(y.hold_s, 2) &&
      r(x.speed, 2) === r(y.speed, 2)
    )
  })
}
