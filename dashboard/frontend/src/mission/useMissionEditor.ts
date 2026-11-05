import { useEffect, useReducer, useState } from 'react'

import { editorReducer, initEditor, newDraft, type Draft } from './draft.ts'

// 브라우저에만 저장하는 편의 기능 (새로고침해도 편집 중인 미션이 남도록). 실패해도 동작에 지장 없음.
function readStored<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

export function writeStored(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* 저장 공간 없음, 사생활 보호 모드 등 */
  }
}

/** localStorage에 남는 useState. 기본값과 합쳐서 읽으므로 필드가 늘어도 안전하다. */
export function useStoredState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    const stored = readStored<T>(key)
    if (stored == null) return initial
    if (typeof initial === 'object' && initial && !Array.isArray(initial)) return { ...initial, ...stored }
    return stored
  })
  useEffect(() => writeStored(key, value), [key, value])
  return [value, setValue] as const
}

const DRAFT_KEY = 'fire-dashboard.mission-draft.v1'
/** 순찰 영역 (미션 계획 화면). 다른 미션을 열면 비운다 */
export const AREA_KEY = 'fire-dashboard.mission-area.v1'

interface StoredDraft {
  draft: Draft
  dirty: boolean
}

export function useMissionEditor(defaultAlt: number) {
  const [state, dispatch] = useReducer(editorReducer, undefined, () => {
    const s = readStored<StoredDraft>(DRAFT_KEY)
    return s?.draft?.items ? initEditor(s.draft, s.dirty) : initEditor(newDraft(defaultAlt))
  })
  useEffect(() => {
    const t = window.setTimeout(() => writeStored(DRAFT_KEY, { draft: state.draft, dirty: state.dirty }), 300)
    return () => window.clearTimeout(t)
  }, [state.draft, state.dirty])
  return [state, dispatch] as const
}
