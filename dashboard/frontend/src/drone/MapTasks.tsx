// 실시간 관제 지도의 우클릭 메뉴: 이 위치로 보내기 · 웨이포인트 찍기 · 영역 순찰.
// MapContainer 안에 넣는다. 그리는 동안의 설정 칸은 지도 왼쪽 위에 붙는다.

import L from 'leaflet'
import { Fragment, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { CircleMarker, Marker, Polygon, Polyline, Popup, Tooltip, useMap, useMapEvents } from 'react-leaflet'

import { distance, formatDistance, formatDuration, type LatLon } from '../lib/geo.ts'
import { cameraSwath } from '../lib/grid.ts'
import { DEFAULT_ASSUMPTIONS } from '../lib/missionCheck.ts'
import {
  areaPoints,
  defaultSpacing,
  QUICK_END_LABEL,
  quickPlan,
  routeLength,
  timeLabel,
  type QuickEnd,
} from '../lib/quickMission.ts'
import { cachedIcon, wpIcon } from '../mission/mapIcons.ts'
import NumInput from '../mission/NumInput.tsx'
import { MAX_ALT_M, type MissionPlan } from '../protocol.ts'

export interface TaskDrone {
  id: string
  pos: LatLon | null
  /** 홈 기준 고도 m */
  alt: number | null
  armed: boolean
  mode: string
  home: LatLon | null
}

export interface GotoTarget {
  droneId: string
  at: LatLon
  alt: number
}

interface Props {
  /** FC가 연결된 드론 */
  drones: TaskDrone[]
  defaultDroneId: string | null
  busy: boolean
  /** GUIDED로 가는 중인 드론의 목표 */
  targets: GotoTarget[]
  onGoto: (droneId: string, at: LatLon, alt: number) => Promise<boolean>
  onRun: (droneId: string, plan: MissionPlan) => Promise<boolean>
}

type DrawKind = 'wp' | 'area'

interface Draw {
  kind: DrawKind
  droneId: string
  points: LatLon[]
  alt: number
  spacing: number
  end: QuickEnd
}

const KIND_LABEL: Record<DrawKind, string> = { wp: '웨이포인트 찍기', area: '영역 순찰' }
// 지도 위 색은 배경 지도 위에서 잘 보이도록 테마와 관계없이 고정
const DRAFT_COLOR = '#2563eb'
const AREA_COLOR = '#a855f7'
const TARGET_COLOR = '#16a34a'

const ll = (p: LatLon): L.LatLngTuple => [p.lat, p.lon]
const fromLL = (p: L.LatLng): LatLon => ({ lat: p.lat, lon: p.lng })
const validAlt = (a: number | null): a is number => a != null && a >= 2 && a <= MAX_ALT_M
const flying = (d: TaskDrone | undefined) => !!d && d.armed && (d.alt ?? 0) >= 1

const vertexIcon = () =>
  cachedIcon('vertex', () =>
    L.divIcon({ className: 'map-icon', html: '<div class="area-vertex"></div>', iconSize: [14, 14], iconAnchor: [7, 7] }),
  )

export default function MapTasks(p: Props) {
  const map = useMap()
  const [menu, setMenu] = useState<LatLon | null>(null)
  const [choice, setChoice] = useState<string | null>(null)
  const [altInput, setAltInput] = useState<number | null>(null)
  const [draw, setDraw] = useState<Draw | null>(null)

  const has = (id: string | null) => !!id && p.drones.some((d) => d.id === id)
  const pick = has(choice) ? choice : has(p.defaultDroneId) ? p.defaultDroneId : (p.drones[0]?.id ?? null)
  const drone = p.drones.find((d) => d.id === pick)
  const alt = altInput ?? (flying(drone) ? Math.min(Math.max(Math.round(drone!.alt!), 5), MAX_ALT_M) : 30)

  useMapEvents({
    contextmenu(e) {
      if (draw) return
      setMenu(fromLL(e.latlng))
      setAltInput(null)
    },
    click(e) {
      if (draw) setDraw({ ...draw, points: [...draw.points, fromLL(e.latlng)] })
    },
  })

  useEffect(() => {
    if (!draw) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setDraw(null)
    window.addEventListener('keydown', onKey)
    map.getContainer().classList.add('drawing')
    return () => {
      window.removeEventListener('keydown', onKey)
      map.getContainer().classList.remove('drawing')
    }
  }, [map, draw])

  const closeMenu = () => {
    setMenu(null)
    map.closePopup()
  }

  const goto = async () => {
    if (!menu || !pick) return
    const at = menu
    closeMenu()
    await p.onGoto(pick, at, alt)
  }

  const startDraw = (kind: DrawKind) => {
    if (!menu || !pick) return
    setDraw({ kind, droneId: pick, points: [menu], alt, spacing: defaultSpacing(alt), end: 'rtl' })
    closeMenu()
  }

  return (
    <>
      {menu && (
        <Popup
          position={ll(menu)}
          eventHandlers={{ remove: () => setMenu(null) }}
          minWidth={230}
          // 지도 위 버튼(왼쪽 아래 전체 보기 등, 오른쪽 위 장소 검색)에 가리지 않게 지도를 옮겨 띄운다
          autoPanPaddingTopLeft={[20, 70]}
          autoPanPaddingBottomRight={[20, 70]}
        >
          <div className="task-menu">
            <div className="muted small">
              {menu.lat.toFixed(6)}, {menu.lon.toFixed(6)}
            </div>
            {p.drones.length === 0 ? (
              <p className="small">FC가 연결된 드론이 없어요.</p>
            ) : (
              <>
                <div className="task-row">
                  <label>
                    드론{' '}
                    <select value={pick ?? ''} onChange={(e) => setChoice(e.target.value)}>
                      {p.drones.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.id} · {d.mode}
                          {d.armed ? '' : ' · 시동 꺼짐'}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    고도 <NumInput value={alt} digits={0} onCommit={(v) => setAltInput(v)} aria-label="고도 m" /> m
                  </label>
                </div>
                {!validAlt(alt) && <p className="small bad">고도는 2~{MAX_ALT_M} m로 정해 주세요.</p>}
                {drone?.pos && (
                  <p className="muted small">
                    {pick}에서 {formatDistance(distance(drone.pos, menu))}
                  </p>
                )}
                <button
                  className="primary"
                  disabled={!flying(drone) || p.busy || !validAlt(alt)}
                  title="GUIDED: 그 위치로 가서 멈춰요. 미션은 미션 시작·재개로 이어서 비행"
                  onClick={goto}
                >
                  이 위치로 보내기
                </button>
                {!flying(drone) && <p className="muted small">떠 있는 드론만 보낼 수 있어요 (시동·이륙은 조종기로).</p>}
                <div className="task-split" />
                <button disabled={!validAlt(alt)} onClick={() => startDraw('wp')}>
                  여기부터 웨이포인트 찍기
                </button>
                <button disabled={!validAlt(alt)} onClick={() => startDraw('area')}>
                  여기부터 영역 그리기 (순찰)
                </button>
              </>
            )}
          </div>
        </Popup>
      )}

      {draw && <DrawLayer draw={draw} drone={p.drones.find((d) => d.id === draw.droneId)} onChange={setDraw} />}
      {draw && (
        <DrawPanel
          draw={draw}
          drone={p.drones.find((d) => d.id === draw.droneId)}
          busy={p.busy}
          onChange={setDraw}
          onCancel={() => setDraw(null)}
          onRun={async (plan) => {
            if (await p.onRun(draw.droneId, plan)) setDraw(null)
          }}
        />
      )}

      {p.targets.map((t) => {
        const d = p.drones.find((x) => x.id === t.droneId)
        return (
          <Fragment key={`goto-${t.droneId}`}>
            {d?.pos && (
              <Polyline
                positions={[ll(d.pos), ll(t.at)]}
                pathOptions={{ color: TARGET_COLOR, weight: 2, dashArray: '6 6' }}
                interactive={false}
              />
            )}
            <CircleMarker center={ll(t.at)} radius={10} pathOptions={{ color: TARGET_COLOR, weight: 3, fillOpacity: 0.15 }}>
              <Tooltip direction="top" offset={[0, -10]}>
                {t.droneId} 목표 · {t.alt} m
              </Tooltip>
            </CircleMarker>
          </Fragment>
        )
      })}
    </>
  )
}

/** 그리는 중인 점·영역과 미리 보는 경로 */
function routeOf(draw: Draw, drone: TaskDrone | undefined): LatLon[] {
  return draw.kind === 'wp' ? draw.points : areaPoints(draw.points, draw.spacing, drone?.pos ?? null)
}

function DrawLayer({ draw, drone, onChange }: { draw: Draw; drone?: TaskDrone; onChange: (d: Draw) => void }) {
  const route = routeOf(draw, drone)
  const line = [...(drone?.pos ? [drone.pos] : []), ...route]
  const move = (i: number, at: LatLon) => onChange({ ...draw, points: draw.points.map((q, j) => (j === i ? at : q)) })
  const remove = (i: number) => onChange({ ...draw, points: draw.points.filter((_, j) => j !== i) })

  return (
    <>
      {draw.kind === 'area' && draw.points.length >= 2 && (
        <Polygon
          positions={draw.points.map(ll)}
          pathOptions={{ color: AREA_COLOR, weight: 2, dashArray: '6 4', fillOpacity: 0.08 }}
          interactive={false}
        />
      )}
      {line.length > 1 && (
        <Polyline positions={line.map(ll)} pathOptions={{ color: DRAFT_COLOR, weight: 3, opacity: 0.85 }} interactive={false} />
      )}
      {draw.points.map((pt, i) => (
        <Marker
          key={`draw-${i}`}
          position={ll(pt)}
          icon={draw.kind === 'wp' ? wpIcon(i + 1, 'wp', 'sel') : vertexIcon()}
          draggable
          zIndexOffset={1500}
          eventHandlers={{
            dragend: (e) => move(i, fromLL((e.target as L.Marker).getLatLng())),
            contextmenu: () => remove(i),
          }}
        >
          <Tooltip direction="top" offset={[0, -10]}>
            끌어서 이동 · 우클릭으로 삭제
          </Tooltip>
        </Marker>
      ))}
    </>
  )
}

function DrawPanel(p: {
  draw: Draw
  drone?: TaskDrone
  busy: boolean
  onChange: (d: Draw) => void
  onCancel: () => void
  onRun: (plan: MissionPlan) => void
}) {
  const map = useMap()
  const [box] = useState(() => {
    const el = L.DomUtil.create('div', 'leaflet-control task-panel')
    L.DomEvent.disableClickPropagation(el)
    L.DomEvent.disableScrollPropagation(el)
    return el
  })
  useEffect(() => {
    const ctl = new L.Control({ position: 'topleft' })
    ctl.onAdd = () => box
    ctl.addTo(map)
    return () => {
      ctl.remove()
    }
  }, [map, box])

  const { draw, drone } = p
  const route = routeOf(draw, drone)
  const length = routeLength(drone?.pos ?? null, route, draw.end === 'rtl' ? (drone?.home ?? null) : null)
  const enough = draw.kind === 'wp' ? draw.points.length >= 1 : draw.points.length >= 3 && route.length > 0
  const set = (patch: Partial<Draw>) => p.onChange({ ...draw, ...patch })

  const run = () => {
    if (!drone) return
    const name = `${KIND_LABEL[draw.kind]} ${timeLabel()}`
    const ask = drone.armed
      ? `${drone.id}의 미션을 이 경로(웨이포인트 ${route.length}개)로 바꾸고 바로 출발할까요?\n드론에 있던 미션은 지워져요.`
      : `${drone.id}에 이 경로(웨이포인트 ${route.length}개)를 미션으로 올릴까요?\n드론에 있던 미션은 지워져요.`
    if (window.confirm(ask)) p.onRun(quickPlan(name, route, draw.alt, draw.end))
  }

  return createPortal(
    <div>
      <div className="task-head">
        <strong>
          {KIND_LABEL[draw.kind]} · {draw.droneId}
        </strong>
      </div>
      <p className="muted small">
        {draw.kind === 'wp'
          ? '지도를 눌러 지나갈 점을 차례로 찍으세요.'
          : '지도를 눌러 영역의 꼭짓점을 찍으세요 (3개 이상). 안을 지그재그로 훑어요.'}{' '}
        점은 끌어서 옮기고 우클릭으로 지워요. Esc는 취소.
      </p>
      <div className="task-row">
        <label>
          고도 <NumInput value={draw.alt} digits={0} onCommit={(v) => validAlt(v) && set({ alt: v })} aria-label="고도 m" /> m
        </label>
        {draw.kind === 'area' && (
          <label title={`고도 ${draw.alt} m에서 카메라 폭 약 ${Math.round(cameraSwath(draw.alt))} m`}>
            줄 간격{' '}
            <NumInput value={draw.spacing} digits={0} onCommit={(v) => v && v >= 5 && set({ spacing: v })} aria-label="줄 간격 m" /> m
          </label>
        )}
      </div>
      <select value={draw.end} onChange={(e) => set({ end: e.target.value as QuickEnd })}>
        {(Object.keys(QUICK_END_LABEL) as QuickEnd[]).map((k) => (
          <option key={k} value={k}>
            {QUICK_END_LABEL[k]}
          </option>
        ))}
      </select>
      <p className="small">
        {draw.kind === 'area' ? `꼭짓점 ${draw.points.length}개 → ` : ''}웨이포인트 {route.length}개
        {length > 0 && ` · ${formatDistance(length)} · 약 ${formatDuration(length / DEFAULT_ASSUMPTIONS.speed)}`}
      </p>
      {!drone && <p className="small bad">{draw.droneId}의 연결이 끊겼어요.</p>}
      <div className="task-buttons">
        <button className="primary" disabled={!drone || !enough || p.busy} onClick={run}>
          {drone?.armed ? '출발' : '드론에 올리기'}
        </button>
        <button disabled={!draw.points.length} onClick={() => set({ points: draw.points.slice(0, -1) })}>
          마지막 점 지우기
        </button>
        <button onClick={p.onCancel}>취소</button>
      </div>
    </div>,
    box,
  )
}
