"""미션(웨이포인트) 모델과 MAVLink 매핑.

대시보드·DB·드론 모두 이 모델을 그대로 쓴다. drone-agent는 MAV_CMD/FRAME 표로
MAVLink MISSION_ITEM_INT로 바꾸고, 대시보드 API는 Mission Planner .waypoints 파일로 바꾼다.

seq 규칙: MissionPlan.items의 seq는 0부터 시작한다.
ArduPilot은 미션 0번을 홈 위치로 쓰므로, 드론에 올릴 때는 MAVLink seq = 우리 seq + 1 이다.
"""

from typing import Literal, NamedTuple

from pydantic import BaseModel, Field, model_validator

CommandType = Literal["takeoff", "waypoint", "loiter_time", "change_speed", "rtl", "land"]
AltFrame = Literal["relative", "absolute", "terrain"]

# 우리 명령 → MAV_CMD 번호
MAV_CMD = {
    "waypoint": 16,  # MAV_CMD_NAV_WAYPOINT       param1 = 대기 시간(s)
    "loiter_time": 19,  # MAV_CMD_NAV_LOITER_TIME  param1 = 선회 시간(s)
    "rtl": 20,  # MAV_CMD_NAV_RETURN_TO_LAUNCH
    "land": 21,  # MAV_CMD_NAV_LAND
    "takeoff": 22,  # MAV_CMD_NAV_TAKEOFF
    "change_speed": 178,  # MAV_CMD_DO_CHANGE_SPEED   param2 = 속도(m/s)
}

# 고도 기준 → MAV_FRAME 번호. MISSION_ITEM_INT와 .waypoints 파일 모두 이 번호를 쓴다.
# (5·6·11 같은 _INT 프레임은 MAVLink에서 폐기 예정이라 보내지 않는다. 받으면 읽기는 한다.)
MAV_FRAME = {"absolute": 0, "relative": 3, "terrain": 10}

# 위치가 필요한 명령
_NEEDS_POSITION = {"waypoint", "loiter_time"}
_NEEDS_ALT = {"takeoff", "waypoint", "loiter_time"}

# 국내 드론 비행 고도 제한 (150m 이상은 별도 승인 필요)
MAX_ALT_M = 150.0


class MissionItem(BaseModel):
    seq: int = Field(ge=0)
    command: CommandType
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    alt: float | None = Field(default=None, description="m, frame 기준")
    frame: AltFrame = "relative"
    hold_s: float = Field(default=0, ge=0, description="waypoint 대기 / loiter_time 선회 시간")
    speed: float | None = Field(default=None, gt=0, le=20, description="change_speed 전용, m/s")

    @model_validator(mode="after")
    def _check_fields(self):
        if self.command in _NEEDS_POSITION and (self.lat is None or self.lon is None):
            raise ValueError(f"{self.command}에는 lat/lon이 필요함")
        if self.command in _NEEDS_ALT:
            if self.alt is None:
                raise ValueError(f"{self.command}에는 alt가 필요함")
            if self.frame != "absolute" and not (0 < self.alt <= MAX_ALT_M):
                raise ValueError(f"고도는 0 초과 {MAX_ALT_M:.0f}m 이하여야 함 (현재 {self.alt})")
        if self.command == "change_speed" and self.speed is None:
            raise ValueError("change_speed에는 speed가 필요함")
        return self


class PlanHome(BaseModel):
    """계획할 때 정한 이륙 지점. 거리·시간 추정과 .waypoints 파일 0번 줄에 쓴다.

    실제 홈은 기체가 시동을 걸 때 GPS 위치로 정하므로, 드론에 올릴 때 이 값은 참고용이다.
    """

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_msl: float = Field(default=0, description="해발 고도 m (모르면 0)")


class MissionPlan(BaseModel):
    mission_id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    home: PlanHome | None = None
    items: list[MissionItem] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_seq(self):
        seqs = [it.seq for it in self.items]
        if seqs != list(range(len(seqs))):
            raise ValueError("items의 seq는 0부터 1씩 증가해야 함")
        return self


# ---------------------------------------------------------------------------
# MissionItem ⇄ MAVLink 미션 항목
# drone-agent(MISSION_ITEM_INT)와 .waypoints 파일이 같은 변환을 쓴다.
# ---------------------------------------------------------------------------
class MavItem(NamedTuple):
    """MAVLink 미션 항목 하나. 좌표는 도 단위 실수 (INT 메시지는 보낼 때 1e7을 곱한다)."""

    command: int
    frame: AltFrame
    param1: float = 0.0
    param2: float = 0.0
    param3: float = 0.0
    param4: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0


class UnsupportedMavItem(ValueError):
    """우리 미션 모델로 표현할 수 없는 MAVLink 항목 (예: 카메라 트리거)."""


_CMD_BY_NUMBER = {num: name for name, num in MAV_CMD.items()}
# MAV_FRAME 번호 → 고도 기준. 파일 번호와 INT 번호를 모두 받는다.
# 2(MAV_FRAME_MISSION)는 위치가 없는 DO 명령에 쓰인다.
_FRAME_BY_NUMBER: dict[int, AltFrame] = {
    0: "absolute",
    5: "absolute",
    3: "relative",
    6: "relative",
    2: "relative",
    10: "terrain",
    11: "terrain",
}


def item_to_mav(item: MissionItem) -> MavItem:
    cmd = MAV_CMD[item.command]
    lat, lon = item.lat or 0.0, item.lon or 0.0
    alt = item.alt or 0.0
    match item.command:
        case "waypoint" | "loiter_time":
            return MavItem(cmd, item.frame, param1=item.hold_s, lat=lat, lon=lon, alt=alt)
        case "takeoff":
            return MavItem(cmd, item.frame, lat=lat, lon=lon, alt=alt)
        case "land":
            # 좌표가 0이면 현재 위치에 착륙
            return MavItem(cmd, item.frame, lat=lat, lon=lon)
        case "rtl":
            return MavItem(cmd, item.frame)
        case "change_speed":
            # param1: 1 = 대지 속도, param2: m/s, param3: -1 = 스로틀 유지
            return MavItem(cmd, item.frame, param1=1, param2=item.speed or 0.0, param3=-1)
    raise AssertionError(item.command)


def frame_from_number(frame: int) -> AltFrame:
    """MAV_FRAME 번호(파일용·INT용 모두) → 고도 기준."""
    try:
        return _FRAME_BY_NUMBER[int(frame)]
    except KeyError:
        raise UnsupportedMavItem(f"지원하지 않는 좌표계 MAV_FRAME {frame}") from None


def item_from_mav(seq: int, mav: MavItem) -> MissionItem:
    """MAVLink 항목 → MissionItem. 지원하지 않는 명령이면 UnsupportedMavItem.

    mav.frame은 고도 기준 이름이어야 한다 (번호는 frame_from_number로 먼저 바꾼다).
    """
    name = _CMD_BY_NUMBER.get(int(mav.command))
    if name is None:
        raise UnsupportedMavItem(f"지원하지 않는 명령 MAV_CMD {int(mav.command)}")
    # 고도를 쓰지 않는 명령은 FC·GCS마다 frame을 제각각(0, 2, 3) 돌려주므로 기본값으로 맞춘다
    frame = mav.frame if name in _NEEDS_ALT else "relative"
    has_pos = not (mav.lat == 0 and mav.lon == 0)
    lat = mav.lat if has_pos else None
    lon = mav.lon if has_pos else None
    match name:
        case "waypoint" | "loiter_time":
            return MissionItem(seq=seq, command=name, frame=frame, lat=lat, lon=lon, alt=mav.alt, hold_s=max(mav.param1, 0))
        case "takeoff":
            return MissionItem(seq=seq, command=name, frame=frame, alt=mav.alt)
        case "land":
            return MissionItem(seq=seq, command=name, frame=frame, lat=lat, lon=lon)
        case "rtl":
            return MissionItem(seq=seq, command=name, frame=frame)
        case "change_speed":
            if int(mav.param1) not in (0, 1):
                raise UnsupportedMavItem("DO_CHANGE_SPEED는 수평 속도(param1 = 0 또는 1)만 지원")
            if mav.param2 <= 0:
                raise UnsupportedMavItem("DO_CHANGE_SPEED 속도(param2)가 0 이하")
            return MissionItem(seq=seq, command=name, frame=frame, speed=mav.param2)
    raise AssertionError(name)
