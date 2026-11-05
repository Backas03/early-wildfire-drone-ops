# 저비용 AI 기반 산불 조기 탐지 및 대응 드론 시스템

대구대학교 2026 캡스톤디자인 · 팀 불좀꺼조

드론(F450 + Pixhawk + 라즈베리파이 5)의 라즈베리파이가 카메라 영상에서 YOLOv8로 불꽃·연기를 직접 찾고(**엣지 AI**), LTE로 탐지 결과·사진·위치를 서버에 보낸다. 서버는 사진을 큰 모델로 한 번 더 확인하고(하이브리드), 웹 대시보드에 알림·위치·사진을 보여준다. 웨이포인트(순찰 경로) 설정도 대시보드에서 한다.

## 폴더 구조

| 폴더 | 내용 | 상태 |
|---|---|---|
| [`docs/protocol.md`](docs/protocol.md) | **서버 간 통신 프로토콜 명세** (MQTT 토픽·메시지) | 초안 |
| [`shared/`](shared/) | 프로토콜 코드 (pydantic 모델). 모든 Python 서버가 import | 초안 |
| [`dashboard/backend/`](dashboard/backend/) | 대시보드 API (FastAPI): 미션 저장(SQLite), `.waypoints` 입출력, 드론 명령, WebSocket | 미션·드론 명령 API |
| [`dashboard/frontend/`](dashboard/frontend/) | 대시보드 웹 (React + TypeScript + Vite + Leaflet) | 미션 계획, 실시간 관제 |
| [`event-server/`](event-server/) | 이벤트·로깅 서버: 화재 판정, DB 저장, 알림 | 뼈대 |
| [`drone-agent/`](drone-agent/) | 라즈베리파이용: Pixhawk(MAVLink) ⇄ MQTT 중계 | 텔레메트리, 미션 업로드·다운로드, 모드 변경 |
| [`edge-ai/`](edge-ai/) | 라즈베리파이용: 카메라 → YOLOv8 불·연기 탐지 → MQTT (엣지 AI) | 라즈베리파이 5에서 동작 확인 · 카메라 모듈 시험 전 |
| [`models/`](models/) | 탐지 모델 (`fire_n`·`fire_s`, 받는 방법은 [`models/README.md`](models/README.md)) | |
| [`sim/`](sim/) | 가짜 드론, SITL(가상 드론) 실행, 가상 조종사, **가상 카메라**(위성사진 + 가상 화재) | |
| [`infra/`](infra/) | Mosquitto 등 설정 파일 | |
| `papers/` | 과제 계획서 | |

## 개발 환경 (서버 PC, Windows)

설치된 것: Python 3.13, Node.js 24 LTS, Git, Mosquitto(윈도우 서비스로 자동 실행), MediaMTX, FFmpeg, WSL(Ubuntu 22.04)

처음 한 번 (저장소 루트에서):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r edge-ai/requirements.txt
cd dashboard\frontend; npm install; cd ..\..
# 탐지 모델 받기 (models/README.md)
curl.exe -fL -o models/fire_n.pt https://raw.githubusercontent.com/AlimTleuliyev/wildfire-detection/main/fire-models/fire_n.pt
curl.exe -fL -o models/fire_s.pt https://raw.githubusercontent.com/AlimTleuliyev/wildfire-detection/main/fire-models/fire_s.pt
```

지도 타일 (선택): VWorld 인증키가 있으면 국토부 지도·위성사진을 쓴다. [`dashboard/frontend/.env.example`](dashboard/frontend/.env.example)을 `.env.local`로 복사하고 `VITE_VWORLD_KEY`를 채운 뒤 웹을 다시 실행한다. 키가 없으면 OpenStreetMap과 Esri 위성사진을 쓴다.

## 실행

각각 터미널을 따로 열어서 저장소 루트에서 실행한다. Mosquitto는 서비스로 이미 돌고 있다.

```powershell
# 1. 대시보드 API  → http://localhost:8000/api/health
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir dashboard/backend --port 8000 --reload

# 2. 대시보드 웹  → http://localhost:5173
npm run dev --prefix dashboard/frontend

# 3-a. SITL(가상 드론) + drone-agent + 엣지 AI  → 아래 "시뮬레이션으로 비행해 보기"
wsl bash sim/sitl/run_sitl.sh                                   # 터미널 A
.\.venv\Scripts\python.exe drone-agent/agent.py --mavlink tcp:127.0.0.1:5760   # 터미널 B
.\.venv\Scripts\python.exe edge-ai/edge.py --source sim                         # 터미널 C (가상 카메라로 탐지)

# 3-b. 또는 가짜 드론 (원을 그리며 텔레메트리만 보냄. 명령은 못 받음)
#      drone-agent와 같은 이름(drone1)을 쓰므로 둘 중 하나만 켠다
.\.venv\Scripts\python.exe sim/fake_drone.py

# 4. 이벤트 서버 (선택)
cd event-server; ..\.venv\Scripts\python.exe -m event_server.main
```

## 시뮬레이션으로 비행해 보기

실제 기체 없이 ArduPilot SITL(진짜 비행 프로그램을 PC에서 돌리는 가상 드론)로 미션 업로드부터 귀환까지 해 볼 수 있다. 대시보드 API·웹은 위 1, 2번처럼 켜 둔다.

1. **SITL 켜기** (터미널 A): `wsl bash sim/sitl/run_sitl.sh`
   - 처음 한 번은 실행 파일(약 7 MB)을 WSL의 `~/fire-drone-sitl`에 받는다.
   - "Waiting for connection" 이 나오면 정상. drone-agent가 붙어야 시작한다.
2. **drone-agent 켜기** (터미널 B): `.\.venv\Scripts\python.exe drone-agent/agent.py --mavlink tcp:127.0.0.1:5760`
   - "FC 연결됨"이 나오고, 대시보드 위쪽에 "드론 1대 온라인"이 뜬다.
3. **엣지 AI 켜기** (터미널 C): `.\.venv\Scripts\python.exe edge-ai/edge.py --source sim`
   - 가상 카메라가 드론 위치에서 내려다본 위성사진에 가상 화재(불꽃·연기)를 그려 넣고, 라즈베리파이 대신 이 PC에서 탐지한다.
   - 가상 화재 위치는 [`sim/scenarios/campus.json`](sim/scenarios/campus.json) (캠퍼스 남쪽 하천 풀밭 3곳). 지도에 빨간 점선 원으로 보인다.
4. **미션 올리기**: **미션 계획** 탭 → `drone1 홈 사용` → 웨이포인트 찍기 (가상 화재 위를 지나게) → 왼쪽 위 `드론에 업로드`.
   - 옆에 "드론과 같음"이 뜨면 드론에 들어간 것이다. 고도는 30~40 m를 권장 (아래 "알아 둘 것").
5. **이륙** (터미널 D): `.\.venv\Scripts\python.exe sim/sitl/pilot.py start`
   - 대시보드는 시동·이륙을 하지 않으므로 이 스크립트가 조종사 역할을 한다 (시동 + 미션 시작).
   - SITL을 막 켰으면 GPS가 잡힐 때까지 30초~1분 동안 시동이 거부되는데, 알아서 다시 시도한다.
6. **지켜보기**: **실시간 관제** 탭. 드론 위치·궤적, 지금 향하는 웨이포인트(빨간 점), 고도·속도·배터리, FC 메시지가 보인다.
   - **카메라 · AI 탐지**: 카메라 미리보기(1초에 한 장)에 탐지 상자가 겹쳐 보이고, 불·연기를 찾으면 사진이 서버에 저장되어 아래에 쌓인다. 지도의 하늘색 점선은 카메라가 보는 땅 범위.
   - `일시 정지`(BRAKE): 그 자리에서 멈춤 → `미션 시작·재개`(AUTO): 멈춘 곳부터 이어서
   - `귀환 (RTL)`: 홈으로 돌아와 착륙 · `바로 착륙`(LAND): 그 자리에 착륙
   - 착륙하면 FC가 알아서 시동을 끈다.

알아 둘 것

- 비행 중(AUTO)에는 미션을 바꿀 수 없다. `일시 정지` 뒤에 올린다.
- **가상 카메라의 한계**: 고도 25 m 아래(이륙·착륙 중)에서는 위성사진을 크게 늘려 뿌옇게 보이고, AI가 이를 연기로 착각해 사진이 저장되곤 한다. 실제 카메라와 다른 점이고, 서버 쪽에서 낮은 고도의 탐지를 거르는 것이 다음 단계다. 탐지율 측정은 `python sim/evaluate_detection.py` (결과는 [`models/README.md`](models/README.md)).
- 가상 카메라 장면 한 장만 그려 보기: `python sim/virtual_camera.py --lat 35.89632 --lon 128.8504 --alt 35 --out frame.jpg`
- SITL 옵션: `SITL_SPEEDUP=2`(2배속), `SITL_WIPE=1`(파라미터·미션 초기화), `SITL_HOME="위도,경도,해발,방향"`. 예: `wsl env SITL_SPEEDUP=2 bash sim/sitl/run_sitl.sh`
- SITL 배터리는 비행할수록 줄어든다. SITL을 다시 켜면 가득 찬다.
- Mission Planner로 같은 SITL 보기: TCP 연결 → `127.0.0.1` : `5762`. Mission Planner에서 미션을 바꾸면 대시보드에도 반영된다.
- `pilot.py takeoff --alt 20`: 시동 + 이륙 후 제자리 비행 (그다음 대시보드에서 `미션 시작·재개`).

## 엣지 AI를 라즈베리파이에서 돌리기

같은 프로그램을 카메라만 바꿔서 쓴다. 드론에 달기 전에 책상 위에서 속도·발열을 잴 수 있다.
라즈베리파이 5 + Raspberry Pi OS 13(trixie, Python 3.13)에서 확인했다.

**서버 PC 준비 (한 번, 관리자 PowerShell)**: Mosquitto가 바깥 접속을 받게 하고, 방화벽은 라즈베리파이 IP만 연다. 자세한 내용은 [`infra/mosquitto/mosquitto.conf`](infra/mosquitto/mosquitto.conf) 맨 위 주석.

```powershell
Copy-Item infra\mosquitto\mosquitto.conf "C:\Program Files\mosquitto\mosquitto.conf"
New-NetFirewallRule -DisplayName "Mosquitto MQTT from pi-vitamin" -Direction Inbound -Protocol TCP -LocalPort 1883 -RemoteAddress <라즈베리파이 IP> -Action Allow -Profile Any
Restart-Service mosquitto
```

공유기에서 라즈베리파이 IP를 고정(DHCP 예약)해 둔다. 방화벽 규칙이 그 IP를 기준으로 한다.

**라즈베리파이 설치 (한 번)**

```bash
git clone https://github.com/Backas03/early-wildfire-drone-ops.git && cd early-wildfire-drone-ops
sudo apt install -y python3-picamera2                    # 카메라 모듈 3 (pip 아님, 데스크톱 OS에는 이미 있음)
python3 -m venv --system-site-packages .venv             # apt로 깐 picamera2·numpy를 보이게
# PyTorch는 CPU 전용판을 먼저 깐다. 그냥 깔면 GPU(CUDA)판과 NVIDIA 라이브러리 4 GB가 같이 깔린다
.venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r edge-ai/requirements.txt -e shared
```

탐지 모델은 서버 PC에서 확인한 파일을 복사한 뒤 (서버 PC에서 `scp models/fire_n.pt pi-vitamin@pi-vitamin.lan:early-wildfire-drone-ops/models/`), 라즈베리파이용으로 바꾼다:

```bash
.venv/bin/yolo export model=models/fire_n.pt format=ncnn imgsz=640   # → models/fire_n_ncnn_model/
```

**실행**: `--mqtt-host`에는 서버 PC 이름(공유기가 붙여 준 `<PC 이름>.lan`)이나 IP를 쓴다.

```bash
.venv/bin/python edge-ai/edge.py --source picamera --model models/fire_n_ncnn_model --mqtt-host com12-45.lan
```

- 대시보드 **실시간 관제 → 카메라 · AI 탐지**에서 초당 탐지 횟수, 추론 시간, CPU 온도를 볼 수 있다. 80°C를 넘으면 쿨러를 확인한다.
- 라즈베리파이 화면(VNC)에서 탐지 창 보기: `--show`. SSH에서 실행할 때는 앞에 `DISPLAY=:0 XAUTHORITY=~/.Xauthority`를 붙인다. 창을 닫거나 `q`를 눌러도 탐지는 계속된다. 창과 VNC가 CPU를 더 써서 쿨러 없이 초당 5번이면 75°C 가까이 오른다 (초당 3번은 70°C 안팎).
- 카메라 없이 시험: `--source sim` (서버 PC에서 `sim/fake_drone.py`나 SITL을 켜 두면 그 위치에서 본 가상 카메라), `--source video:파일.mp4`, `--source cam:0`
- 전원 확인: `vcgencmd get_throttled`가 `0x0`이 아니면 전압이 떨어진 적이 있다. 라즈베리파이 5는 5V 5A(27W) 전원이 필요하다.

## 미션 계획 화면 사용법

http://localhost:5173 → **미션 계획** 탭

- **홈 지정**: 지도 위 모드 버튼 `홈 지정` → 지도 클릭. 드론이 연결돼 있으면 `drone1 홈 사용` 버튼으로 가져올 수 있다.
- **웨이포인트**: `웨이포인트 추가` 모드에서 지도 클릭. 점은 끌어서 옮기고, 선 가운데 `+`를 누르면 사이에 끼워 넣는다.
- **순찰 경로 자동 생성**: `순찰 영역` 모드에서 꼭짓점을 찍고 간격·방향·고도·속도를 정한 뒤 `미션에 적용` (이륙·RTL은 유지).
- **검증**: 고도 150 m 초과, 위치 누락 등은 오류(저장 불가), 배터리 시간 초과·RTL 없음 등은 경고로 표시한다. 비행 시간 추정에 쓰는 속도·배터리 시간은 `비행 시간 추정 기준`에서 고친다.
- **저장·파일**: `저장`(서버 DB), `가져오기`/`내보내기`(Mission Planner `.waypoints`).
- **드론과 주고받기**: `드론에 업로드`, `드론에서 가져오기`. 옆에 지금 편집 중인 미션이 드론에 든 것과 같은지 보인다.
- **단축키**: `Ctrl+S` 저장, `Ctrl+Z`/`Ctrl+Y` 실행 취소/다시 실행, `Delete` 선택 항목 삭제, `Esc` 선택 모드로.
- 편집 중인 미션은 브라우저에 자동 보관돼서 새로고침해도 남는다 (저장은 따로 해야 함).

MQTT 메시지 직접 보기:

```powershell
& "C:\Program Files\mosquitto\mosquitto_sub.exe" -t "drone/#" -t "server/#" -t "events" -v
```

## 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest shared/tests dashboard/backend/tests drone-agent/tests edge-ai/tests sim/tests
npm test --prefix dashboard/frontend    # 순찰 경로·미션 검증 계산
```

- `drone-agent/tests`: 가짜 FC로 미션 업로드·다운로드 절차(메시지 유실, 거부, 무응답 포함). 실제 ArduPilot과의 확인은 위 시뮬레이션 절차로 한다.
- `edge-ai/tests`: 가짜 카메라·모델로 무엇을 언제 발행하는지 (매 프레임 결과, 사진 간격, 미리보기, 상태).
- `sim/tests`: 가상 카메라의 기하 (땅 ⇄ 화면, 기수 방향, 기울기)와 화재 그리기. 위성 타일 없이 돈다.

프로토콜을 바꾸면 JSON Schema도 다시 만든다: `.\.venv\Scripts\python.exe -m protocol.schema` → `shared/schema/`

## 진행 단계

| 단계 | 기간(안) | 내용 | 상태 |
|---|---|---|---|
| 0. 준비 | 10/27~10/29 | 도구 설치, 폴더 구조, 프로토콜 초안 | 완료 (SITL·Mission Planner는 직접 설치) |
| 1. 미션 계획 화면 | 10/30~11/8 | 지도 웨이포인트 편집, 자동 순찰경로, 검증, 미션 저장, `.waypoints` 입출력 | 구현 완료 · Mission Planner 호환 확인 필요 |
| 2. 드론 연결 | 11/9~11/22 | 미션 업로드/다운로드, 모드 변경, 실시간 지도 표시 | SITL로 구현·비행 확인 · 실기체 확인 필요 |
| 3. 엣지 AI | 11/9~11/22 | 라즈베리파이 탐지(edge-ai), 가상 카메라, 탐지 사진 저장, 관제 화면 | 라즈베리파이 5에서 가상 카메라로 동작 확인 (NCNN 73 ms/장, 초당 3번) · 카메라 모듈 시험 필요 |
| 3-2. 서버 재확인·영상 | 11/23~11/29 | 탐지 사진을 fire_s로 재확인, MediaMTX·브라우저 WebRTC 재생 | |
| 4. 이벤트·로깅 | 11/23~12/6 | 화재 판정(낮은 고도·재확인 반영), DB, 알림, 비행 기록 | |
| 5. 실기체 통합 | 12/7~ | RPi 설치, Tailscale, 벤치 테스트 → 시험 비행 | |

## 안전 원칙

- 대시보드는 **시동(arm)·이륙을 하지 않는다.** 미션 업로드와 모드 변경(AUTO/BRAKE/RTL/LAND)만 한다. 시동은 조종기로 한다.
- 미션 고도는 이륙 지점 기준 **150m 이하**로 제한한다.
- AUTO로 비행 중에는 미션을 바꾸거나 지울 수 없다 (drone-agent가 거부).
- 대시보드의 일시 정지는 LOITER가 아니라 **BRAKE**다. LOITER는 조종기 스로틀을 따라서, 스틱이 내려가 있으면 하강한다 (SITL에서 확인).
