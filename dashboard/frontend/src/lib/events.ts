// 화재 이벤트 표시용 계산 (컴포넌트가 아닌 것)
import type { EventStatus, FireEvent } from '../protocol.ts'

export const EVENT_STATUS_LABEL: Record<EventStatus, string> = {
  new: '신규',
  acknowledged: '확인함',
  resolved: '종료',
  false_alarm: '오탐',
}

const CLS_KO: Record<FireEvent['classes'][number], string> = { fire: '불', smoke: '연기' }

export const isOpen = (e: FireEvent) => e.status === 'new' || e.status === 'acknowledged'

/** 진행 중인 것 먼저, 그 안에서는 신규 → 최근에 본 것 */
export function sortEvents(list: FireEvent[]): FireEvent[] {
  const rank = (e: FireEvent) => (e.status === 'new' ? 0 : e.status === 'acknowledged' ? 1 : 2)
  return [...list].sort((a, b) => rank(a) - rank(b) || b.last_seen - a.last_seen)
}

export const eventKinds = (e: FireEvent) => e.classes.map((c) => CLS_KO[c]).join('·')

/** "연기 의심 · 최고 82% · 서버 확인 2/3" */
export function eventSummary(e: FireEvent): string {
  const checks = e.check_confirmed + e.check_rejected
  return `${eventKinds(e)} 의심 · 최고 ${Math.floor(e.max_conf * 100)}%${checks ? ` · 서버 확인 ${e.check_confirmed}/${checks}` : ''}`
}
