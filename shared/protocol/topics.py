"""MQTT 토픽 이름. 문자열을 직접 쓰지 말고 이 함수들을 사용한다."""

# 드론 ↔ 서버
def telemetry(drone_id: str) -> str:
    return f"drone/{drone_id}/telemetry"


def status(drone_id: str) -> str:
    return f"drone/{drone_id}/status"


def cmd(drone_id: str) -> str:
    return f"drone/{drone_id}/cmd"


def cmd_ack(drone_id: str) -> str:
    return f"drone/{drone_id}/cmd/ack"


def mission(drone_id: str) -> str:
    return f"drone/{drone_id}/mission"


def statustext(drone_id: str) -> str:
    return f"drone/{drone_id}/statustext"


# 드론의 엣지 AI
def edge(drone_id: str) -> str:
    return f"drone/{drone_id}/edge"


def edge_preview(drone_id: str) -> str:
    return f"drone/{drone_id}/edge/preview"


def snapshot(drone_id: str) -> str:
    return f"drone/{drone_id}/snapshot"


# 시뮬레이션 전용: 가상 화재 정답 위치
SIM_FIRES = "sim/fires"


# 추론 서버 → 이벤트 서버
def detections(drone_id: str) -> str:
    return f"inference/{drone_id}/detections"


# 이벤트 서버 → 대시보드
EVENTS = "events"


# 각 서버의 생존 상태 (대시보드 "시스템 상태" 화면용)
def server_status(name: str) -> str:
    return f"server/{name}/status"


# 구독용 와일드카드
ALL_TELEMETRY = "drone/+/telemetry"
ALL_STATUS = "drone/+/status"
ALL_CMD_ACK = "drone/+/cmd/ack"
ALL_MISSION = "drone/+/mission"
ALL_STATUSTEXT = "drone/+/statustext"
ALL_EDGE = "drone/+/edge"
ALL_EDGE_PREVIEW = "drone/+/edge/preview"
ALL_SNAPSHOT = "drone/+/snapshot"
ALL_DETECTIONS = "inference/+/detections"
ALL_SERVER_STATUS = "server/+/status"


def drone_id_from(topic: str) -> str:
    """'drone/{id}/...' 또는 'inference/{id}/...' 에서 id를 꺼낸다."""
    parts = topic.split("/")
    if len(parts) < 3 or parts[0] not in ("drone", "inference"):
        raise ValueError(f"드론 토픽이 아님: {topic}")
    return parts[1]
