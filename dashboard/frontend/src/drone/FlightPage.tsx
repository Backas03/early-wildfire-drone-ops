import '../mission/mission.css'
import './flight.css'

import { useEffect, useMemo, useState } from 'react'

import { distance, formatDistance, formatDuration, type LatLon } from '../lib/geo.ts'
import { COMMAND_LABEL, DEFAULT_ASSUMPTIONS, missionStats } from '../lib/missionCheck.ts'
import type { DroneMarker } from '../mission/mapIcons.ts'
import { usingVworld } from '../mission/tiles.ts'
import { useStoredState } from '../mission/useMissionEditor.ts'
import {
  hasPosition,
  type DroneMission,
  type DroneStatus,
  type FlightMode,
  type MissionPlan,
  type StatusText,
  type Telemetry,
} from '../protocol.ts'
import type { LiveState } from '../useLiveFeed.ts'
import { cameraFootprint } from './camera.ts'
import EdgePanel from './EdgePanel.tsx'
import FlightMap from './FlightMap.tsx'
import type { GotoTarget, TaskDrone } from './MapTasks.tsx'
import { useDroneCommand, type Notice } from './useDroneCommand.ts'

const DEFAULT_CENTER: LatLon = { lat: 35.8964, lon: 128.8495 }
const STALE_MS = 5000

interface ModeButton {
  mode: FlightMode
  label: string
  title: string
  confirm?: string
  cls?: string
}

// 시동·이륙은 없다 (조종기로). 멈춤·귀환은 급할 때 쓰므로 확인 없이 바로 보낸다.
// 멈춤에 LOITER를 쓰지 않는 이유: LOITER는 조종기 스로틀을 따르므로 스틱이 내려가 있으면 그대로
// 하강한다 (SITL에서 24 m → 착륙 확인). BRAKE는 스틱과 관계없이 위치·고도를 유지한다.
const MODE_BUTTONS: ModeButton[] = [
  {
    mode: 'AUTO',
    label: '미션 시작·재개',
    title: 'AUTO: 드론에 올린 미션을 따라 비행 (멈췄던 곳부터 이어서)',
    confirm: '드론이 미션을 따라 비행해요 (AUTO). 계속할까요?',
    cls: 'primary',
  },
  {
    mode: 'BRAKE',
    label: '일시 정지',
    title: 'BRAKE: 그 자리에 멈춰 위치·고도 유지 (조종기 스틱과 무관). 미션 시작·재개로 이어서 비행',
  },
  { mode: 'RTL', label: '귀환 (RTL)', title: 'RTL: 홈으로 돌아와 착륙', cls: 'warn' },
  {
    mode: 'LAND',
    label: '바로 착륙',
    title: 'LAND: 지금 위치에 바로 착륙',
    confirm: '지금 위치에 바로 착륙해요. 아래가 안전한지 확인했나요?',
  },
]

const FIX_LABEL = ['없음', '없음', '2D', '3D', 'DGPS', 'RTK Float', 'RTK Fixed']

interface Settings {
  selected: string | null
  follow: boolean
}

interface Props {
  live: LiveState
  clearTrail: (droneId: string) => void
  onOpenInEditor: (plan: MissionPlan) => void
}

export default function FlightPage({ live, clearTrail, onOpenInEditor }: Props) {
  const [settings, setSettings] = useStoredState<Settings>('fire-dashboard.flight.v1', { selected: null, follow: true })
  const [fitSignal, setFitSignal] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const modeCmd = useDroneCommand(live.acks)
  const missionCmd = useDroneCommand(live.acks)
  const taskCmd = useDroneCommand(live.acks) // 지도 우클릭 메뉴 (이 위치로 보내기 · 웨이포인트 · 영역 순찰)
  const [gotoTargets, setGotoTargets] = useState<Record<string, GotoTarget>>({})

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])

  const ids = useMemo(
    () => [...new Set([...Object.keys(live.drones), ...Object.keys(live.telemetry)])].sort(),
    [live.drones, live.telemetry],
  )
  const selectedId =
    settings.selected && ids.includes(settings.selected)
      ? settings.selected
      : (ids.find((id) => live.drones[id]?.online) ?? ids[0] ?? null)

  const tel = selectedId ? live.telemetry[selectedId] : undefined
  const status = selectedId ? live.drones[selectedId] : undefined
  const dm = selectedId ? live.missions[selectedId] : undefined
  const mission = dm?.mission ?? null
  const home: LatLon | null = status?.home ? { lat: status.home.lat, lon: status.home.lon } : (mission?.home ?? null)
  const fresh = !!tel && now - tel.ts < STALE_MS
  const linkOk = !!status?.online && !!status.fc_connected

  const drones: DroneMarker[] = Object.values(live.telemetry)
    .filter(hasPosition)
    .map((t) => ({
      id: t.drone_id,
      lat: t.position.lat,
      lon: t.position.lon,
      heading: t.velocity.heading,
      alt: t.position.alt_rel,
      online: (live.drones[t.drone_id]?.online ?? true) && now - t.ts < STALE_MS,
    }))

  const [initialCenter] = useState<LatLon>(() => {
    const t = selectedId ? live.telemetry[selectedId] : undefined
    if (t && hasPosition(t)) return { lat: t.position.lat, lon: t.position.lon }
    const h = selectedId ? live.drones[selectedId]?.home : null
    return h ? { lat: h.lat, lon: h.lon } : DEFAULT_CENTER
  })

  const select = (id: string) => {
    setSettings({ ...settings, selected: id })
    setFitSignal((n) => n + 1)
  }

  const setMode = (b: ModeButton) => {
    if (!selectedId) return
    if (b.confirm && !window.confirm(b.confirm)) return
    modeCmd.run(selectedId, { type: 'set_mode', mode: b.mode }, `${b.label} (${b.mode})`, `${selectedId}: ${b.mode} 모드로 바꿨어요`)
  }

  const clearMission = () => {
    if (!selectedId) return
    if (!window.confirm(`${selectedId}에 들어 있는 미션을 지울까요?`)) return
    missionCmd.run(selectedId, { type: 'mission_clear' }, '드론 미션 삭제', `${selectedId}의 미션을 지웠어요`)
  }

  // 지도 우클릭 메뉴에서 쓰는 드론: FC가 연결된 것만
  const taskDrones: TaskDrone[] = ids
    .filter((id) => live.drones[id]?.online && live.drones[id]?.fc_connected)
    .map((id) => {
      const t = live.telemetry[id]
      const h = live.drones[id]?.home
      return {
        id,
        pos: t && hasPosition(t) ? { lat: t.position.lat, lon: t.position.lon } : null,
        alt: t ? t.position.alt_rel : null,
        armed: !!t?.flight.armed,
        mode: t?.flight.mode ?? '?',
        home: h ? { lat: h.lat, lon: h.lon } : null,
      }
    })
  // GUIDED로 가는 중인 드론만 목표를 보여 준다 (다른 모드로 바뀌면 목표는 끝)
  const activeTargets = Object.values(gotoTargets).filter((g) => live.telemetry[g.droneId]?.flight.mode === 'GUIDED')

  const gotoHere = async (droneId: string, at: LatLon, alt: number) => {
    const ack = await taskCmd.run(
      droneId,
      { type: 'goto', lat: at.lat, lon: at.lon, alt },
      '이 위치로 보내기',
      `${droneId}: 지정한 위치로 가는 중이에요 (도착하면 그 자리에서 대기)`,
    )
    if (ack) setGotoTargets((s) => ({ ...s, [droneId]: { droneId, at, alt } }))
    return !!ack
  }

  // 지도에서 만든 미션: (AUTO로 비행 중이면 일시 정지) → 올리기 → (떠 있으면) 출발
  const runQuickMission = async (droneId: string, plan: MissionPlan) => {
    const f = live.telemetry[droneId]?.flight
    if (f?.armed && f.mode === 'AUTO') {
      if (!(await taskCmd.run(droneId, { type: 'set_mode', mode: 'BRAKE' }, '일시 정지', null))) return false
    }
    if (!(await taskCmd.run(droneId, { type: 'mission_upload', mission: plan }, '미션 올리기', null))) return false
    if (!f?.armed) {
      taskCmd.setNotice({ kind: 'ok', text: `${droneId}에 올렸어요. 조종기로 시동을 건 뒤 “미션 시작·재개”를 누르세요` })
      return true
    }
    await taskCmd.run(droneId, { type: 'set_mode', mode: 'AUTO' }, '출발', `${droneId}: 새 경로로 출발했어요`)
    return true
  }

  const refreshMission = () => {
    if (selectedId) missionCmd.run(selectedId, { type: 'mission_download' }, '드론 미션 다시 읽기', null)
  }

  return (
    <div className="mission-page flight-page">
      <aside className="side">
        <section className="panel">
          <h3>드론 ({ids.length})</h3>
          {ids.length === 0 ? (
            <p className="muted small">
              연결된 드론이 없어요. 시뮬레이션은 README의 “시뮬레이션으로 비행해 보기”를 따라 SITL과 drone-agent를 켜세요.
            </p>
          ) : (
            <ul className="drone-list">
              {ids.map((id) => (
                <DroneCard
                  key={id}
                  id={id}
                  tel={live.telemetry[id]}
                  status={live.drones[id]}
                  now={now}
                  selected={id === selectedId}
                  onSelect={() => select(id)}
                />
              ))}
            </ul>
          )}
        </section>

        {selectedId && (
          <>
            <section className="panel">
              <div className="panel-head">
                <h3>{selectedId} 상태</h3>
                <span className={`pill ${linkOk && fresh ? 'ok' : 'bad'}`}>
                  {!status?.online ? '오프라인' : !status.fc_connected ? 'FC 연결 안 됨' : !fresh ? '신호 끊김' : '연결됨'}
                </span>
              </div>
              {tel ? <Readout tel={tel} home={home} now={now} /> : <p className="muted small">텔레메트리를 기다리는 중…</p>}
            </section>

            <EdgePanel
              status={live.edge[selectedId]}
              preview={live.previews[selectedId]}
              frame={live.detections[selectedId]}
              snapshots={live.snapshots[selectedId] ?? []}
              now={now}
            />

            <section className="panel">
              <h3>비행 모드</h3>
              <div className="mode-grid">
                {MODE_BUTTONS.map((b) => (
                  <button
                    key={b.mode}
                    className={[b.cls ?? '', tel?.flight.mode === b.mode ? 'current' : ''].join(' ')}
                    title={b.title}
                    disabled={!linkOk || !!modeCmd.running || !!taskCmd.running}
                    onClick={() => setMode(b)}
                  >
                    {b.label}
                    <small>{b.mode}</small>
                  </button>
                ))}
              </div>
              <CommandStatus running={modeCmd.running?.label} progress={null} notice={modeCmd.notice} />
              <p className="muted small note">
                시동·이륙은 대시보드에서 하지 않아요 (조종기로). 시뮬레이션에서는{' '}
                <code>python sim/sitl/pilot.py start</code>가 조종사 역할을 해요.
              </p>
            </section>

            <section className="panel">
              <div className="panel-head">
                <h3>드론에 들어 있는 미션</h3>
                <button className="small-btn" onClick={refreshMission} disabled={!linkOk || !!missionCmd.running}>
                  다시 읽기
                </button>
              </div>
              <DroneMissionInfo dm={dm} tel={tel} home={home} />
              <div className="row-buttons">
                <button onClick={() => mission && onOpenInEditor(mission)} disabled={!mission}>
                  미션 계획 화면에서 열기
                </button>
                <button onClick={clearMission} disabled={!linkOk || !mission || !!missionCmd.running}>
                  드론 미션 삭제
                </button>
              </div>
              <CommandStatus running={missionCmd.running?.label} progress={missionCmd.progress} notice={missionCmd.notice} />
              <p className="muted small note">
                새 미션은 “미션 계획” 탭에서 만들고 “드론에 업로드”로 올려요. 간단한 경로는 지도를 우클릭해 바로 찍을 수 있어요.
              </p>
            </section>

            <section className="panel">
              <h3>FC 메시지</h3>
              <FcLog texts={live.texts[selectedId] ?? []} />
            </section>
          </>
        )}
      </aside>

      <div className="map-wrap">
        <div className="map-actions">
          <button onClick={() => setFitSignal((n) => n + 1)}>전체 보기</button>
          <label className="check">
            <input
              type="checkbox"
              checked={settings.follow}
              onChange={(e) => setSettings({ ...settings, follow: e.target.checked })}
            />
            드론 따라가기
          </label>
          {selectedId && (live.trails[selectedId]?.length ?? 0) > 1 && (
            <button onClick={() => clearTrail(selectedId)}>궤적 지우기</button>
          )}
        </div>
        <FlightMap
          drones={drones}
          selectedId={selectedId}
          trails={live.trails}
          mission={mission}
          home={home}
          currentSeq={tel?.flight.armed && tel.mission ? tel.mission.current_seq : null}
          follow={settings.follow}
          fitSignal={fitSignal}
          initialCenter={initialCenter}
          onSelectDrone={select}
          footprint={tel && hasPosition(tel) && live.edge[selectedId ?? '']?.online ? cameraFootprint(tel) : null}
          simFires={live.simFires}
          onPlaceJump={() => setSettings((s) => ({ ...s, follow: false }))}
          tasks={{
            drones: taskDrones,
            busy: !!taskCmd.running || !!modeCmd.running || !!missionCmd.running,
            targets: activeTargets,
            onGoto: gotoHere,
            onRun: runQuickMission,
          }}
        />
        <div className="map-toast">
          <CommandStatus running={taskCmd.running?.label} progress={null} notice={taskCmd.notice} />
        </div>
        {!usingVworld && <p className="tile-note">VWorld 키가 없어서 OpenStreetMap을 쓰는 중 (README 참고)</p>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------

function DroneCard(p: {
  id: string
  tel?: Telemetry
  status?: DroneStatus
  now: number
  selected: boolean
  onSelect: () => void
}) {
  const online = !!p.status?.online && !!p.tel && p.now - p.tel.ts < STALE_MS
  return (
    <li className={p.selected ? 'on' : ''}>
      <button className="link" onClick={p.onSelect} aria-pressed={p.selected}>
        <strong>
          <span className={`dot ${online ? 'ok' : 'bad'}`} /> {p.id}
        </strong>
        <span className="muted small">
          {p.tel
            ? `${p.tel.flight.mode} · ${p.tel.flight.armed ? '시동 걸림' : '시동 꺼짐'} · ${p.tel.position.alt_rel.toFixed(0)} m` +
              (p.tel.battery.remaining != null ? ` · 배터리 ${p.tel.battery.remaining}%` : '')
            : p.status?.online
              ? 'FC 응답 기다리는 중'
              : '오프라인'}
        </span>
      </button>
    </li>
  )
}

function Readout({ tel, home, now }: { tel: Telemetry; home: LatLon | null; now: number }) {
  const b = tel.battery
  const batClass = b.remaining == null ? '' : b.remaining < 20 ? 'bad' : b.remaining < 35 ? 'warn' : ''
  const age = Math.max(0, Math.round((now - tel.ts) / 1000))
  const toHome = home && hasPosition(tel) ? distance(home, { lat: tel.position.lat, lon: tel.position.lon }) : null
  return (
    <dl className="stats readout">
      <div>
        <dt>모드</dt>
        <dd>{tel.flight.mode}</dd>
      </div>
      <div>
        <dt>시동</dt>
        <dd className={tel.flight.armed ? 'warn' : ''}>{tel.flight.armed ? '걸림 (비행 가능)' : '꺼짐'}</dd>
      </div>
      <div>
        <dt>고도 (홈 기준)</dt>
        <dd>
          {tel.position.alt_rel.toFixed(1)} m <small>해발 {tel.position.alt_msl.toFixed(0)} m</small>
        </dd>
      </div>
      <div>
        <dt>속도</dt>
        <dd>
          {tel.velocity.ground_speed.toFixed(1)} m/s <small>상승 {tel.velocity.climb.toFixed(1)}</small>
        </dd>
      </div>
      <div>
        <dt>배터리</dt>
        <dd className={batClass}>
          {b.remaining != null ? `${b.remaining}%` : '—'} <small>{b.voltage.toFixed(1)} V</small>
        </dd>
      </div>
      <div>
        <dt>GPS</dt>
        <dd className={tel.gps.fix_type < 3 ? 'bad' : ''}>
          {FIX_LABEL[tel.gps.fix_type] ?? `fix ${tel.gps.fix_type}`} <small>위성 {tel.gps.satellites}개</small>
        </dd>
      </div>
      <div>
        <dt>홈까지</dt>
        <dd>{toHome != null ? formatDistance(toHome) : '—'}</dd>
      </div>
      <div>
        <dt>마지막 수신</dt>
        <dd className={age * 1000 >= STALE_MS ? 'bad' : ''}>{age <= 1 ? '방금' : `${age}초 전`}</dd>
      </div>
    </dl>
  )
}

function DroneMissionInfo({ dm, tel, home }: { dm?: DroneMission; tel?: Telemetry; home: LatLon | null }) {
  const plan = dm?.mission
  const stats = useMemo(() => (plan ? missionStats(home && { ...home, alt_msl: 0 }, plan.items, DEFAULT_ASSUMPTIONS) : null), [plan, home])
  if (!dm) return <p className="muted small">드론 미션을 확인하는 중…</p>
  if (dm.error) return <p className="err-text">드론 미션을 읽지 못했어요: {dm.error}</p>
  if (!plan) return <p className="muted small">드론에 미션이 없어요.</p>

  const cur = tel?.mission?.current_seq
  const item = cur != null ? plan.items[cur] : undefined
  const total = plan.items.length
  const flying = tel?.flight.armed && tel.flight.mode === 'AUTO'
  return (
    <div className="drone-mission">
      <p className="mission-name">
        <strong>{plan.name}</strong> <span className="muted small">항목 {total}개</span>
      </p>
      {stats && home && (
        <p className="muted small">
          비행 거리 {formatDistance(stats.distance)} · 예상 {formatDuration(stats.seconds)} (기본 속도 {DEFAULT_ASSUMPTIONS.speed} m/s 기준)
        </p>
      )}
      {cur != null && tel?.flight.armed && (
        <>
          <div className="progress" aria-label="미션 진행">
            <div style={{ width: `${Math.min(100, ((cur + (flying ? 0.5 : 0)) / total) * 100)}%` }} />
          </div>
          <p className="small">
            {flying ? '진행 중' : '멈춤 · 재개하면'}: {cur + 1}번 {item ? COMMAND_LABEL[item.command] : ''} ({cur + 1}/{total})
          </p>
        </>
      )}
    </div>
  )
}

function CommandStatus({ running, progress, notice }: { running?: string; progress: number | null; notice: Notice | null }) {
  if (running)
    return (
      <p className="notice ok" role="status">
        {running} 중…{progress != null && ` ${Math.round(progress * 100)}%`}
      </p>
    )
  if (notice)
    return (
      <p className={`notice ${notice.kind}`} role="status">
        {notice.text}
      </p>
    )
  return null
}

function FcLog({ texts }: { texts: StatusText[] }) {
  if (!texts.length) return <p className="muted small">아직 받은 메시지가 없어요.</p>
  return (
    <ol className="fc-log">
      {texts.map((t, i) => (
        <li key={`${t.ts}-${i}`} className={t.severity <= 3 ? 'bad' : t.severity === 4 ? 'warn' : ''}>
          <time>{new Date(t.ts).toLocaleTimeString('ko-KR', { hour12: false })}</time> {t.text}
        </li>
      ))}
    </ol>
  )
}
