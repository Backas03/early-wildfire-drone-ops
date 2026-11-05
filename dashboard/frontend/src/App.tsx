import { useEffect, useState } from 'react'

import FlightPage from './drone/FlightPage.tsx'
import { draftFromPlan } from './mission/draft.ts'
import MissionPage from './mission/MissionPage.tsx'
import { AREA_KEY, useMissionEditor, writeStored } from './mission/useMissionEditor.ts'
import type { MissionPlan } from './protocol.ts'
import StatusPage from './StatusPage.tsx'
import { useLiveFeed } from './useLiveFeed.ts'

type Tab = 'mission' | 'flight' | 'status'

const TABS: [Tab, string][] = [
  ['mission', '미션 계획'],
  ['flight', '실시간 관제'],
  ['status', '연결 상태'],
]

const tabFromHash = (): Tab => TABS.find(([t]) => location.hash === `#/${t}`)?.[0] ?? 'mission'

export default function App() {
  const [live, clearTrail] = useLiveFeed()
  // 편집 상태는 탭을 바꿔도 남도록 여기서 들고 있는다
  const [editor, dispatch] = useMissionEditor(30)
  const [tab, setTab] = useState<Tab>(tabFromHash)

  useEffect(() => {
    const onHash = () => setTab(tabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  /** 드론에 들어 있는 미션을 편집기로 가져간다 */
  const openInEditor = (plan: MissionPlan) => {
    if (editor.dirty && !window.confirm('미션 계획 화면에 저장하지 않은 변경 사항이 있어요. 버리고 드론 미션을 열까요?'))
      return
    // 저장된 미션을 그대로 올린 것이면 mission_id가 남아 있다
    dispatch({ type: 'load', draft: draftFromPlan(plan), dirty: !plan.mission_id })
    writeStored(AREA_KEY, [])
    location.hash = '#/mission'
  }

  const online = Object.values(live.drones).filter((d) => d.online).length

  return (
    <div className="app">
      <header className="topbar">
        <h1>산불 탐지 드론 관제</h1>
        <nav>
          {TABS.map(([t, label]) => (
            <a key={t} href={`#/${t}`} className={tab === t ? 'on' : ''} aria-current={tab === t ? 'page' : undefined}>
              {label}
            </a>
          ))}
        </nav>
        <span className="spacer" />
        <span className="muted small">드론 {online}대 온라인</span>
        <span className={live.connected ? 'pill ok' : 'pill bad'}>API {live.connected ? '연결됨' : '연결 안 됨'}</span>
      </header>
      {tab === 'mission' && <MissionPage live={live} editor={editor} dispatch={dispatch} />}
      {tab === 'flight' && <FlightPage live={live} clearTrail={clearTrail} onOpenInEditor={openInEditor} />}
      {tab === 'status' && <StatusPage live={live} />}
    </div>
  )
}
