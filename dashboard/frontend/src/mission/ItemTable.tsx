import type { Dispatch } from 'react'

import type { AltFrame, CommandType } from '../protocol.ts'
import { formatDistance } from '../lib/geo.ts'
import { COMMAND_LABEL, itemErrors, NEEDS_ALT, NEEDS_POSITION, type PlanItem } from '../lib/missionCheck.ts'
import type { Action, DraftItem } from './draft.ts'
import NumInput from './NumInput.tsx'

const FRAME_LABEL: Record<AltFrame, string> = {
  relative: '이륙 지점 기준',
  absolute: '해발 고도',
  terrain: '지면 기준',
}

const COMMANDS = Object.keys(COMMAND_LABEL) as CommandType[]

interface Props {
  items: DraftItem[]
  selected: number | null
  legs: (number | null)[]
  endIndex: number | null
  dispatch: Dispatch<Action>
  /** 위치가 필요한 명령으로 바꿀 때 쓸 기본 위치 */
  fallbackPosition: (index: number) => { lat: number; lon: number } | null
  defaultAlt: number
}

/** 값 칸: 명령마다 의미가 다르다 */
function ValueCell({ it, onChange }: { it: PlanItem; onChange: (patch: Partial<PlanItem>) => void }) {
  if (it.command === 'waypoint' || it.command === 'loiter_time')
    return (
      <label className="unit">
        <NumInput value={it.hold_s} digits={1} onCommit={(v) => onChange({ hold_s: Math.max(0, v ?? 0) })} aria-label="대기 시간" />
        <span>초</span>
      </label>
    )
  if (it.command === 'change_speed')
    return (
      <label className="unit">
        <NumInput value={it.speed} digits={1} onCommit={(v) => onChange({ speed: v })} aria-label="속도" />
        <span>m/s</span>
      </label>
    )
  return <span className="muted">—</span>
}

export default function ItemTable({ items, selected, legs, endIndex, dispatch, fallbackPosition, defaultAlt }: Props) {
  const update = (index: number, patch: Partial<PlanItem>) => dispatch({ type: 'update', index, patch })

  const changeCommand = (index: number, command: CommandType) => {
    const it = items[index]
    const patch: Partial<PlanItem> = { command }
    if (NEEDS_POSITION.has(command) && (it.lat == null || it.lon == null)) {
      const p = fallbackPosition(index)
      if (p) Object.assign(patch, p)
    }
    if (NEEDS_ALT.has(command) && it.alt == null) patch.alt = defaultAlt
    if (command === 'change_speed' && it.speed == null) patch.speed = 5
    if (command === 'loiter_time' && it.hold_s === 0) patch.hold_s = 10
    dispatch({ type: 'update', index, patch })
  }

  if (!items.length) return <p className="muted pad">항목이 없어요. 지도에서 웨이포인트를 추가하세요.</p>

  return (
    <table className="items">
      <thead>
        <tr>
          <th title="Mission Planner와 같은 번호 (0번 = 홈)">#</th>
          <th>명령</th>
          <th>고도</th>
          <th>값</th>
          <th className="right">거리</th>
          <th aria-label="삭제" />
        </tr>
      </thead>
      <tbody>
        {items.map((it, i) => {
          const sel = i === selected
          const errs = itemErrors(it)
          const dead = endIndex != null && i > endIndex
          return [
            <tr
              key={it.key}
              className={[sel ? 'sel' : '', errs.length ? 'err' : '', dead ? 'dead' : ''].join(' ')}
              onClick={() => dispatch({ type: 'select', index: sel ? null : i })}
              title={errs.join('\n') || undefined}
            >
              <td className="num">{i + 1}</td>
              <td onClick={(e) => e.stopPropagation()}>
                <select value={it.command} onChange={(e) => changeCommand(i, e.target.value as CommandType)} aria-label="명령">
                  {COMMANDS.map((c) => (
                    <option key={c} value={c}>
                      {COMMAND_LABEL[c]}
                    </option>
                  ))}
                </select>
              </td>
              <td onClick={(e) => e.stopPropagation()}>
                {NEEDS_ALT.has(it.command) ? (
                  <label className="unit">
                    <NumInput value={it.alt} digits={1} onCommit={(v) => update(i, { alt: v })} aria-label="고도" />
                    <span>m</span>
                  </label>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td onClick={(e) => e.stopPropagation()}>
                <ValueCell it={it} onChange={(patch) => update(i, patch)} />
              </td>
              <td className="num right muted">{legs[i] != null ? formatDistance(legs[i]!) : ''}</td>
              <td onClick={(e) => e.stopPropagation()}>
                <button className="icon" title="삭제" aria-label={`${i + 1}번 삭제`} onClick={() => dispatch({ type: 'remove', index: i })}>
                  ✕
                </button>
              </td>
            </tr>,
            sel && (
              <tr key={`${it.key}-detail`} className="detail">
                <td />
                <td colSpan={5}>
                  <div className="detail-grid">
                    <label>
                      위도
                      <NumInput value={it.lat} digits={7} allowEmpty onCommit={(v) => update(i, { lat: v })} />
                    </label>
                    <label>
                      경도
                      <NumInput value={it.lon} digits={7} allowEmpty onCommit={(v) => update(i, { lon: v })} />
                    </label>
                    <label>
                      고도 기준
                      <select value={it.frame} onChange={(e) => update(i, { frame: e.target.value as AltFrame })}>
                        {(Object.keys(FRAME_LABEL) as AltFrame[]).map((f) => (
                          <option key={f} value={f}>
                            {FRAME_LABEL[f]}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="row-buttons">
                      <button disabled={i === 0} onClick={() => dispatch({ type: 'move', index: i, to: i - 1 })}>
                        ↑ 위로
                      </button>
                      <button
                        disabled={i === items.length - 1}
                        onClick={() => dispatch({ type: 'move', index: i, to: i + 1 })}
                      >
                        ↓ 아래로
                      </button>
                    </div>
                  </div>
                  {errs.length > 0 && <p className="err-text">{errs.join(' · ')}</p>}
                </td>
              </tr>
            ),
          ]
        })}
      </tbody>
    </table>
  )
}
