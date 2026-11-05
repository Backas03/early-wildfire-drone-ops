// 화재 이벤트: 이벤트 서버가 판정한 "화재 의심" 목록과 지도 위 표시, 새 이벤트 알림.
// 상태는 사람이 바꾼다: 신규 → 확인함 → 종료, 또는 오탐.

import L from 'leaflet'
import { useEffect, useRef, useState } from 'react'
import { Circle, Marker, Tooltip } from 'react-leaflet'

import { EVENT_STATUS_LABEL, eventKinds as kinds, eventSummary as summary, isOpen } from '../lib/events.ts'
import { distance, formatDistance, type LatLon } from '../lib/geo.ts'
import { eventsApi } from '../mission/api.ts'
import { cachedIcon } from '../mission/mapIcons.ts'
import type { EventStatus, FireEvent } from '../protocol.ts'

const pct = (v: number) => `${Math.floor(v * 100)}%`
/** 20:17:18 */
const clock = (ms: number) => new Date(ms).toTimeString().slice(0, 8)

function ago(ms: number, now: number): string {
  const s = Math.max(0, Math.round((now - ms) / 1000))
  if (s < 60) return `${s}초 전`
  if (s < 3600) return `${Math.floor(s / 60)}분 전`
  return `${Math.floor(s / 3600)}시간 전`
}

/** 상태 바꾸기 (버튼 여러 개가 같이 쓴다) */
function useEventStatus() {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const set = async (e: FireEvent, status: EventStatus) => {
    setBusy(e.event_id)
    setError(null)
    try {
      await eventsApi.setStatus(e.event_id, status)
    } catch (err) {
      setError(`상태를 바꾸지 못했어요: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setBusy(null)
    }
  }
  return { set, busy, error }
}

// ---------------------------------------------------------------------------
// 왼쪽 패널
// ---------------------------------------------------------------------------
interface PanelProps {
  events: FireEvent[]
  now: number
  selectedId: string | null
  /** 고른 드론 위치 (거리 표시) */
  from: LatLon | null
  serverOnline: boolean
  onSelect: (e: FireEvent) => void
}

export default function EventsPanel(p: PanelProps) {
  const [showClosed, setShowClosed] = useState(false)
  const status = useEventStatus()
  const open = p.events.filter(isOpen)
  const closedCount = p.events.length - open.length
  const list = showClosed ? p.events : open

  return (
    <section className="panel events-panel">
      <div className="panel-head">
        <h3>
          화재 이벤트 {open.length > 0 && <span className="pill bad">{open.length}건 진행 중</span>}
        </h3>
        {closedCount > 0 && (
          <label className="check small">
            <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
            끝난 것 {closedCount}
          </label>
        )}
      </div>
      {!p.serverOnline && <p className="small bad-text">이벤트 서버가 꺼져 있어요. 판정·상태 변경이 안 돼요.</p>}
      {list.length === 0 ? (
        <p className="muted small">
          {p.events.length
            ? '진행 중인 이벤트가 없어요.'
            : '아직 없어요. 같은 곳에서 여러 번 잡히고 서버 재확인을 통과하면 여기에 떠요.'}
        </p>
      ) : (
        <ul className="event-list">
          {list.map((e) => (
            <li key={e.event_id} className={`event ${e.status}${e.event_id === p.selectedId ? ' sel' : ''}`}>
              <button className="event-main" onClick={() => p.onSelect(e)} title="지도에서 보기">
                {e.snapshot_url ? <img src={e.snapshot_url} alt="대표 탐지 사진" loading="lazy" /> : <span className="event-noimg" />}
                <span className="event-text">
                  <span>
                    <strong>{kinds(e)} 의심</strong> <span className={`event-status ${e.status}`}>{EVENT_STATUS_LABEL[e.status]}</span>
                  </span>
                  <span className="muted small">
                    {clock(e.first_seen)}부터 · 마지막 {ago(e.last_seen, p.now)} · {e.drone_id}
                  </span>
                  <span className="small">
                    최고 {pct(e.max_conf)} · 탐지 {e.detection_count}번
                    {e.check_confirmed + e.check_rejected > 0 && ` · 서버 확인 ${e.check_confirmed}/${e.check_confirmed + e.check_rejected}`}
                    {p.from && ` · ${formatDistance(distance(p.from, e))}`}
                  </span>
                </span>
              </button>
              <EventButtons e={e} status={status} />
            </li>
          ))}
        </ul>
      )}
      {status.error && <p className="notice err">{status.error}</p>}
    </section>
  )
}

function EventButtons({ e, status }: { e: FireEvent; status: ReturnType<typeof useEventStatus> }) {
  const busy = status.busy === e.event_id
  return (
    <div className="event-actions">
      {e.status === 'new' && (
        <button className="primary" disabled={busy} onClick={() => status.set(e, 'acknowledged')}>
          확인함
        </button>
      )}
      {e.status === 'acknowledged' && (
        <button disabled={busy} onClick={() => status.set(e, 'resolved')} title="불이 꺼졌거나 처리가 끝남">
          종료
        </button>
      )}
      {isOpen(e) && (
        <button disabled={busy} onClick={() => status.set(e, 'false_alarm')} title="불이 아님. 같은 곳의 탐지는 다시 알리지 않아요">
          오탐
        </button>
      )}
      {!isOpen(e) && (
        <button disabled={busy} onClick={() => status.set(e, 'acknowledged')}>
          다시 열기
        </button>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 지도 위 표시 (MapContainer 안)
// ---------------------------------------------------------------------------
const EVENT_COLOR: Record<EventStatus, string> = {
  new: '#dc2626',
  acknowledged: '#ea580c',
  resolved: '#64748b',
  false_alarm: '#64748b',
}

const eventIcon = (status: EventStatus, selected: boolean) =>
  cachedIcon(`event|${status}|${selected}`, () =>
    L.divIcon({
      className: 'map-icon',
      html: `<div class="event-pin ${status}${selected ? ' sel' : ''}">!</div>`,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    }),
  )

export function EventMarkers(p: { events: FireEvent[]; selectedId: string | null; onSelect: (e: FireEvent) => void }) {
  return (
    <>
      {p.events.map((e) => (
        <Circle
          key={`area-${e.event_id}`}
          center={[e.lat, e.lon]}
          radius={Math.max((e.radius_m ?? 0) * 2, 8)}
          pathOptions={{ color: EVENT_COLOR[e.status], weight: 2, dashArray: '4 4', fillOpacity: isOpen(e) ? 0.15 : 0.05 }}
          interactive={false}
        />
      ))}
      {p.events.map((e) => (
        <Marker
          key={`pin-${e.event_id}`}
          position={[e.lat, e.lon]}
          icon={eventIcon(e.status, e.event_id === p.selectedId)}
          zIndexOffset={isOpen(e) ? 2500 : 1000}
          eventHandlers={{ click: () => p.onSelect(e) }}
        >
          <Tooltip direction="top" offset={[0, -14]}>
            {summary(e)} · {EVENT_STATUS_LABEL[e.status]}
          </Tooltip>
        </Marker>
      ))}
    </>
  )
}

// ---------------------------------------------------------------------------
// 새 이벤트 알림 (지도 위쪽 띠)
// ---------------------------------------------------------------------------
export function EventAlert(p: { events: FireEvent[]; onShow: (e: FireEvent) => void }) {
  const status = useEventStatus()
  const fresh = p.events.filter((e) => e.status === 'new').sort((a, b) => b.first_seen - a.first_seen)
  useBeepOnNew(fresh.map((e) => e.event_id))
  if (!fresh.length) return null
  const e = fresh[0]
  return (
    <div className="event-alert" role="alert">
      <strong>{fresh.length > 1 ? `새 화재 의심 ${fresh.length}건` : '새 화재 의심'}</strong>
      <span>
        {e.drone_id} · {summary(e)} · {clock(e.first_seen)}
      </span>
      <button onClick={() => p.onShow(e)}>위치 보기</button>
      <button className="primary" disabled={status.busy === e.event_id} onClick={() => status.set(e, 'acknowledged')}>
        확인함
      </button>
      {status.error && <span className="small">{status.error}</span>}
    </div>
  )
}

/** 화면을 연 뒤 새로 생긴 이벤트마다 짧게 소리를 낸다.
 * 처음 몇 초 동안 받은 것(이미 있던 이벤트 목록)에는 내지 않는다. */
function useBeepOnNew(ids: string[]) {
  const seen = useRef(new Set<string>())
  const [openedAt] = useState(() => Date.now())
  const key = ids.join(',')
  useEffect(() => {
    const list = key ? key.split(',') : []
    const added = list.filter((id) => !seen.current.has(id))
    list.forEach((id) => seen.current.add(id))
    if (added.length && Date.now() - openedAt > 3000) beep()
  }, [key, openedAt])
}

function beep() {
  try {
    const ctx = new AudioContext()
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'square'
    osc.frequency.value = 880
    gain.gain.value = 0.05
    osc.connect(gain).connect(ctx.destination)
    osc.start()
    osc.frequency.setValueAtTime(660, ctx.currentTime + 0.18)
    osc.stop(ctx.currentTime + 0.4)
    osc.onended = () => void ctx.close()
  } catch {
    // 소리를 못 내도 화면 알림은 있다
  }
}
