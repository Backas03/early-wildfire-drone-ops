# 서버 간 통신 프로토콜 v1 (초안)

> 상태: **초안 (2단계, SITL로 확인)**. 구현하면서 바뀔 수 있음. 바꿀 때는 `shared/protocol/` 코드와 이 문서를 같이 고친다.
> 코드가 정답이다: 메시지 형식은 [`shared/protocol/messages.py`](../shared/protocol/messages.py), 미션 형식은 [`shared/protocol/mission.py`](../shared/protocol/mission.py).

## 1. 개요

모든 서버와 드론은 **MQTT 브로커(Mosquitto) 하나**를 통해 메시지를 주고받는다. 영상만 예외로 MediaMTX(RTSP/SRT/WebRTC)를 쓴다.

```
드론 (RPi 5)
 ├─ drone-agent ── MQTT ──▶ 텔레메트리·상태 발행 / 명령 구독
 ├─ edge-ai     ── MQTT ──▶ 카메라 → YOLOv8(fire_n) → 탐지 결과·탐지 사진·미리보기 발행  (엣지 AI)
 └─ 카메라 ── RTSP/SRT ──▶ MediaMTX (관제용 영상, 3단계)

서버 PC
 ├─ Mosquitto     : MQTT 브로커 (localhost:1883, 드론은 Tailscale로 접속)
 ├─ MediaMTX      : 영상 중계
 ├─ 대시보드 API   : MQTT ⇄ 브라우저(WebSocket), 명령 발행, 미션 저장, 탐지 사진 저장(media/)
 ├─ 서버 재확인    : 탐지 사진을 큰 모델(fire_s)로 다시 확인해 오탐을 거름 (예정)
 └─ 이벤트·로깅    : 탐지 + 텔레메트리 구독 → 화재 판정 → DB → events 발행
```

**하이브리드 구조 (2026-11-02 결정):** 탐지는 드론의 라즈베리파이가 하고(엣지 AI), 서버는 엣지가 보낸 사진을 큰 모델로 한 번 더 확인한다. LTE가 약해도 드론이 스스로 판단하고, 보내는 데이터는 탐지 결과와 사진 몇 장으로 줄어든다. 계획서(10월)의 "서버에서 YOLOv8 추론"에서 바뀐 부분이다.

## 2. 공통 규칙

| 항목 | 규칙 |
|---|---|
| 인코딩 | UTF-8 JSON, 필드 이름 snake_case |
| `v` | 프로토콜 버전 (현재 `1`). 호환되지 않게 바뀌면 올린다 |
| `ts` | 메시지 생성 시각, **Unix epoch 밀리초(UTC)** |
| 단위 | 거리·고도 m, 속도 m/s, 각도 deg(0~360, 북쪽 0), 전압 V, 전류 A |
| `drone_id` | 영문·숫자 (예: `drone1`). 토픽과 메시지 안의 값이 같아야 함 |
| 시각 동기화 | 드론(RPi)과 서버 모두 NTP 사용. 탐지와 드론 위치를 시각으로 맞추기 때문 |

## 3. 토픽 목록

| 토픽 | 보내는 쪽 → 받는 쪽 | QoS | retain | 메시지 | 주기 |
|---|---|---|---|---|---|
| `drone/{id}/telemetry` | drone-agent → API, 이벤트 서버 | 0 | X | `Telemetry` | 2Hz (LTE 상태에 따라 1~5Hz) |
| `drone/{id}/status` | drone-agent → 전체 | 1 | O | `DroneStatus` | 연결·변경 시 + LWT |
| `drone/{id}/cmd` | API → drone-agent | 1 | X | `Command` | 사용자 조작 시 |
| `drone/{id}/cmd/ack` | drone-agent → API | 1 | X | `CommandAck` | 명령 처리 중 |
| `drone/{id}/mission` | drone-agent → API | 1 | O | `DroneMission` | FC 연결 시, 미션이 바뀔 때 |
| `drone/{id}/statustext` | drone-agent → API | 1 | X | `StatusText` | FC가 문장을 보낼 때 |
| `inference/{id}/detections` | edge-ai → API, 이벤트 서버 | 0 | X | `DetectionFrame` | 추론한 프레임마다 (2~5Hz) |
| `drone/{id}/edge` | edge-ai → 전체 | 1 | O | `EdgeStatus` | 5초마다 + LWT |
| `drone/{id}/snapshot` | edge-ai → API(저장), 서버 재확인 | 1 | X | `Snapshot` | 불·연기를 찾았을 때 (몇 초에 한 장) |
| `drone/{id}/edge/preview` | edge-ai → API | 0 | X | `CameraPreview` | 1Hz (설정) |
| `sim/fires` | 가상 카메라 → API | 1 | O | `SimFires` | 시뮬레이션 시작 시 (시뮬레이션 전용) |
| `events` | 이벤트 서버 → API | 1 | X | `EventMessage` | 이벤트 생성·변경 시 |
| `server/{name}/status` | 각 서버 → API | 1 | O | `ServerStatus` | 시작·종료 시 + LWT |

- **LWT (Last Will)**: 연결할 때 "내가 끊기면 이걸 대신 발행해 줘"라고 브로커에 맡겨 두는 메시지. drone-agent와 각 서버는 `online: false` 메시지를 LWT로 등록한다. 그래서 LTE가 끊기면 대시보드에 바로 "오프라인"이 뜬다.
- **retain**: 브로커가 마지막 메시지를 기억했다가 새 구독자에게 바로 준다. 대시보드를 새로 열어도 드론 상태를 즉시 알 수 있다.

## 4. 메시지

### 4.1 `Telemetry` — `drone/{id}/telemetry`

```json
{
  "v": 1, "ts": 1761552000123, "drone_id": "drone1",
  "position": { "lat": 35.8964, "lon": 128.8495, "alt_rel": 50.2, "alt_msl": 150.2 },
  "velocity": { "ground_speed": 5.1, "climb": 0.0, "heading": 270.0 },
  "battery":  { "voltage": 11.6, "current": 12.3, "remaining": 72 },
  "gps":      { "fix_type": 3, "satellites": 12, "hdop": 0.8 },
  "flight":   { "mode": "AUTO", "armed": true },
  "mission":  { "current_seq": 3, "total": 10 },
  "link":     { "lte_rssi": -71 }
}
```

- `alt_rel`: 홈(이륙 지점) 기준 고도. `alt_msl`: 해발 고도.
- `mission.current_seq`: **MissionPlan 기준** 번호 (MAVLink 번호 - 1). `mission.total`: 미션 항목 수 (= `MissionPlan.items` 길이, 홈 제외). 미션이 없으면 `mission: null`.
- `gps.fix_type`: 0~1 없음, 2 = 2D, 3 = 3D, 4 이상 DGPS/RTK.
- **GPS가 잡히기 전(`fix_type` < 2)에는 `position`이 0, 0으로 온다.** 받는 쪽은 이때 위치를 쓰지 않는다 (지도에 그리지 않기, 탐지 위치로 쓰지 않기).

### 4.2 `DroneStatus` — `drone/{id}/status` (retain, LWT)

```json
{ "v": 1, "ts": 1761552000000, "drone_id": "drone1", "online": true,
  "fc_connected": true, "agent_version": "0.1.0",
  "home": { "lat": 35.8964, "lon": 128.8495, "alt_rel": 0, "alt_msl": 100.0 } }
```

LWT로 등록하는 메시지는 `"online": false`, `"fc_connected": false`.

### 4.3 `Command` — `drone/{id}/cmd`

모든 명령에 `cmd_id`(UUID)가 있고, `type`으로 종류를 구분한다.

| type | 추가 필드 | 하는 일 |
|---|---|---|
| `mission_upload` | `mission: MissionPlan` | 드론에 미션 업로드 (기존 미션 덮어씀) |
| `mission_download` | - | 드론에 저장된 미션을 읽어 ack의 `mission`으로 돌려줌 |
| `mission_clear` | - | 드론 미션 삭제 |
| `set_mode` | `mode`: `AUTO` `LOITER` `RTL` `LAND` `GUIDED` `BRAKE` | 비행 모드 변경. `AUTO` = 미션 시작/재개 |

```json
{ "v": 1, "ts": 1761552000000, "cmd_id": "6f1c...", "type": "set_mode", "mode": "RTL" }
```

> **안전 원칙:** 시동(arm)·이륙·시동 끄기(disarm) 명령은 프로토콜에 **일부러 넣지 않았다**. 시동은 조종기로 하고, 대시보드는 미션 업로드와 모드 변경만 한다. `STABILIZE` 같은 수동 모드도 원격으로 바꾸지 않는다.

- 브라우저는 `POST /api/drones/{id}/commands`로 명령을 보낸다 (본문 = 이 메시지, `cmd_id`는 브라우저가 정함). API가 검증해서 MQTT로 발행하고, 마지막 ack(`succeeded`/`failed`)를 HTTP 응답으로 돌려준다. 진행률은 `/ws`로 오는 ack로 따라간다.
- drone-agent가 거부하는 경우: FC 연결 안 됨, **시동이 걸린 채 AUTO로 비행 중일 때 `mission_upload`·`mission_clear`** (비행 중인 미션이 바뀌면 위험), 드론에 미션이 없는데 `AUTO`.
- **일시 정지에는 `BRAKE`를 쓴다.** `LOITER`는 조종기 스로틀을 따르므로 스틱이 내려가 있으면 그대로 하강한다 (SITL에서 24 m → 착륙 확인). `BRAKE`는 스틱과 관계없이 위치·고도를 유지하고, `AUTO`로 멈춘 곳부터 이어서 비행한다.

### 4.4 `CommandAck` — `drone/{id}/cmd/ack`

한 명령에 여러 번 올 수 있다: `accepted` → `in_progress`(진행률) → `succeeded` 또는 `failed`.

```json
{ "v": 1, "ts": 1761552000500, "drone_id": "drone1", "cmd_id": "6f1c...",
  "status": "in_progress", "progress": 0.4, "error": null, "mission": null }
```

- drone-agent는 명령을 받자마자 `accepted`를 보낸다. 미션 업로드·다운로드는 10%마다 `in_progress`(`progress` 0~1)를 보낸다.
- API는 명령을 보내고 **10초** 안에 `accepted`가 안 오면 실패(드론 응답 없음, HTTP 504)로 처리한다. accepted 뒤에는 명령별 제한 시간(업로드·다운로드 90초, 나머지 20초)을 둔다.
- `mission_download`의 결과는 `succeeded` ack의 `mission` 필드에 담긴다.
- `failed`의 `error`는 사람이 읽는 문장이다. 모드 변경이 거부되면 FC가 보낸 이유(STATUSTEXT)를 덧붙인다.

### 4.5 `DetectionFrame` — `inference/{id}/detections`

```json
{ "v": 1, "ts": 1761552000700, "drone_id": "drone1", "frame_ts": 1761552000400,
  "stream": "drone1", "width": 1280, "height": 720, "model": "fire_n",
  "detections": [ { "cls": "fire", "conf": 0.82, "bbox": [0.41, 0.33, 0.52, 0.47] } ],
  "infer_ms": 34.5 }
```

- 드론의 edge-ai가 발행한다 (엣지 AI). 모델·속도는 [`models/README.md`](../models/README.md).
- **탐지가 없어도 빈 리스트로 발행한다.** 이벤트 서버가 "N초 동안 계속 보였는지"를 판단하려면 안 보인 프레임도 알아야 한다.
- `bbox`는 0~1로 정규화한 `[x1, y1, x2, y2]`. `detections`는 신뢰도 높은 순.
- `frame_ts`는 프레임을 찍은 시각. 이벤트 서버는 이 시각과 가장 가까운 텔레메트리로 위치를 정한다.

### 4.6 `EventMessage` — `events`

```json
{ "v": 1, "ts": 1761552003000, "action": "created",
  "event": { "event_id": "evt_...", "drone_id": "drone1", "status": "new",
             "classes": ["fire", "smoke"], "max_conf": 0.86,
             "first_seen": 1761552000400, "last_seen": 1761552003000,
             "lat": 35.8971, "lon": 128.8502,
             "snapshot_url": "/media/snapshots/evt_....jpg", "detection_count": 9 } }
```

- `status`: `new`(신규) → `acknowledged`(확인) → `resolved`(종료) 또는 `false_alarm`(오탐).
- `lat/lon`은 탐지 당시 **드론 위치**다 (화재 지점 자체의 추정은 이후 단계에서).

### 4.7 `ServerStatus` — `server/{name}/status` (retain, LWT)

`name`: `dashboard-api` `event-server` `inference` `streaming`

```json
{ "v": 1, "ts": 1761552000000, "name": "event-server", "online": true, "version": "0.1.0", "detail": null }
```

### 4.8 `DroneMission` — `drone/{id}/mission` (retain)

지금 **FC에 들어 있는** 미션. 대시보드가 비행 중인 경로를 그리고, 편집 중인 미션과 같은지 비교할 때 쓴다.

```json
{ "v": 1, "ts": 1761552000000, "drone_id": "drone1",
  "mission": { "mission_id": "a1b2...", "name": "뒷산 순찰 A", "home": {...}, "items": [...] },
  "error": null }
```

- 발행 시점: agent가 FC에 연결될 때(FC에서 읽어서), 업로드·삭제·다운로드 후, 다른 GCS(Mission Planner 등)가 미션을 바꾼 것을 알아챘을 때 (`MISSION_CURRENT`의 항목 수·mission_id 변화).
- FC는 미션 이름을 저장하지 않는다. agent는 마지막으로 올린 미션을 기억해 두고(`drone-agent/state/`), FC 미션이 그것과 같으면 이름과 `mission_id`를 붙인다. 다르면 이름은 `"{id}에서 받은 미션"`, `mission_id`는 `null`.
- `mission: null` + `error: null` = 미션 없음. `error`가 있으면 읽지 못한 것 (예: 우리 모델에 없는 카메라 트리거 명령이 들어 있음).

### 4.9 `StatusText` — `drone/{id}/statustext`

FC가 보내는 문장(MAVLink `STATUSTEXT`) 그대로. 시동 거부 이유(`PreArm: ...`), 미션 진행(`Mission: 3 WP`, `Reached command #3`), 경고 등.

```json
{ "v": 1, "ts": 1761552000000, "drone_id": "drone1", "severity": 4, "text": "PreArm: GPS not healthy" }
```

- `severity`: 0 비상 · 1 경보 · 2 위험 · 3 오류 · 4 경고 · 5 알림 · 6 정보 · 7 디버그.
- 대시보드 API는 드론마다 최근 30개를 기억했다가 새로 접속한 브라우저에 먼저 보낸다.

### 4.10 `EdgeStatus` — `drone/{id}/edge` (retain, LWT)

드론에서 도는 탐지 프로그램(edge-ai) 상태. 5초마다 보내고, LWT는 `online: false`.

```json
{ "v": 1, "ts": 1761552000000, "drone_id": "drone1", "online": true, "model": "fire_n",
  "source": "picamera", "imgsz": 640, "target_hz": 3.0, "fps": 2.98, "infer_ms": 96.4,
  "cpu_temp_c": 61.2, "detail": null }
```

- `source`: `picamera`(카메라 모듈 3) · `sim`(가상 카메라) · `video`(영상 파일) · `cam`(웹캠).
- `fps`·`infer_ms`는 최근 5초 평균. `cpu_temp_c`는 라즈베리파이에서만 (발열 확인용).
- `detail`: 문제가 있을 때 사람이 읽는 문장 (예: "카메라 기다리는 중").

### 4.11 `Snapshot` — `drone/{id}/snapshot`

불·연기를 찾은 프레임의 **원본** 사진 (박스를 그리지 않음). 신뢰도 0.4 이상을 처음 찾으면 바로 한 장, 계속 보이면 5초마다 한 장 (edge-ai 옵션으로 조정).

```json
{ "v": 1, "ts": 1761552000900, "drone_id": "drone1", "snapshot_id": "drone1-1761552000400-ab12cd",
  "frame_ts": 1761552000400, "width": 1280, "height": 720, "model": "fire_n",
  "detections": [ { "cls": "smoke", "conf": 0.74, "bbox": [0.45, 0.02, 0.98, 0.56] } ],
  "jpeg_b64": "/9j/4AAQ..." }
```

- 대시보드 API가 받아서 `media/snapshots/{snapshot_id}.jpg`로 저장하고, 브라우저에는 `jpeg_b64` 대신 `"url": "/media/snapshots/....jpg"`를 붙여 넘긴다.
- `snapshot_id`는 영문·숫자·`-`·`_`만 (파일 이름으로 쓰므로). 사진은 5 MB 이하 JPEG.
- 서버 재확인(예정)은 이 사진을 큰 모델(fire_s)로 다시 본다.

### 4.12 `CameraPreview` — `drone/{id}/edge/preview`

관제용 작은 미리보기 (기본 폭 480 px, 1초에 한 장). `Snapshot`과 같은 모양이지만 `snapshot_id`·`model`이 없고 저장하지 않는다. 실시간 영상(MediaMTX)이 생긴 뒤에도 LTE가 약할 때 대신 쓸 수 있다.

### 4.13 `SimFires` — `sim/fires` (retain, 시뮬레이션 전용)

가상 카메라가 그리는 가상 화재의 정답 위치. 대시보드 지도에 표시하고, 탐지 위치가 얼마나 맞는지 잴 때 쓴다.

```json
{ "v": 1, "ts": 1761552000000, "scenario": "대구대 캠퍼스 남쪽 하천 풀밭",
  "fires": [ { "fire_id": "A", "lat": 35.89632, "lon": 128.8504, "size_m": 6, "smoke": true } ] }
```

## 5. 미션 (`MissionPlan`)

```json
{ "mission_id": null, "name": "뒷산 순찰 A",
  "home": { "lat": 35.8964, "lon": 128.8495, "alt_msl": 100 },
  "items": [
    { "seq": 0, "command": "takeoff",  "alt": 30, "frame": "relative" },
    { "seq": 1, "command": "waypoint", "lat": 35.8970, "lon": 128.8500, "alt": 50, "hold_s": 0 },
    { "seq": 2, "command": "change_speed", "speed": 5 },
    { "seq": 3, "command": "loiter_time", "lat": 35.8980, "lon": 128.8510, "alt": 50, "hold_s": 10 },
    { "seq": 4, "command": "rtl" } ] }
```

| command | 필수 필드 | MAV_CMD | MAVLink 파라미터 |
|---|---|---|---|
| `takeoff` | alt | 22 NAV_TAKEOFF | alt |
| `waypoint` | lat, lon, alt | 16 NAV_WAYPOINT | param1 = hold_s |
| `loiter_time` | lat, lon, alt | 19 NAV_LOITER_TIME | param1 = hold_s |
| `change_speed` | speed | 178 DO_CHANGE_SPEED | param1 = 1(대지속도), param2 = speed |
| `rtl` | - | 20 NAV_RETURN_TO_LAUNCH | - |
| `land` | (lat, lon) | 21 NAV_LAND | 위치 없으면 현재 위치에 착륙 |

| frame (고도 기준) | MAV_FRAME 번호 | 설명 |
|---|---|---|
| `relative` (기본) | 3 | 이륙 지점 기준 |
| `absolute` | 0 | 해발 고도 |
| `terrain` | 10 | 지형 기준 (지형 데이터 필요, 이후 단계) |

- `MISSION_ITEM_INT`와 `.waypoints` 파일 모두 위 번호를 쓴다 (Mission Planner·QGroundControl과 같음). 5·6·11 같은 `_INT` 프레임은 MAVLink에서 폐기 예정이라 보내지 않고, 받으면 같은 뜻으로 읽는다.
- 고도를 쓰지 않는 명령(`rtl`, `change_speed`, `land`)은 FC·GCS마다 frame을 0·2·3으로 제각각 돌려주므로, 읽을 때 `relative`로 맞춘다.

- **seq 규칙:** `items[].seq`는 0부터 시작. ArduPilot은 0번 미션을 홈 위치로 쓰므로 **드론에 올릴 때 MAVLink seq = seq + 1**, 0번에는 홈을 넣는다. 대시보드 화면의 번호도 Mission Planner와 같게 seq + 1로 보여 준다.
- **home (선택):** 계획할 때 정한 이륙 지점. 대시보드의 거리·비행 시간 추정과 `.waypoints` 파일 0번 줄에 쓴다. 실제 홈은 기체가 시동을 걸 때 GPS 위치로 정하므로, 드론에 올릴 때는 참고용이다.
- **검증:** relative/terrain 고도는 0 초과 **150m 이하** (국내 비행 고도 제한). 첫 항목은 `takeoff`, 마지막은 `rtl` 또는 `land`를 권장 (대시보드에서 경고).
- **변환 코드:** `protocol.item_to_mav` / `item_from_mav` (드론·파일 공용), `.waypoints` 파일은 `protocol.dump_waypoints` / `load_waypoints`. 위 표에 없는 명령(카메라 트리거 등)이 든 파일은 거부한다.

## 6. 버전 관리

- 필드 **추가**는 버전을 올리지 않는다. 받는 쪽은 모르는 필드를 무시한다.
- 필드 삭제·이름 변경·의미 변경은 `v`를 올리고, 이 문서 아래에 변경 내역을 남긴다.

| 버전 | 날짜 | 변경 |
|---|---|---|
| 1 (초안) | 2026-10-27 | 최초 작성 |
| 1 (초안) | 2026-10-29 | `MissionPlan.home` 추가 (선택 필드라 버전 유지) |
| 1 (초안) | 2026-11-02 | `DroneMission`·`StatusText` 토픽 추가. `MISSION_ITEM_INT` frame을 6 → 3으로 (FC는 둘 다 받음). `Telemetry.mission.total`을 홈 제외 항목 수로 바로잡음 (전에는 1 적게 나옴) |
| 1 (초안) | 2026-11-02 | 엣지 AI(하이브리드) 구조로: `DetectionFrame`을 드론이 발행, `infer_ms` 추가. `EdgeStatus`·`Snapshot`·`CameraPreview`·`SimFires` 토픽 추가 |
