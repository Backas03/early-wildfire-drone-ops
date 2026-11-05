// 미션 검증과 거리·비행 시간 추정.
// 오류(error)는 서버(shared/protocol/mission.py)도 거부하는 것이라 저장을 막는다.
// 경고(warn)는 저장은 되지만 비행 전에 확인해야 하는 것.

import { MAX_ALT_M, MAX_SPEED_MS, type CommandType, type MissionItem, type PlanHome } from '../protocol.ts'
import { distance, formatDuration, type LatLon } from './geo.ts'

export type PlanItem = Omit<MissionItem, 'seq'>

export const COMMAND_LABEL: Record<CommandType, string> = {
  takeoff: '이륙',
  waypoint: '웨이포인트',
  loiter_time: '제자리 비행',
  change_speed: '속도 변경',
  rtl: 'RTL(귀환)',
  land: '착륙',
}

export const NEEDS_POSITION: ReadonlySet<CommandType> = new Set(['waypoint', 'loiter_time'])
export const NEEDS_ALT: ReadonlySet<CommandType> = new Set(['takeoff', 'waypoint', 'loiter_time'])

/** 지도에 점으로 그리는 항목인지 (착륙은 좌표가 있을 때만) */
export function mapPosition(it: PlanItem): LatLon | null {
  if (it.lat == null || it.lon == null) return null
  if (NEEDS_POSITION.has(it.command) || it.command === 'land') return { lat: it.lat, lon: it.lon }
  return null
}

/** 비행 시간 추정에 쓰는 기체 값. FC 파라미터에 맞춰 고친다. */
export interface Assumptions {
  /** 기본 비행 속도 m/s (WPNAV_SPEED) */
  speed: number
  /** 상승 속도 m/s (WPNAV_SPEED_UP) */
  climb: number
  /** 하강 속도 m/s (WPNAV_SPEED_DN) */
  descent: number
  /** 배터리로 비행 가능한 시간 (분) */
  flightMin: number
}

export const DEFAULT_ASSUMPTIONS: Assumptions = { speed: 5, climb: 2.5, descent: 1.5, flightMin: 10 }

const RTL_ALT = 15 // ArduCopter RTL_ALT 기본값 m
const LAND_ALT_LOW = 10 // 이 고도 아래에서는 LAND_SPEED로 천천히 내려옴
const LAND_SPEED = 0.5

export interface Stats {
  /** 총 수평 이동 거리 m */
  distance: number
  /** 예상 비행 시간 s */
  seconds: number
  maxAlt: number
  /** 홈에서 가장 먼 지점까지 거리 m (홈이 없으면 null) */
  maxFromHome: number | null
  waypoints: number
  /** 항목마다 직전 위치에서 거리 m */
  legs: (number | null)[]
  /** 미션이 끝나는 RTL/착륙 항목 번호 (없으면 null) */
  endIndex: number | null
}

function landSeconds(alt: number, a: Assumptions): number {
  if (alt <= 0) return 0
  if (alt <= LAND_ALT_LOW) return alt / LAND_SPEED
  return (alt - LAND_ALT_LOW) / a.descent + LAND_ALT_LOW / LAND_SPEED
}

/** 상대 고도(이륙 지점 기준)로 바꾼 값. 해발 기준은 홈 해발 고도를 알아야 바꿀 수 있다. */
function relativeAlt(it: PlanItem, home: PlanHome | null, fallback: number): number {
  if (it.alt == null) return fallback
  if (it.frame === 'absolute') return home && home.alt_msl ? it.alt - home.alt_msl : fallback
  return it.alt // terrain은 평지로 가정
}

export function missionStats(home: PlanHome | null, items: PlanItem[], a: Assumptions): Stats {
  let pos: LatLon | null = home
  let alt = 0
  let speed = a.speed
  const st: Stats = {
    distance: 0,
    seconds: 0,
    maxAlt: 0,
    maxFromHome: home ? 0 : null,
    waypoints: 0,
    legs: items.map(() => null),
    endIndex: null,
  }

  for (let i = 0; i < items.length; i++) {
    const it = items[i]
    switch (it.command) {
      case 'takeoff': {
        const target = relativeAlt(it, home, alt)
        if (target > alt) st.seconds += (target - alt) / a.climb
        alt = target
        break
      }
      case 'waypoint':
      case 'loiter_time': {
        if (it.lat == null || it.lon == null) break
        const p = { lat: it.lat, lon: it.lon }
        const target = relativeAlt(it, home, alt)
        const d = pos ? distance(pos, p) : 0
        const dz = target - alt
        st.legs[i] = pos ? d : null
        st.distance += d
        st.seconds += Math.max(d / speed, dz > 0 ? dz / a.climb : -dz / a.descent) + it.hold_s
        st.waypoints++
        if (home) st.maxFromHome = Math.max(st.maxFromHome ?? 0, distance(home, p))
        pos = p
        alt = target
        break
      }
      case 'change_speed':
        if (it.speed && it.speed > 0) speed = it.speed
        break
      case 'rtl': {
        if (alt < RTL_ALT) {
          st.seconds += (RTL_ALT - alt) / a.climb
          alt = RTL_ALT
        }
        if (pos && home) {
          const d = distance(pos, home)
          st.legs[i] = d
          st.distance += d
          st.seconds += d / a.speed // RTL_SPEED = 0 이면 WPNAV_SPEED
        }
        st.seconds += landSeconds(alt, a)
        st.endIndex = i
        break
      }
      case 'land': {
        const p = mapPosition(it)
        if (p && pos) {
          const d = distance(pos, p)
          st.legs[i] = d
          st.distance += d
          st.seconds += d / speed
        }
        st.seconds += landSeconds(alt, a)
        st.endIndex = i
        break
      }
    }
    st.maxAlt = Math.max(st.maxAlt, alt)
    if (st.endIndex != null) break
  }
  return st
}

export interface Issue {
  level: 'error' | 'warn'
  text: string
  /** 관련 항목 번호 (0부터) */
  index?: number
}

/** 항목 하나의 오류 (서버 검증과 같은 규칙) */
export function itemErrors(it: PlanItem): string[] {
  const errs: string[] = []
  if (NEEDS_POSITION.has(it.command) && (it.lat == null || it.lon == null)) errs.push('위치가 없어요')
  if (it.lat != null && (it.lat < -90 || it.lat > 90)) errs.push('위도 범위를 벗어났어요')
  if (it.lon != null && (it.lon < -180 || it.lon > 180)) errs.push('경도 범위를 벗어났어요')
  if (NEEDS_ALT.has(it.command)) {
    if (it.alt == null) errs.push('고도가 없어요')
    else if (it.frame !== 'absolute' && !(it.alt > 0 && it.alt <= MAX_ALT_M))
      errs.push(`고도는 0 초과 ${MAX_ALT_M} m 이하여야 해요 (지금 ${it.alt} m)`)
  }
  if (it.command === 'change_speed' && !(it.speed != null && it.speed > 0 && it.speed <= MAX_SPEED_MS))
    errs.push(`속도는 0 초과 ${MAX_SPEED_MS} m/s 이하여야 해요`)
  if (it.hold_s < 0) errs.push('대기 시간은 0 이상이어야 해요')
  return errs
}

export function checkMission(
  name: string,
  home: PlanHome | null,
  items: PlanItem[],
  stats: Stats,
  a: Assumptions,
): Issue[] {
  const out: Issue[] = []
  if (!name.trim()) out.push({ level: 'error', text: '미션 이름을 입력하세요' })
  if (!items.length) out.push({ level: 'error', text: '미션 항목이 없어요' })

  items.forEach((it, i) => {
    for (const e of itemErrors(it)) out.push({ level: 'error', text: `${i + 1}번 ${COMMAND_LABEL[it.command]}: ${e}`, index: i })
  })
  if (!items.length) return out

  if (items[0].command !== 'takeoff')
    out.push({ level: 'warn', text: '첫 항목이 이륙이 아니에요. 지상에서 AUTO로 시작하면 이륙하지 않아요.', index: 0 })
  if (stats.endIndex == null)
    out.push({ level: 'warn', text: '끝에 RTL이나 착륙이 없어요. 미션이 끝나면 마지막 지점에서 제자리 비행해요.' })
  else if (stats.endIndex < items.length - 1)
    out.push({
      level: 'warn',
      text: `${stats.endIndex + 1}번(${COMMAND_LABEL[items[stats.endIndex].command]})에서 미션이 끝나서 그 뒤 항목은 실행되지 않아요.`,
      index: stats.endIndex,
    })
  if (!home) out.push({ level: 'warn', text: '홈(이륙 지점)을 정하면 거리와 비행 시간을 계산해요.' })

  const limit = a.flightMin * 60
  if (home && stats.seconds > limit)
    out.push({
      level: 'warn',
      text: `예상 비행 시간(${formatDuration(stats.seconds)})이 배터리 비행 가능 시간(${a.flightMin}분)을 넘어요.`,
    })
  else if (home && stats.seconds > limit * 0.8)
    out.push({
      level: 'warn',
      text: `예상 비행 시간(${formatDuration(stats.seconds)})이 배터리 비행 가능 시간의 80%를 넘어요. 여유가 부족해요.`,
    })

  const absIdx = items.findIndex((it) => it.frame === 'absolute' && NEEDS_ALT.has(it.command))
  if (absIdx >= 0)
    out.push({
      level: 'warn',
      text: `${absIdx + 1}번 등 해발 고도 기준 항목은 ${MAX_ALT_M} m 제한을 자동으로 확인할 수 없어요.`,
      index: absIdx,
    })
  const terIdx = items.findIndex((it) => it.frame === 'terrain' && NEEDS_ALT.has(it.command))
  if (terIdx >= 0)
    out.push({ level: 'warn', text: '지면 기준 고도는 FC에 지형 데이터가 있어야 동작해요.', index: terIdx })
  if (items.length > 700)
    out.push({ level: 'warn', text: `항목이 ${items.length}개예요. Pixhawk에 저장할 수 있는 개수(약 700개)를 넘을 수 있어요.` })
  return out
}
