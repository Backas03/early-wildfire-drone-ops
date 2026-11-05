// 서버 간 프로토콜 타입 (docs/protocol.md, shared/protocol/*.py와 같아야 함)
// TODO: shared/schema/*.json 에서 자동 생성으로 바꾸기

// ---------------------------------------------------------------------------
// 미션 (shared/protocol/mission.py)
// ---------------------------------------------------------------------------
export type CommandType = 'takeoff' | 'waypoint' | 'loiter_time' | 'change_speed' | 'rtl' | 'land'
export type AltFrame = 'relative' | 'absolute' | 'terrain'

/** 국내 드론 비행 고도 제한 (m, 이륙 지점 기준) */
export const MAX_ALT_M = 150
export const MAX_SPEED_MS = 20

export interface MissionItem {
  seq: number
  command: CommandType
  lat: number | null
  lon: number | null
  alt: number | null
  frame: AltFrame
  hold_s: number
  speed: number | null
}

export interface PlanHome {
  lat: number
  lon: number
  alt_msl: number
}

export interface MissionPlan {
  mission_id: string | null
  name: string
  home: PlanHome | null
  items: MissionItem[]
}

/** GET /api/missions 목록 항목 */
export interface MissionSummary {
  mission_id: string
  name: string
  item_count: number
  waypoint_count: number
  created_at: number
  updated_at: number
}

// ---------------------------------------------------------------------------
// MQTT 메시지 (shared/protocol/messages.py)
// ---------------------------------------------------------------------------

export interface Position {
  lat: number
  lon: number
  alt_rel: number
  alt_msl: number
}

export interface Telemetry {
  v: number
  ts: number
  drone_id: string
  position: Position
  velocity: { ground_speed: number; climb: number; heading: number }
  battery: { voltage: number; current: number | null; remaining: number | null }
  gps: { fix_type: number; satellites: number; hdop: number | null }
  flight: { mode: string; armed: boolean }
  mission: { current_seq: number; total: number } | null
  link: { lte_rssi: number | null }
}

/** GPS가 잡히기 전에는 FC가 위치를 0,0으로 보내므로 지도에 그리면 안 된다 */
export function hasPosition(t: Telemetry): boolean {
  return t.gps.fix_type >= 2 && !(t.position.lat === 0 && t.position.lon === 0)
}

export interface DroneStatus {
  v: number
  ts: number
  drone_id: string
  online: boolean
  fc_connected: boolean
  home: Position | null
  agent_version: string | null
}

/** 대시보드가 바꿀 수 있는 비행 모드. 시동·이륙과 수동 모드는 일부러 없다 (안전 원칙) */
export type FlightMode = 'AUTO' | 'LOITER' | 'RTL' | 'LAND' | 'GUIDED' | 'BRAKE'

/** drone/{id}/cmd 명령 (cmd_id는 보낼 때 붙인다) */
export type Command =
  | { type: 'mission_upload'; mission: MissionPlan }
  | { type: 'mission_download' }
  | { type: 'mission_clear' }
  | { type: 'set_mode'; mode: FlightMode }

export interface CommandAck {
  v: number
  ts: number
  drone_id: string
  cmd_id: string
  status: 'accepted' | 'in_progress' | 'succeeded' | 'failed'
  progress: number | null
  error: string | null
  mission: MissionPlan | null
}

/** drone/{id}/mission (retained): 지금 FC에 들어 있는 미션 */
export interface DroneMission {
  v: number
  ts: number
  drone_id: string
  mission: MissionPlan | null
  error: string | null
}

/** drone/{id}/statustext: FC가 보낸 문장. severity 0 비상 … 4 경고 … 6 정보 */
export interface StatusText {
  v: number
  ts: number
  drone_id: string
  severity: number
  text: string
}

// ---------------------------------------------------------------------------
// 엣지 AI (드론의 라즈베리파이에서 불·연기 탐지)
// ---------------------------------------------------------------------------
export interface Detection {
  cls: 'fire' | 'smoke'
  conf: number
  /** 0~1로 정규화한 x1, y1, x2, y2 */
  bbox: [number, number, number, number]
}

/** inference/{id}/detections: 프레임마다 (못 찾아도 빈 목록) */
export interface DetectionFrame {
  v: number
  ts: number
  drone_id: string
  frame_ts: number
  stream: string
  width: number
  height: number
  model: string
  detections: Detection[]
  infer_ms: number | null
}

/** drone/{id}/edge (retained): 탐지 프로그램 상태 */
export interface EdgeStatus {
  v: number
  ts: number
  drone_id: string
  online: boolean
  model: string | null
  source: string | null
  imgsz: number | null
  target_hz: number | null
  fps: number | null
  infer_ms: number | null
  cpu_temp_c: number | null
  detail: string | null
}

/** drone/{id}/snapshot: 서버가 사진을 저장하고 jpeg 대신 url을 붙여 넘긴다 */
export interface SnapshotInfo {
  v: number
  ts: number
  drone_id: string
  snapshot_id: string
  frame_ts: number
  width: number
  height: number
  model: string
  detections: Detection[]
  url: string
  /** 서버 재확인 결과 (아직 안 했거나 재확인이 꺼져 있으면 없음) */
  check?: SnapshotCheck | null
}

/** drone/{id}/snapshot/check: 탐지 사진을 서버의 큰 모델(fire_s)로 다시 본 결과 */
export interface SnapshotCheck {
  v: number
  ts: number
  drone_id: string
  snapshot_id: string
  model: string
  /** confirmed: 불·연기 맞음, rejected: 오탐 의심 */
  verdict: 'confirmed' | 'rejected'
  threshold: number
  detections: Detection[]
  infer_ms: number
}

/** drone/{id}/edge/preview: 관제용 작은 미리보기 */
export interface CameraPreview {
  v: number
  ts: number
  drone_id: string
  frame_ts: number
  width: number
  height: number
  detections: Detection[]
  jpeg_b64: string
}

/** sim/fires (retained): 시뮬레이션 가상 화재의 정답 위치 */
export interface SimFires {
  v: number
  ts: number
  scenario: string
  fires: { fire_id: string; lat: number; lon: number; size_m: number; smoke: boolean }[]
}

export interface ServerStatus {
  v: number
  ts: number
  name: string
  online: boolean
  version: string | null
  detail: string | null
}

/** /ws 로 오는 메시지: MQTT 토픽 + 내용 */
export interface WsMessage {
  topic: string
  data: unknown
}
