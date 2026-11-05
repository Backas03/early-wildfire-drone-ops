"""Mission Planner `.waypoints` 파일 (QGC WPL 110) 읽기·쓰기.

파일 형식: 첫 줄 `QGC WPL 110`, 그 뒤 한 줄에 항목 하나, 탭으로 구분된 12칸.

    번호  current  frame  command  p1  p2  p3  p4  위도  경도  고도  autocontinue

0번 줄은 홈 위치다. 그래서 파일의 N번 = MissionPlan의 seq N-1 이다.
"""

from pydantic import ValidationError

from .mission import (
    MAV_CMD,
    MAV_FRAME,
    MavItem,
    MissionItem,
    MissionPlan,
    PlanHome,
    UnsupportedMavItem,
    frame_from_number,
    item_from_mav,
    item_to_mav,
)

HEADER = "QGC WPL 110"


class WaypointsFileError(ValueError):
    pass


def _row(index: int, current: int, frame: int, mav: MavItem) -> str:
    cols = [
        str(index),
        str(current),
        str(frame),
        str(mav.command),
        f"{mav.param1:.8f}",
        f"{mav.param2:.8f}",
        f"{mav.param3:.8f}",
        f"{mav.param4:.8f}",
        f"{mav.lat:.8f}",
        f"{mav.lon:.8f}",
        f"{mav.alt:.6f}",
        "1",
    ]
    return "\t".join(cols)


def dump_waypoints(plan: MissionPlan) -> str:
    """MissionPlan → .waypoints 텍스트. 홈이 없으면 0번 줄 좌표는 0으로 쓴다."""
    home = plan.home
    home_item = MavItem(
        MAV_CMD["waypoint"],
        "absolute",
        lat=home.lat if home else 0.0,
        lon=home.lon if home else 0.0,
        alt=home.alt_msl if home else 0.0,
    )
    lines = [HEADER, _row(0, 1, MAV_FRAME["absolute"], home_item)]
    for item in plan.items:
        mav = item_to_mav(item)
        lines.append(_row(item.seq + 1, 0, MAV_FRAME[mav.frame], mav))
    return "\n".join(lines) + "\n"


def _error_text(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        msg = err["msg"].removeprefix("Value error, ")
        field = ".".join(str(p) for p in err["loc"])
        msgs.append(f"{field}: {msg}" if field else msg)
    return "; ".join(msgs)


def load_waypoints(text: str, name: str) -> MissionPlan:
    """.waypoints 텍스트 → MissionPlan (mission_id 없음). 문제가 있으면 WaypointsFileError."""
    lines = [ln.strip() for ln in text.lstrip("﻿").splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].startswith("QGC WPL"):
        raise WaypointsFileError("Mission Planner .waypoints 파일이 아님 (첫 줄이 'QGC WPL 110'이어야 함)")

    home: PlanHome | None = None
    items: list[MissionItem] = []
    for line_no, line in enumerate(lines[1:], start=2):
        cols = line.split()
        if len(cols) != 12:
            raise WaypointsFileError(f"{line_no}번째 줄: 칸이 12개여야 하는데 {len(cols)}개")
        try:
            index, frame, command = int(cols[0]), int(cols[2]), int(cols[3])
            p1, p2, p3, p4, lat, lon, alt = (float(c) for c in cols[4:11])
        except ValueError:
            raise WaypointsFileError(f"{line_no}번째 줄: 숫자가 아닌 값이 있음") from None

        if index == 0:
            if not (lat == 0 and lon == 0):
                try:
                    home = PlanHome(lat=lat, lon=lon, alt_msl=alt)
                except ValidationError as e:
                    raise WaypointsFileError(f"{line_no}번째 줄(홈): {_error_text(e)}") from None
            continue

        try:
            mav = MavItem(command, frame_from_number(frame), p1, p2, p3, p4, lat, lon, alt)
            items.append(item_from_mav(len(items), mav))
        except UnsupportedMavItem as e:
            raise WaypointsFileError(f"{line_no}번째 줄(파일 {index}번): {e}") from None
        except ValidationError as e:
            raise WaypointsFileError(f"{line_no}번째 줄(파일 {index}번): {_error_text(e)}") from None

    if not items:
        raise WaypointsFileError("미션 항목이 없음 (홈만 있음)")
    try:
        return MissionPlan(name=name[:100] or "가져온 미션", home=home, items=items)
    except ValidationError as e:
        raise WaypointsFileError(_error_text(e)) from None
