import './mission.css'

import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch } from 'react'

import { sameItems } from '../drone/compare.ts'
import { useDroneCommand } from '../drone/useDroneCommand.ts'
import { distance, formatDistance, formatDuration, type LatLon } from '../lib/geo.ts'
import { longestEdgeBearing, patrolGrid } from '../lib/grid.ts'
import {
  checkMission,
  COMMAND_LABEL,
  DEFAULT_ASSUMPTIONS,
  mapPosition,
  missionStats,
  type Assumptions,
} from '../lib/missionCheck.ts'
import { hasPosition, type CommandType, type MissionSummary, type PlanHome } from '../protocol.ts'
import type { LiveState } from '../useLiveFeed.ts'
import { ApiError, missionsApi } from './api.ts'
import {
  draftFromPlan,
  draftToPlan,
  insertIndex,
  makeItem,
  newDraft,
  type Action,
  type EditorState,
} from './draft.ts'
import GridPanel, { type GridSettings } from './GridPanel.tsx'
import ItemTable from './ItemTable.tsx'
import MissionList from './MissionList.tsx'
import MissionMap, { type DroneMarker, type MapMode } from './MissionMap.tsx'
import NumInput from './NumInput.tsx'
import { usingVworld } from './tiles.ts'
import { AREA_KEY, useStoredState } from './useMissionEditor.ts'

// 대구대학교 근처 (가짜 드론·SITL 기본 홈)
const DEFAULT_CENTER: LatLon = { lat: 35.8964, lon: 128.8495 }

interface Settings {
  defaultAlt: number
  assumptions: Assumptions
}
const DEFAULT_SETTINGS: Settings = { defaultAlt: 30, assumptions: DEFAULT_ASSUMPTIONS }
const DEFAULT_GRID: GridSettings = { spacing: 40, angle: null, alt: 40, speed: 5 }

const MODE_HINT: Record<MapMode, string> = {
  select: '점을 끌어서 옮기고, 선 가운데 + 를 누르면 사이에 웨이포인트가 들어가요.',
  add: '지도를 클릭하면 웨이포인트가 추가돼요 (선택한 항목 다음, 없으면 RTL 앞). Esc로 끝내요.',
  home: '지도를 클릭해 홈(이륙 지점)을 정하세요.',
  area: '지도를 클릭해 영역 꼭짓점을 추가하세요. 꼭짓점은 끌어서 옮기고 우클릭으로 지워요.',
}

const ADDABLE: CommandType[] = ['takeoff', 'change_speed', 'rtl', 'land']

interface Notice {
  kind: 'ok' | 'err'
  text: string
}

const safeFileName = (s: string) => s.replace(/[\\/:*?"<>|]/g, '_').trim() || 'mission'

interface Props {
  live: LiveState
  editor: EditorState
  dispatch: Dispatch<Action>
}

export default function MissionPage({ live, editor, dispatch }: Props) {
  const { draft, selected, dirty } = editor
  const items = draft.items

  const [settings, setSettings] = useStoredState<Settings>('fire-dashboard.mission-settings.v1', DEFAULT_SETTINGS)
  const [area, setArea] = useStoredState<LatLon[]>(AREA_KEY, [])
  const [grid, setGrid] = useStoredState<GridSettings>('fire-dashboard.grid.v1', DEFAULT_GRID)
  const [mode, setMode] = useState<MapMode>('select')
  const [fitSignal, setFitSignal] = useState(0)
  const [missions, setMissions] = useState<MissionSummary[] | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [nameText, setNameText] = useState<string | null>(null)
  const [bulkAlt, setBulkAlt] = useState('')
  const [addCmd, setAddCmd] = useState<CommandType>('rtl')
  const fileInput = useRef<HTMLInputElement>(null)

  const a = settings.assumptions
  const stats = useMemo(() => missionStats(draft.home, items, a), [draft.home, items, a])
  const issues = useMemo(() => checkMission(draft.name, draft.home, items, stats, a), [draft.name, draft.home, items, stats, a])
  const errors = issues.filter((i) => i.level === 'error')

  const autoAngle = useMemo(() => longestEdgeBearing(area), [area])
  const gridResult = useMemo(
    () => patrolGrid(area, { spacing: grid.spacing, angleDeg: grid.angle ?? autoAngle, start: draft.home }),
    [area, grid.spacing, grid.angle, autoAngle, draft.home],
  )

  // ---------------------------------------------------------------- 알림·목록
  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(() => setNotice(null), notice.kind === 'ok' ? 4000 : 10000)
    return () => window.clearTimeout(t)
  }, [notice])

  const refreshList = useCallback(
    () =>
      missionsApi.list().then(
        (list) => {
          setMissions(list)
          setListError(null)
        },
        (e) => setListError(e instanceof ApiError ? e.message : String(e)),
      ),
    [],
  )

  useEffect(() => {
    let alive = true
    missionsApi.list().then(
      (list) => alive && setMissions(list),
      (e) => alive && setListError(e instanceof ApiError ? e.message : String(e)),
    )
    return () => {
      alive = false
    }
  }, [])

  const fail = (e: unknown) => setNotice({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
  const confirmDiscard = () => !dirty || window.confirm('저장하지 않은 변경 사항이 있어요. 버리고 계속할까요?')

  // ---------------------------------------------------------------- 미션 단위 동작
  const save = async (asCopy = false) => {
    if (errors.length) {
      setNotice({ kind: 'err', text: `저장할 수 없어요: ${errors[0].text}` })
      return
    }
    const plan = draftToPlan(asCopy ? { ...draft, name: `${draft.name} (사본)`.slice(0, 100) } : draft)
    try {
      const saved =
        draft.missionId && !asCopy ? await missionsApi.update(draft.missionId, plan) : await missionsApi.create(plan)
      dispatch({ type: 'saved', missionId: saved.mission_id!, name: saved.name })
      setNotice({ kind: 'ok', text: `"${saved.name}" 저장했어요.` })
      refreshList()
    } catch (e) {
      fail(e)
    }
  }

  const loadDraft = (next: typeof draft, dirtyAfter = false) => {
    dispatch({ type: 'load', draft: next, dirty: dirtyAfter })
    setArea([])
    setMode('select')
    setFitSignal((n) => n + 1)
  }

  const open = async (id: string) => {
    if (id === draft.missionId && !dirty) return
    if (!confirmDiscard()) return
    try {
      loadDraft(draftFromPlan(await missionsApi.get(id)))
    } catch (e) {
      fail(e)
    }
  }

  const remove = async (m: MissionSummary) => {
    if (!window.confirm(`"${m.name}" 미션을 삭제할까요? 되돌릴 수 없어요.`)) return
    try {
      await missionsApi.remove(m.mission_id)
      if (m.mission_id === draft.missionId) dispatch({ type: 'load', draft: { ...draft, missionId: null }, dirty: true })
      setNotice({ kind: 'ok', text: `"${m.name}" 삭제했어요.` })
      refreshList()
    } catch (e) {
      fail(e)
    }
  }

  const createNew = () => {
    if (!confirmDiscard()) return
    loadDraft(newDraft(settings.defaultAlt, draft.home))
  }

  const importFile = async (file: File) => {
    try {
      const plan = await missionsApi.importWaypoints(file.name.replace(/\.[^.]+$/, ''), await file.text())
      if (!confirmDiscard()) return
      loadDraft(draftFromPlan(plan), true)
      setNotice({ kind: 'ok', text: `${file.name}에서 ${plan.items.length}개 항목을 가져왔어요. 저장하려면 저장을 누르세요.` })
    } catch (e) {
      fail(e)
    }
  }

  const exportFile = async () => {
    if (errors.length) {
      setNotice({ kind: 'err', text: `내보낼 수 없어요: ${errors[0].text}` })
      return
    }
    try {
      const text = await missionsApi.exportWaypoints(draftToPlan(draft))
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `${safeFileName(draft.name)}.waypoints`
      link.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      fail(e)
    }
  }

  // ---------------------------------------------------------------- 드론과 주고받기
  const droneCmd = useDroneCommand(live.acks)
  const [targetPick, setTargetPick] = useState<string | null>(null)
  const readyDrones = Object.values(live.drones)
    .filter((d) => d.online && d.fc_connected)
    .map((d) => d.drone_id)
    .sort()
  const target = targetPick && readyDrones.includes(targetPick) ? targetPick : (readyDrones[0] ?? null)
  const onDrone = target ? live.missions[target] : undefined
  const matchesDrone = onDrone?.mission ? sameItems(items, onDrone.mission.items) : false

  const uploadToDrone = async () => {
    if (!target) return
    if (errors.length) {
      setNotice({ kind: 'err', text: `올릴 수 없어요: ${errors[0].text}` })
      return
    }
    const f = live.telemetry[target]?.flight
    if (f?.armed && f.mode === 'AUTO') {
      setNotice({
        kind: 'err',
        text: `${target}가 AUTO로 비행 중이라 미션을 바꿀 수 없어요. 실시간 관제에서 일시 정지(BRAKE)한 뒤 올리세요.`,
      })
      return
    }
    if (!window.confirm(`"${draft.name}" 미션을 ${target}에 올릴까요? 드론에 있던 미션은 지워져요.`)) return
    // 저장한 뒤 고쳤다면 저장된 미션과 내용이 다르므로 mission_id를 붙이지 않는다
    const plan = draftToPlan(dirty ? { ...draft, missionId: null } : draft)
    await droneCmd.run(
      target,
      { type: 'mission_upload', mission: plan },
      '드론에 업로드',
      `${target}에 "${plan.name}" 미션(항목 ${plan.items.length}개)을 올렸어요`,
    )
  }

  const downloadFromDrone = async () => {
    if (!target || !confirmDiscard()) return
    const ack = await droneCmd.run(target, { type: 'mission_download' }, '드론에서 가져오기', null)
    if (!ack) return
    if (!ack.mission) {
      droneCmd.setNotice({ kind: 'err', text: `${target}에 미션이 없어요` })
      return
    }
    loadDraft(draftFromPlan(ack.mission), !ack.mission.mission_id)
    droneCmd.setNotice({ kind: 'ok', text: `${target}의 미션(항목 ${ack.mission.items.length}개)을 가져왔어요` })
  }

  // ---------------------------------------------------------------- 지도 동작
  const setHome = (p: LatLon, altMsl?: number) => {
    const home: PlanHome = { lat: p.lat, lon: p.lon, alt_msl: altMsl ?? draft.home?.alt_msl ?? 0 }
    dispatch({ type: 'setHome', home })
  }

  const onMapClick = (p: LatLon) => {
    if (mode === 'add') {
      dispatch({ type: 'insert', at: insertIndex(items, selected), items: [makeItem('waypoint', { ...p, alt: settings.defaultAlt })] })
    } else if (mode === 'home') {
      setHome(p)
      setMode('select')
    } else if (mode === 'area') {
      setArea([...area, p])
    } else if (selected != null) {
      dispatch({ type: 'select', index: null })
    }
  }

  /** index 앞쪽에서 가장 가까운 위치 (명령을 바꿀 때 기본 위치) */
  const nearbyPosition = (index: number): LatLon | null => {
    for (let i = index - 1; i >= 0; i--) {
      const p = mapPosition(items[i])
      if (p) return p
    }
    for (let i = index + 1; i < items.length; i++) {
      const p = mapPosition(items[i])
      if (p) return p
    }
    return draft.home
  }

  const onInsertAt = (index: number, p: LatLon) => {
    // 고도는 앞 웨이포인트를 따르고, 앞에 없으면 뒤 웨이포인트를 따른다
    const isWp = (i: number) => (items[i].command === 'waypoint' || items[i].command === 'loiter_time') && items[i].alt != null
    const order = [...Array(index).keys()].reverse().concat([...Array(items.length - index).keys()].map((k) => k + index))
    const near = order.find(isWp)
    const alt = near != null ? items[near].alt! : settings.defaultAlt
    dispatch({ type: 'insert', at: index, items: [makeItem('waypoint', { ...p, alt })] })
  }

  const applyGrid = () => {
    const body = items.filter((it) => it.command !== 'takeoff' && it.command !== 'rtl' && it.command !== 'land')
    if (body.length && !window.confirm(`지금 있는 중간 항목 ${body.length}개를 순찰 경로로 바꿀까요? (이륙·RTL은 그대로)`))
      return
    const wps = gridResult.points.map((p) => makeItem('waypoint', { ...p, alt: grid.alt }))
    dispatch({ type: 'replaceBody', items: grid.speed ? [makeItem('change_speed', { speed: grid.speed }), ...wps] : wps })
    setMode('select')
    setNotice({ kind: 'ok', text: `순찰 경로 웨이포인트 ${wps.length}개를 넣었어요.` })
  }

  const addCommand = () => {
    let at = insertIndex(items, selected)
    if (selected == null && (addCmd === 'rtl' || addCmd === 'land')) at = items.length
    if (selected == null && addCmd === 'takeoff') at = 0
    const fields =
      addCmd === 'takeoff' ? { alt: settings.defaultAlt } : addCmd === 'change_speed' ? { speed: 5 } : {}
    dispatch({ type: 'insert', at, items: [makeItem(addCmd, fields)] })
  }

  // ---------------------------------------------------------------- 단축키
  const saveRef = useRef(save)
  useEffect(() => {
    saveRef.current = save
  })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.closest('input, select, textarea')) return
      const mod = e.ctrlKey || e.metaKey
      if (e.key === 'Escape') setMode('select')
      else if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) dispatch({ type: 'undo' })
      else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) dispatch({ type: 'redo' })
      else if (mod && e.key.toLowerCase() === 's') {
        e.preventDefault()
        saveRef.current()
      } else if ((e.key === 'Delete' || e.key === 'Backspace') && editor.selected != null)
        dispatch({ type: 'remove', index: editor.selected })
      else return
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dispatch, editor.selected])

  // ---------------------------------------------------------------- 드론
  const drones: DroneMarker[] = Object.values(live.telemetry).filter(hasPosition).map((t) => ({
    id: t.drone_id,
    lat: t.position.lat,
    lon: t.position.lon,
    heading: t.velocity.heading,
    alt: t.position.alt_rel,
    online: live.drones[t.drone_id]?.online ?? true,
  }))
  const droneHomes = Object.values(live.drones).filter((d) => d.online && d.home)

  // 지도를 처음 만들 때만 쓰는 값
  const [initialCenter] = useState<LatLon>(() => {
    const first = items.map((it) => mapPosition(it)).find((p) => p != null)
    const tel = Object.values(live.telemetry).find(hasPosition)
    return draft.home ?? first ?? (tel ? { lat: tel.position.lat, lon: tel.position.lon } : DEFAULT_CENTER)
  })

  const limitS = a.flightMin * 60
  const timeClass = !draft.home ? '' : stats.seconds > limitS ? 'bad' : stats.seconds > limitS * 0.8 ? 'warn' : 'ok'

  return (
    <div className="mission-page">
      <aside className="side">
        <section className="mission-head">
          <div className="name-row">
            <input
              className="name"
              value={nameText ?? draft.name}
              maxLength={100}
              aria-label="미션 이름"
              onFocus={() => setNameText(draft.name)}
              onChange={(e) => setNameText(e.target.value)}
              onBlur={() => {
                if (nameText != null) dispatch({ type: 'rename', name: nameText })
                setNameText(null)
              }}
              onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
            />
            <span className={`state ${dirty ? 'warn' : 'ok'}`}>
              {!draft.missionId ? '저장 안 됨' : dirty ? '수정됨' : '저장됨'}
            </span>
          </div>
          <div className="row-buttons">
            <button className="primary" onClick={() => save()} disabled={errors.length > 0} title="Ctrl+S">
              저장
            </button>
            {draft.missionId && <button onClick={() => save(true)}>사본 저장</button>}
            <button onClick={createNew}>새 미션</button>
            <button onClick={() => fileInput.current?.click()}>가져오기</button>
            <button onClick={exportFile} disabled={errors.length > 0}>
              내보내기
            </button>
            <input
              ref={fileInput}
              type="file"
              accept=".waypoints,.txt"
              hidden
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.target.value = ''
                if (f) importFile(f)
              }}
            />
          </div>
          <div className="row-buttons">
            <button onClick={() => dispatch({ type: 'undo' })} disabled={!editor.past.length} title="Ctrl+Z">
              ↶ 실행 취소
            </button>
            <button onClick={() => dispatch({ type: 'redo' })} disabled={!editor.future.length} title="Ctrl+Y">
              ↷ 다시 실행
            </button>
          </div>
          <div className="drone-row">
            <span className="muted small">드론</span>
            {readyDrones.length ? (
              <>
                <select value={target ?? ''} onChange={(e) => setTargetPick(e.target.value)} aria-label="미션을 주고받을 드론">
                  {readyDrones.map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
                </select>
                <button onClick={uploadToDrone} disabled={!!droneCmd.running || errors.length > 0}>
                  드론에 업로드
                </button>
                <button onClick={downloadFromDrone} disabled={!!droneCmd.running}>
                  드론에서 가져오기
                </button>
                {onDrone && (
                  <span className={`state ${matchesDrone ? 'ok' : 'warn'}`}>
                    {matchesDrone ? '드론과 같음' : onDrone.mission ? '드론 미션과 다름' : '드론에 미션 없음'}
                  </span>
                )}
              </>
            ) : (
              <span className="muted small">연결된 드론 없음</span>
            )}
          </div>
          {droneCmd.running ? (
            <p className="notice ok" role="status">
              {droneCmd.running.label} 중…{droneCmd.progress != null && ` ${Math.round(droneCmd.progress * 100)}%`}
            </p>
          ) : (
            droneCmd.notice && (
              <p className={`notice ${droneCmd.notice.kind}`} role="status">
                {droneCmd.notice.text}
              </p>
            )
          )}
          {notice && (
            <p className={`notice ${notice.kind}`} role="status">
              {notice.text}
            </p>
          )}
        </section>

        <details className="panel" open>
          <summary>
            <h3>저장된 미션{missions ? ` (${missions.length})` : ''}</h3>
          </summary>
          <MissionList missions={missions} error={listError} currentId={draft.missionId} onOpen={open} onDelete={remove} />
        </details>

        <section className="panel">
          <h3>미션 요약</h3>
          <dl className="stats">
            <div>
              <dt>비행 거리</dt>
              <dd>{draft.home ? formatDistance(stats.distance) : '—'}</dd>
            </div>
            <div>
              <dt>예상 비행 시간</dt>
              <dd className={timeClass}>
                {draft.home ? formatDuration(stats.seconds) : '—'}
                <small> / {a.flightMin}분</small>
              </dd>
            </div>
            <div>
              <dt>최대 고도</dt>
              <dd>{Math.round(stats.maxAlt)} m</dd>
            </div>
            <div>
              <dt>홈에서 최대 거리</dt>
              <dd>{stats.maxFromHome != null ? formatDistance(stats.maxFromHome) : '—'}</dd>
            </div>
          </dl>
          {issues.length > 0 ? (
            <ul className="issues">
              {issues.map((iss, i) => (
                <li
                  key={i}
                  className={iss.level}
                  onClick={() => iss.index != null && dispatch({ type: 'select', index: iss.index })}
                >
                  {iss.level === 'error' ? '오류' : '확인'} · {iss.text}
                </li>
              ))}
            </ul>
          ) : (
            <p className="ok-text small">문제 없음</p>
          )}
        </section>

        <GridPanel
          drawing={mode === 'area'}
          vertexCount={area.length}
          settings={grid}
          autoAngle={autoAngle}
          result={gridResult}
          cruiseSpeed={a.speed}
          onChange={setGrid}
          onToggleDrawing={() => setMode(mode === 'area' ? 'select' : 'area')}
          onApply={applyGrid}
          onClear={() => setArea([])}
        />

        <section className="panel">
          <div className="panel-head">
            <h3>미션 항목 ({items.length})</h3>
          </div>
          <div className="toolbar">
            <label className="unit" title="지도에서 새로 찍는 웨이포인트의 고도">
              새 점 고도
              <NumInput
                value={settings.defaultAlt}
                digits={1}
                onCommit={(v) => v && v > 0 && setSettings({ ...settings, defaultAlt: v })}
              />
              <span>m</span>
            </label>
            <span className="unit">
              <input
                type="number"
                placeholder="고도"
                value={bulkAlt}
                onChange={(e) => setBulkAlt(e.target.value)}
                aria-label="모든 웨이포인트 고도"
              />
              <button
                disabled={!(Number(bulkAlt) > 0)}
                onClick={() => {
                  dispatch({ type: 'setAllAlt', alt: Number(bulkAlt) })
                  setBulkAlt('')
                }}
              >
                모든 점에 적용
              </button>
            </span>
          </div>
          <ItemTable
            items={items}
            selected={selected}
            legs={stats.legs}
            endIndex={stats.endIndex}
            dispatch={dispatch}
            fallbackPosition={nearbyPosition}
            defaultAlt={settings.defaultAlt}
          />
          <div className="toolbar">
            <select value={addCmd} onChange={(e) => setAddCmd(e.target.value as CommandType)} aria-label="추가할 명령">
              {ADDABLE.map((c) => (
                <option key={c} value={c}>
                  {COMMAND_LABEL[c]}
                </option>
              ))}
            </select>
            <button onClick={addCommand}>명령 추가</button>
            <span className="muted small">웨이포인트는 지도에서 추가</span>
          </div>
        </section>

        <details className="panel">
          <summary>
            <h3>비행 시간 추정 기준</h3>
          </summary>
          <p className="muted small">FC 파라미터와 배터리에 맞게 고치세요. 추정은 바람·가감속을 무시한 대략값이에요.</p>
          <div className="form-grid">
            {(
              [
                ['speed', '기본 속도 (WPNAV_SPEED)', 'm/s'],
                ['climb', '상승 속도 (WPNAV_SPEED_UP)', 'm/s'],
                ['descent', '하강 속도 (WPNAV_SPEED_DN)', 'm/s'],
                ['flightMin', '배터리 비행 가능 시간', '분'],
              ] as const
            ).map(([k, label, unit]) => (
              <label key={k}>
                {label}
                <span className="unit">
                  <NumInput
                    value={a[k]}
                    digits={1}
                    onCommit={(v) =>
                      v && v > 0 && setSettings({ ...settings, assumptions: { ...a, [k]: v } })
                    }
                  />
                  <span>{unit}</span>
                </span>
              </label>
            ))}
          </div>
        </details>
      </aside>

      <div className="map-wrap">
        <div className="map-tools">
          <div className="seg" role="group" aria-label="지도 모드">
            {(
              [
                ['select', '선택'],
                ['add', '웨이포인트 추가'],
                ['home', '홈 지정'],
                ['area', '순찰 영역'],
              ] as const
            ).map(([m, label]) => (
              <button key={m} className={mode === m ? 'on' : ''} aria-pressed={mode === m} onClick={() => setMode(m)}>
                {label}
              </button>
            ))}
          </div>
          <p className="hint">{MODE_HINT[mode]}</p>
        </div>

        <div className="map-actions">
          <button onClick={() => setFitSignal((n) => n + 1)}>미션 전체 보기</button>
          {droneHomes.map((d) => (
            <button
              key={d.drone_id}
              title={`${d.drone_id}가 보고한 홈 위치를 미션 홈으로`}
              onClick={() => setHome({ lat: d.home!.lat, lon: d.home!.lon }, d.home!.alt_msl)}
            >
              {d.drone_id} 홈 사용
            </button>
          ))}
          {draft.home && droneHomes.length > 0 && (
            <span className="muted small">
              {droneHomes
                .map((d) => `${d.drone_id} 홈과 ${formatDistance(distance(draft.home!, d.home!))}`)
                .join(' · ')}
            </span>
          )}
        </div>

        <MissionMap
          draft={draft}
          selected={selected}
          mode={mode}
          area={area}
          gridPreview={mode === 'area' ? gridResult.points : []}
          drones={drones}
          fitSignal={fitSignal}
          initialCenter={initialCenter}
          onMapClick={onMapClick}
          onSelect={(i) => dispatch({ type: 'select', index: i })}
          onMoveItem={(i, p) => dispatch({ type: 'update', index: i, patch: p })}
          onInsertAt={onInsertAt}
          onMoveHome={(p) => setHome(p)}
          onAreaChange={setArea}
        />
        {!usingVworld && (
          <p className="tile-note">VWorld 키가 없어서 OpenStreetMap을 쓰는 중 (README 참고)</p>
        )}
      </div>
    </div>
  )
}
