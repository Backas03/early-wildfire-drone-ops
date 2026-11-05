"""MQTT 메시지 스키마 (프로토콜 v1 초안).

공통 규칙
- 인코딩: UTF-8 JSON, 필드 이름은 snake_case
- ts: 메시지를 만든 시각, Unix epoch 밀리초(UTC)
- 단위: 거리·고도 m, 속도 m/s, 각도 deg, 전압 V, 전류 A
- v: 프로토콜 버전. 호환되지 않게 바뀌면 올린다.
"""

import time
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from .mission import MAX_ALT_M, MissionPlan

PROTOCOL_VERSION = 1


def now_ms() -> int:
    return int(time.time() * 1000)


class _Msg(BaseModel):
    v: int = PROTOCOL_VERSION
    ts: int = Field(default_factory=now_ms)


# ---------------------------------------------------------------------------
# drone/{id}/telemetry  (드론 → 서버, QoS 0, 2~5Hz)
# ---------------------------------------------------------------------------
class Position(BaseModel):
    lat: float
    lon: float
    alt_rel: float = Field(description="홈(이륙 지점) 기준 고도 m")
    alt_msl: float = Field(description="해발 고도 m")


class Velocity(BaseModel):
    ground_speed: float
    climb: float
    heading: float = Field(ge=0, lt=360)


class BatteryInfo(BaseModel):
    voltage: float
    current: float | None = None
    remaining: int | None = Field(default=None, ge=0, le=100, description="% (FC 추정값)")


class GpsInfo(BaseModel):
    fix_type: int = Field(description="0-1: 없음, 2: 2D, 3: 3D, 4+: DGPS/RTK")
    satellites: int
    hdop: float | None = None


class FlightInfo(BaseModel):
    mode: str = Field(description="ArduCopter 모드 이름 (STABILIZE, LOITER, AUTO, RTL ...)")
    armed: bool


class MissionProgress(BaseModel):
    current_seq: int = Field(description="MissionPlan 기준 seq (MAVLink seq - 1)")
    total: int


class LinkInfo(BaseModel):
    lte_rssi: int | None = Field(default=None, description="dBm, LTE 모듈에서 읽음")


class Telemetry(_Msg):
    drone_id: str
    position: Position
    velocity: Velocity
    battery: BatteryInfo
    gps: GpsInfo
    flight: FlightInfo
    mission: MissionProgress | None = None
    link: LinkInfo = Field(default_factory=LinkInfo)


# ---------------------------------------------------------------------------
# drone/{id}/status  (드론 → 서버, QoS 1, retained, LWT)
# agent 연결 시 online=true를 발행하고, 끊기면 브로커가 LWT로 online=false를 발행한다.
# ---------------------------------------------------------------------------
class DroneStatus(_Msg):
    drone_id: str
    online: bool
    fc_connected: bool = False
    home: Position | None = None
    agent_version: str | None = None


# ---------------------------------------------------------------------------
# drone/{id}/cmd  (서버 → 드론, QoS 1)
# 안전상 시동(arm)·이륙 명령은 프로토콜에 넣지 않는다. 시동은 조종기로 한다.
# ---------------------------------------------------------------------------
class _Cmd(_Msg):
    cmd_id: str = Field(min_length=1, description="UUID. ack와 짝을 맞출 때 사용")


class MissionUploadCmd(_Cmd):
    type: Literal["mission_upload"] = "mission_upload"
    mission: MissionPlan


class MissionDownloadCmd(_Cmd):
    type: Literal["mission_download"] = "mission_download"


class MissionClearCmd(_Cmd):
    type: Literal["mission_clear"] = "mission_clear"


FlightMode = Literal["AUTO", "LOITER", "RTL", "LAND", "GUIDED", "BRAKE"]


class SetModeCmd(_Cmd):
    type: Literal["set_mode"] = "set_mode"
    mode: FlightMode


class GotoCmd(_Cmd):
    """이 위치로 가서 멈춘다 (GUIDED). 떠 있을 때만 받는다. 미션은 미션 시작·재개(AUTO)로 이어서 비행"""

    type: Literal["goto"] = "goto"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = Field(gt=0, le=MAX_ALT_M, description="홈(이륙 지점) 기준 고도 m")


Command = Annotated[
    MissionUploadCmd | MissionDownloadCmd | MissionClearCmd | SetModeCmd | GotoCmd,
    Field(discriminator="type"),
]
CommandAdapter: TypeAdapter[Command] = TypeAdapter(Command)


# ---------------------------------------------------------------------------
# drone/{id}/cmd/ack  (드론 → 서버, QoS 1)
# 한 명령에 여러 번 올 수 있다: accepted → in_progress(진행률) → succeeded | failed
# ---------------------------------------------------------------------------
class CommandAck(_Msg):
    drone_id: str
    cmd_id: str
    status: Literal["accepted", "in_progress", "succeeded", "failed"]
    progress: float | None = Field(default=None, ge=0, le=1)
    error: str | None = None
    mission: MissionPlan | None = Field(default=None, description="mission_download 결과")


# ---------------------------------------------------------------------------
# drone/{id}/mission  (드론 → 서버, QoS 1, retained)
# 지금 FC에 들어 있는 미션. agent가 FC에 연결될 때, 업로드·삭제 후,
# 다른 GCS(Mission Planner 등)가 미션을 바꾼 것을 알아챘을 때 발행한다.
# ---------------------------------------------------------------------------
class DroneMission(_Msg):
    drone_id: str
    mission: MissionPlan | None = Field(default=None, description="None = 미션 없음 또는 읽지 못함")
    error: str | None = Field(default=None, description="읽지 못한 이유 (지원하지 않는 항목 등)")


# ---------------------------------------------------------------------------
# drone/{id}/statustext  (드론 → 서버, QoS 1)
# FC가 보내는 문장(STATUSTEXT) 그대로. 예: "PreArm: GPS not healthy", "Reached command #3"
# ---------------------------------------------------------------------------
class StatusText(_Msg):
    drone_id: str
    severity: int = Field(ge=0, le=7, description="0 비상 · 2 위험 · 3 오류 · 4 경고 · 5 알림 · 6 정보 · 7 디버그")
    text: str


# ---------------------------------------------------------------------------
# inference/{id}/detections  (드론의 엣지 AI → 서버, QoS 0)
# 추론한 모든 프레임마다 발행한다. 탐지가 없어도 빈 리스트로 발행해야
# 이벤트 서버가 "N초 지속" 여부를 판단할 수 있다.
# ---------------------------------------------------------------------------
class Detection(BaseModel):
    cls: Literal["fire", "smoke"]
    conf: float = Field(ge=0, le=1)
    bbox: tuple[float, float, float, float] = Field(description="x1,y1,x2,y2 (0~1로 정규화)")


class DetectionFrame(_Msg):
    drone_id: str
    frame_ts: int = Field(description="프레임 촬영 시각 ms. 드론 위치와 맞출 때 사용")
    stream: str = Field(description="영상 이름 (MediaMTX 경로). 예: drone1")
    width: int
    height: int
    model: str = Field(description="모델 이름. 예: fire_n")
    detections: list[Detection]
    infer_ms: float | None = Field(default=None, description="이 프레임 추론에 걸린 시간")


# ---------------------------------------------------------------------------
# drone/{id}/edge  (엣지 AI → 전체, QoS 1, retained, LWT)
# 드론에서 도는 탐지 프로그램의 상태. 5초마다 + 시작·종료 시.
# ---------------------------------------------------------------------------
class EdgeStatus(_Msg):
    drone_id: str
    online: bool
    model: str | None = None
    source: str | None = Field(default=None, description="picamera | sim | video | cam")
    imgsz: int | None = Field(default=None, description="모델 입력 크기(px)")
    target_hz: float | None = Field(default=None, description="목표 추론 횟수/초")
    fps: float | None = Field(default=None, description="실제 추론 횟수/초 (최근 5초)")
    infer_ms: float | None = Field(default=None, description="평균 추론 시간")
    cpu_temp_c: float | None = Field(default=None, description="라즈베리파이 CPU 온도")
    detail: str | None = Field(default=None, description="오류 등 사람이 읽는 문장")


# ---------------------------------------------------------------------------
# drone/{id}/snapshot  (엣지 AI → 서버, QoS 1)
# 불·연기를 찾은 프레임의 원본 사진. 서버가 저장하고 큰 모델로 다시 확인한다.
# 같은 탐지가 계속되면 몇 초에 한 장만 보낸다.
# ---------------------------------------------------------------------------
class Snapshot(_Msg):
    drone_id: str
    snapshot_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    frame_ts: int
    width: int
    height: int
    model: str
    detections: list[Detection] = Field(description="엣지가 찾은 것 (bbox는 이 사진 기준)")
    jpeg_b64: str = Field(description="JPEG 원본 (base64). 박스는 그리지 않는다")


# ---------------------------------------------------------------------------
# drone/{id}/snapshot/check  (서버 재확인 → 대시보드·이벤트 판정, QoS 1)
# 탐지 사진을 서버의 큰 모델(fire_s)로 다시 본 결과. 엣지(fire_n)의 오탐을 거른다.
# ---------------------------------------------------------------------------
class SnapshotCheck(_Msg):
    drone_id: str
    snapshot_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$", description="다시 본 Snapshot")
    model: str = Field(description="재확인에 쓴 모델 (예: fire_s)")
    verdict: Literal["confirmed", "rejected"] = Field(description="confirmed: 불·연기 맞음, rejected: 오탐 의심")
    threshold: float = Field(description="이 신뢰도 이상인 불·연기가 있으면 confirmed")
    detections: list[Detection] = Field(description="큰 모델이 찾은 것 (bbox는 사진 기준)")
    infer_ms: float


# ---------------------------------------------------------------------------
# drone/{id}/edge/preview  (엣지 AI → 대시보드, QoS 0)
# 관제용 작은 미리보기 (기본 1초에 한 장). 실시간 영상(MediaMTX)이 생기기 전까지 쓰고,
# 그 뒤에도 LTE가 약할 때 대신 쓸 수 있다.
# ---------------------------------------------------------------------------
class CameraPreview(_Msg):
    drone_id: str
    frame_ts: int
    width: int
    height: int
    detections: list[Detection]
    jpeg_b64: str


# ---------------------------------------------------------------------------
# sim/fires  (가상 카메라 → 대시보드, QoS 1, retained) — 시뮬레이션 전용
# 가상 화재의 정답 위치. 탐지 위치가 얼마나 맞는지 비교할 때 쓴다.
# ---------------------------------------------------------------------------
class SimFire(BaseModel):
    fire_id: str
    lat: float
    lon: float
    size_m: float = Field(gt=0, description="불이 난 영역 지름")
    smoke: bool = True


class SimFires(_Msg):
    scenario: str
    fires: list[SimFire]


# ---------------------------------------------------------------------------
# events  (이벤트 서버 → 대시보드, QoS 1)
# ---------------------------------------------------------------------------
class FireEvent(BaseModel):
    event_id: str
    drone_id: str
    status: Literal["new", "acknowledged", "resolved", "false_alarm"]
    classes: list[Literal["fire", "smoke"]]
    max_conf: float = Field(ge=0, le=1)
    first_seen: int
    last_seen: int
    lat: float = Field(description="탐지 당시 드론 위치 (화재 위치 추정값)")
    lon: float
    snapshot_url: str | None = None
    detection_count: int = 0


class EventMessage(_Msg):
    action: Literal["created", "updated"]
    event: FireEvent


# ---------------------------------------------------------------------------
# server/{name}/status  (각 서버 → 전체, QoS 1, retained, LWT)
# ---------------------------------------------------------------------------
class ServerStatus(_Msg):
    name: Literal["dashboard-api", "event-server", "inference", "streaming"]
    online: bool
    version: str | None = None
    detail: str | None = None
