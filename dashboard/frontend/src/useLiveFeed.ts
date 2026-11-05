import { useEffect, useState } from 'react'
import {
  hasPosition,
  type CameraPreview,
  type CommandAck,
  type DetectionFrame,
  type DroneMission,
  type DroneStatus,
  type EdgeStatus,
  type ServerStatus,
  type SimFires,
  type SnapshotCheck,
  type SnapshotInfo,
  type StatusText,
  type Telemetry,
  type WsMessage,
} from './protocol'

export type TrailPoint = [lat: number, lon: number]

export interface LiveState {
  connected: boolean
  telemetry: Record<string, Telemetry>
  drones: Record<string, DroneStatus>
  servers: Record<string, ServerStatus>
  /** 드론 FC에 들어 있는 미션 */
  missions: Record<string, DroneMission>
  /** 드론별 최근 FC 문장 (최신이 앞) */
  texts: Record<string, StatusText[]>
  /** 명령별 마지막 ack (진행률 표시용) */
  acks: Record<string, CommandAck>
  /** 드론별 비행 궤적 */
  trails: Record<string, TrailPoint[]>
  /** 엣지 AI: 상태, 마지막 탐지 프레임, 미리보기, 탐지 사진 (최신이 앞) */
  edge: Record<string, EdgeStatus>
  detections: Record<string, DetectionFrame>
  previews: Record<string, CameraPreview>
  snapshots: Record<string, SnapshotInfo[]>
  /** 사진보다 먼저 온 서버 재확인 결과 (snapshot_id별). 사진이 오면 붙이고 지운다 */
  earlyChecks: Record<string, SnapshotCheck>
  /** 시뮬레이션 가상 화재 정답 위치 */
  simFires: SimFires | null
}

const initial: LiveState = {
  connected: false,
  telemetry: {},
  drones: {},
  servers: {},
  missions: {},
  texts: {},
  acks: {},
  trails: {},
  edge: {},
  detections: {},
  previews: {},
  snapshots: {},
  earlyChecks: {},
  simFires: null,
}

const TEXTS_KEPT = 50
const SNAPSHOTS_KEPT = 20
const EARLY_CHECKS_KEPT = 50
const ACKS_KEPT = 30
const TRAIL_KEPT = 3000 // 2Hz로 25분
const TRAIL_MIN_STEP_DEG = 0.000005 // 약 0.5 m. 제자리에 있을 때 점이 쌓이지 않게

function appendTrail(trail: TrailPoint[] | undefined, t: Telemetry): TrailPoint[] {
  const p: TrailPoint = [t.position.lat, t.position.lon]
  const prev = trail ?? []
  const last = prev[prev.length - 1]
  if (last && Math.abs(last[0] - p[0]) < TRAIL_MIN_STEP_DEG && Math.abs(last[1] - p[1]) < TRAIL_MIN_STEP_DEG) return prev
  const next = prev.length >= TRAIL_KEPT ? prev.slice(prev.length - TRAIL_KEPT + 1) : prev.slice()
  next.push(p)
  return next
}

function reduce(s: LiveState, topic: string, data: unknown): LiveState {
  const [root, id, kind, sub] = topic.split('/')
  if (root === 'server' && kind === 'status') {
    const v = data as ServerStatus
    return { ...s, servers: { ...s.servers, [v.name]: v } }
  }
  if (root === 'inference' && kind === 'detections') {
    return { ...s, detections: { ...s.detections, [id]: data as DetectionFrame } }
  }
  if (root === 'sim' && id === 'fires') return { ...s, simFires: data as SimFires }
  if (root !== 'drone' || !id) return s
  switch (sub ? `${kind}/${sub}` : kind) {
    case 'telemetry': {
      const t = data as Telemetry
      // 시동이 걸린 동안만 궤적을 남기고, 새로 시동을 걸면 (= 새 비행) 처음부터 그린다
      const newFlight = t.flight.armed && s.telemetry[id] && !s.telemetry[id].flight.armed
      const track = t.flight.armed && hasPosition(t)
      return {
        ...s,
        telemetry: { ...s.telemetry, [id]: t },
        trails: track ? { ...s.trails, [id]: appendTrail(newFlight ? [] : s.trails[id], t) } : s.trails,
      }
    }
    case 'status':
      return { ...s, drones: { ...s.drones, [id]: data as DroneStatus } }
    case 'mission':
      return { ...s, missions: { ...s.missions, [id]: data as DroneMission } }
    case 'statustext': {
      const list = [data as StatusText, ...(s.texts[id] ?? [])].slice(0, TEXTS_KEPT)
      return { ...s, texts: { ...s.texts, [id]: list } }
    }
    case 'edge':
      return { ...s, edge: { ...s.edge, [id]: data as EdgeStatus } }
    case 'edge/preview':
      return { ...s, previews: { ...s.previews, [id]: data as CameraPreview } }
    case 'snapshot': {
      const early = s.earlyChecks[(data as SnapshotInfo).snapshot_id]
      const snap = { ...(data as SnapshotInfo), check: (data as SnapshotInfo).check ?? early ?? null }
      const rest = (s.snapshots[id] ?? []).filter((x) => x.snapshot_id !== snap.snapshot_id)
      const earlyChecks = early ? { ...s.earlyChecks } : s.earlyChecks
      if (early) delete earlyChecks[snap.snapshot_id]
      return { ...s, earlyChecks, snapshots: { ...s.snapshots, [id]: [snap, ...rest].slice(0, SNAPSHOTS_KEPT) } }
    }
    case 'snapshot/check': {
      const c = data as SnapshotCheck
      const list = s.snapshots[id] ?? []
      if (list.some((x) => x.snapshot_id === c.snapshot_id)) {
        const next = list.map((x) => (x.snapshot_id === c.snapshot_id ? { ...x, check: c } : x))
        return { ...s, snapshots: { ...s.snapshots, [id]: next } }
      }
      const earlyChecks = { ...s.earlyChecks, [c.snapshot_id]: c }
      const keys = Object.keys(earlyChecks)
      if (keys.length > EARLY_CHECKS_KEPT) delete earlyChecks[keys[0]]
      return { ...s, earlyChecks }
    }
    case 'cmd/ack': {
      const a = data as CommandAck
      const acks = { ...s.acks, [a.cmd_id]: a }
      const keys = Object.keys(acks)
      if (keys.length > ACKS_KEPT) delete acks[keys[0]]
      return { ...s, acks }
    }
  }
  return s
}

/** 대시보드 API의 /ws에 붙어서 드론·서버 최신 상태를 유지한다. 끊기면 2초 뒤 재접속. */
export function useLiveFeed(): [LiveState, (droneId: string) => void] {
  const [state, setState] = useState<LiveState>(initial)

  useEffect(() => {
    let ws: WebSocket | null = null
    let retry: number | undefined
    let closed = false

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws`)
      // 다시 붙으면 서버가 최근 FC 문장·탐지 사진을 처음부터 다시 보내므로 비운다
      ws.onopen = () => setState((s) => ({ ...s, connected: true, texts: {}, snapshots: {}, earlyChecks: {} }))
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }))
        if (!closed) retry = window.setTimeout(connect, 2000)
      }
      ws.onmessage = (ev) => {
        const { topic, data } = JSON.parse(ev.data) as WsMessage
        setState((s) => reduce(s, topic, data))
      }
    }

    connect()
    return () => {
      closed = true
      window.clearTimeout(retry)
      ws?.close()
    }
  }, [])

  const clearTrail = (droneId: string) => setState((s) => ({ ...s, trails: { ...s.trails, [droneId]: [] } }))
  return [state, clearTrail]
}
