// 미션 편집 상태 (리듀서). 화면과 분리해 두어 실행 취소·저장 로직을 한곳에서 관리한다.

import type { CommandType, MissionPlan, PlanHome } from '../protocol.ts'
import type { PlanItem } from '../lib/missionCheck.ts'

/** 편집 중인 항목. key는 React 목록용 고유값 (저장하지 않음) */
export interface DraftItem extends PlanItem {
  key: string
}

export interface Draft {
  missionId: string | null
  name: string
  home: PlanHome | null
  items: DraftItem[]
}

export interface EditorState {
  draft: Draft
  selected: number | null
  /** 마지막 저장 이후 바뀌었는지 */
  dirty: boolean
  past: Draft[]
  future: Draft[]
}

let keyCounter = 0
export const newKey = () => `k${Date.now().toString(36)}-${(keyCounter++).toString(36)}`

export function makeItem(command: CommandType, fields: Partial<PlanItem> = {}): DraftItem {
  return {
    key: newKey(),
    command,
    lat: null,
    lon: null,
    alt: null,
    frame: 'relative',
    hold_s: 0,
    speed: null,
    ...fields,
  }
}

/** 새 미션: 이륙 → (웨이포인트) → RTL */
export function newDraft(defaultAlt: number, home: PlanHome | null = null): Draft {
  return {
    missionId: null,
    name: '새 미션',
    home,
    items: [makeItem('takeoff', { alt: defaultAlt }), makeItem('rtl')],
  }
}

export function draftFromPlan(plan: MissionPlan): Draft {
  return {
    missionId: plan.mission_id,
    name: plan.name,
    home: plan.home,
    items: plan.items.map(({ seq: _seq, ...rest }) => ({ ...rest, key: newKey() })),
  }
}

export function draftToPlan(d: Draft): MissionPlan {
  return {
    mission_id: d.missionId,
    name: d.name.trim(),
    home: d.home,
    items: d.items.map(({ key: _key, ...rest }, seq) => ({ seq, ...rest })),
  }
}

const TAIL: ReadonlySet<CommandType> = new Set(['rtl', 'land'])

/** 끝의 RTL/착륙 묶음이 시작되는 위치 */
export function tailStart(items: PlanItem[]): number {
  let i = items.length
  while (i > 0 && TAIL.has(items[i - 1].command)) i--
  return i
}

/** 맨 앞 이륙 묶음이 끝나는 위치 */
export function headEnd(items: PlanItem[]): number {
  let i = 0
  while (i < items.length && items[i].command === 'takeoff') i++
  return i
}

/** 새 항목을 넣을 위치: 선택한 항목 다음, 없으면 끝의 RTL/착륙 앞 */
export function insertIndex(items: PlanItem[], selected: number | null): number {
  if (selected != null && selected < items.length) return selected + 1
  return Math.max(tailStart(items), headEnd(items))
}

export type Action =
  | { type: 'load'; draft: Draft; dirty?: boolean }
  | { type: 'rename'; name: string }
  | { type: 'setHome'; home: PlanHome | null }
  | { type: 'insert'; at: number; items: DraftItem[] }
  | { type: 'update'; index: number; patch: Partial<PlanItem> }
  | { type: 'remove'; index: number }
  | { type: 'move'; index: number; to: number }
  | { type: 'replaceBody'; items: DraftItem[] }
  | { type: 'setAllAlt'; alt: number }
  | { type: 'select'; index: number | null }
  | { type: 'saved'; missionId: string; name: string }
  | { type: 'undo' }
  | { type: 'redo' }

const HISTORY = 100

function edit(s: EditorState, draft: Draft, selected: number | null = s.selected): EditorState {
  return {
    draft,
    selected,
    dirty: true,
    past: [...s.past.slice(-(HISTORY - 1)), s.draft],
    future: [],
  }
}

export function initEditor(draft: Draft, dirty = false): EditorState {
  return { draft, selected: null, dirty, past: [], future: [] }
}

export function editorReducer(s: EditorState, a: Action): EditorState {
  const d = s.draft
  switch (a.type) {
    case 'load':
      return initEditor(a.draft, a.dirty ?? false)
    case 'rename':
      return a.name === d.name ? s : edit(s, { ...d, name: a.name })
    case 'setHome':
      return edit(s, { ...d, home: a.home })
    case 'insert': {
      const items = [...d.items]
      items.splice(a.at, 0, ...a.items)
      return edit(s, { ...d, items }, a.at + a.items.length - 1)
    }
    case 'update': {
      const items = d.items.map((it, i) => (i === a.index ? { ...it, ...a.patch } : it))
      return edit(s, { ...d, items })
    }
    case 'remove': {
      const items = d.items.filter((_, i) => i !== a.index)
      const selected = items.length ? Math.min(a.index, items.length - 1) : null
      return edit(s, { ...d, items }, s.selected === a.index ? selected : s.selected)
    }
    case 'move': {
      if (a.to < 0 || a.to >= d.items.length || a.to === a.index) return s
      const items = [...d.items]
      const [it] = items.splice(a.index, 1)
      items.splice(a.to, 0, it)
      return edit(s, { ...d, items }, a.to)
    }
    case 'replaceBody': {
      // 앞의 이륙과 끝의 RTL/착륙은 남기고 가운데를 바꾼다
      const head = d.items.slice(0, headEnd(d.items))
      const tail = d.items.slice(Math.max(tailStart(d.items), head.length))
      return edit(s, { ...d, items: [...head, ...a.items, ...tail] }, null)
    }
    case 'setAllAlt': {
      const items = d.items.map((it) =>
        it.command === 'waypoint' || it.command === 'loiter_time' ? { ...it, alt: a.alt } : it,
      )
      return edit(s, { ...d, items })
    }
    case 'select':
      return { ...s, selected: a.index }
    case 'saved':
      return { ...s, draft: { ...d, missionId: a.missionId, name: a.name }, dirty: false }
    case 'undo': {
      const prev = s.past.at(-1)
      if (!prev) return s
      return { ...s, draft: prev, past: s.past.slice(0, -1), future: [d, ...s.future], dirty: true, selected: null }
    }
    case 'redo': {
      const next = s.future[0]
      if (!next) return s
      return { ...s, draft: next, past: [...s.past, d], future: s.future.slice(1), dirty: true, selected: null }
    }
  }
}
