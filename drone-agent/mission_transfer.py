"""FC와 미션·모드를 주고받는 MAVLink 절차.

- 미션 업로드:  MISSION_COUNT → (FC) MISSION_REQUEST_INT × N → MISSION_ITEM_INT × N → (FC) MISSION_ACK
- 미션 다운로드: MISSION_REQUEST_LIST → (FC) MISSION_COUNT → MISSION_REQUEST_INT/ITEM_INT × N → MISSION_ACK
- 미션 삭제:    MISSION_CLEAR_ALL → (FC) MISSION_ACK
- 모드 변경:    COMMAND_LONG(DO_SET_MODE) → (FC) COMMAND_ACK
- 위치로 보내기: COMMAND_INT(DO_REPOSITION, GUIDED로 바꾸기 포함) → (FC) COMMAND_ACK

MAVLink 연결은 Port(send/recv)로만 쓴다. 실제로는 fc_link.FcLink가, 테스트에서는 가짜 FC가 붙는다.
번호 규칙: MAVLink 0번은 홈, 1번부터가 MissionPlan.items[0]부터다.
"""

import math
import time
from collections.abc import Callable
from typing import Any, NamedTuple, Protocol

from pydantic import ValidationError

from protocol import (
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

MISSION_TYPE = 0  # MAV_MISSION_TYPE_MISSION (웨이포인트 미션)
MAV_MISSION_ACCEPTED = 0
MAV_CMD_DO_SET_MODE = 176
MAV_MODE_FLAG_CUSTOM_MODE_ENABLED = 1
MAV_RESULT_ACCEPTED = 0
MAV_CMD_DO_REPOSITION = 192
MAV_DO_REPOSITION_FLAGS_CHANGE_MODE = 1  # GUIDED가 아니면 GUIDED로 바꾼 뒤 간다
MAV_FRAME_GLOBAL_RELATIVE_ALT = 3

# 사람이 읽을 수 있게 (pymavlink 없이도 테스트가 돌도록 직접 적어 둔다)
MISSION_RESULT = {
    1: "오류", 2: "지원하지 않는 좌표계", 3: "지원하지 않는 명령", 4: "저장 공간 부족",
    5: "잘못된 값", 6: "param1 오류", 7: "param2 오류", 8: "param3 오류", 9: "param4 오류",
    10: "위도 오류", 11: "경도 오류", 12: "고도 오류", 13: "순서 오류", 14: "거부됨",
    15: "잘못된 번호", 30: "취소됨",
}
COMMAND_RESULT = {1: "일시 거부", 2: "거부됨", 3: "지원 안 함", 4: "실패", 5: "진행 중", 6: "취소됨"}

Progress = Callable[[float], None]


class FcError(Exception):
    """FC가 거부했거나 응답하지 않음. 메시지는 대시보드에 그대로 보인다."""


class Port(Protocol):
    def send(self, name: str, *args: Any) -> None:
        """target_system, target_component를 앞에 붙여 `<name>_send`를 호출한다."""

    def recv(self, types: set[str], timeout: float) -> Any | None:
        """types 중 하나인 다음 메시지. timeout 동안 없으면 None."""

    def drain(self) -> None:
        """이전 거래에서 남은 메시지를 버린다."""


class Row(NamedTuple):
    """MISSION_ITEM_INT 한 줄 (좌표는 도 단위)."""

    command: int
    frame: int
    param1: float = 0.0
    param2: float = 0.0
    param3: float = 0.0
    param4: float = 0.0
    lat: float = 0.0
    lon: float = 0.0
    alt: float = 0.0


# ---------------------------------------------------------------------------
# MissionPlan ⇄ Row
# ---------------------------------------------------------------------------
def plan_to_rows(plan: MissionPlan, home: PlanHome | None) -> list[Row]:
    """0번(홈) + 미션 항목. 홈은 ArduPilot이 무시하지만 자리는 채워야 한다."""
    h = home or plan.home
    rows = [Row(16, MAV_FRAME["absolute"], lat=h.lat if h else 0.0, lon=h.lon if h else 0.0, alt=h.alt_msl if h else 0.0)]
    for item in plan.items:
        m = item_to_mav(item)
        rows.append(Row(m.command, MAV_FRAME[m.frame], m.param1, m.param2, m.param3, m.param4, m.lat, m.lon, m.alt))
    return rows


def plan_from_rows(rows: list[Row], name: str) -> MissionPlan | None:
    """FC에서 읽은 줄 → MissionPlan. 항목이 없으면 None. 표현할 수 없으면 FcError."""
    if len(rows) <= 1:
        return None
    h = rows[0]
    home = PlanHome(lat=h.lat, lon=h.lon, alt_msl=h.alt) if (h.lat or h.lon) else None
    items: list[MissionItem] = []
    for seq, r in enumerate(rows[1:]):
        try:
            mav = MavItem(r.command, frame_from_number(r.frame), r.param1, r.param2, r.param3, r.param4, r.lat, r.lon, r.alt)
            items.append(item_from_mav(seq, mav))
        except UnsupportedMavItem as e:
            raise FcError(f"드론 미션 {seq + 1}번: {e}") from None
        except ValidationError as e:
            raise FcError(f"드론 미션 {seq + 1}번: {e.errors()[0]['msg']}") from None
    return MissionPlan(name=name, home=home, items=items)


def same_items(a: MissionPlan, b: MissionPlan) -> bool:
    """FC를 거쳐 온 값(정수 좌표·float32)과 비교할 수 있게 반올림해서 비교한다."""

    def key(p: MissionPlan):
        return [
            (r.command, round(r.param1, 2), round(r.param2, 2), round(r.lat, 6), round(r.lon, 6), round(r.alt, 1))
            for r in plan_to_rows(p, None)[1:]
        ]

    return key(a) == key(b)


# ---------------------------------------------------------------------------
# 절차
# ---------------------------------------------------------------------------
def _item_args(seq: int, r: Row) -> tuple:
    # MISSION_ITEM_INT: seq, frame, command, current, autocontinue, p1~p4, x, y, z, mission_type
    return (
        seq, r.frame, r.command, 0, 1,
        r.param1, r.param2, r.param3, r.param4,
        int(round(r.lat * 1e7)), int(round(r.lon * 1e7)), float(r.alt),
        MISSION_TYPE,
    )  # fmt: skip


def _mission_type(msg) -> int:
    return getattr(msg, "mission_type", MISSION_TYPE)


def upload(port: Port, rows: list[Row], progress: Progress | None = None, timeout: float = 1.5, retries: int = 4) -> None:
    """미션 전체를 덮어쓴다. 실패하면 FcError."""
    n = len(rows)
    port.drain()
    port.send("mission_count", n, MISSION_TYPE)
    sent: set[int] = set()
    misses = 0
    while True:
        msg = port.recv({"MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"}, timeout)
        if msg is None:
            misses += 1
            if misses > retries:
                raise FcError("FC 응답 없음 (미션 업로드)")
            if not sent:  # 개수를 못 받았을 수 있으니 다시 알린다. 그 뒤로는 FC가 다시 요청한다.
                port.send("mission_count", n, MISSION_TYPE)
            continue
        if _mission_type(msg) != MISSION_TYPE:
            continue
        misses = 0
        if msg.get_type() == "MISSION_ACK":
            if msg.type != MAV_MISSION_ACCEPTED:
                raise FcError(f"FC가 미션을 거부함: {MISSION_RESULT.get(msg.type, msg.type)}")
            if len(sent) < n:
                raise FcError(f"FC가 업로드를 중간에 끝냄 ({len(sent)}/{n})")
            return
        seq = msg.seq
        if not 0 <= seq < n:
            raise FcError(f"FC가 없는 항목을 요청함 ({seq})")
        port.send("mission_item_int", *_item_args(seq, rows[seq]))
        if seq not in sent:
            sent.add(seq)
            if progress:
                progress(len(sent) / n)


def _recv_where(port: Port, types: set[str], pred: Callable[[Any], bool], timeout: float):
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        msg = port.recv(types, left)
        if msg is not None and pred(msg):
            return msg
    return None


def download(port: Port, progress: Progress | None = None, timeout: float = 1.5, retries: int = 4) -> list[Row]:
    """FC 미션 전체를 읽는다 (0번 홈 포함)."""
    port.drain()
    for _ in range(retries):
        port.send("mission_request_list", MISSION_TYPE)
        msg = _recv_where(port, {"MISSION_COUNT"}, lambda m: _mission_type(m) == MISSION_TYPE, timeout)
        if msg is not None:
            break
    else:
        raise FcError("FC 응답 없음 (미션 개수)")
    n = msg.count
    rows: list[Row] = []
    for seq in range(n):
        for _ in range(retries):
            port.send("mission_request_int", seq, MISSION_TYPE)
            item = _recv_where(port, {"MISSION_ITEM_INT"}, lambda m, s=seq: m.seq == s and _mission_type(m) == MISSION_TYPE, timeout)
            if item is not None:
                break
        else:
            raise FcError(f"FC 응답 없음 (미션 {seq}번)")
        rows.append(
            Row(item.command, item.frame, item.param1, item.param2, item.param3, item.param4, item.x / 1e7, item.y / 1e7, item.z)
        )
        if progress:
            progress((seq + 1) / n)
    port.send("mission_ack", MAV_MISSION_ACCEPTED, MISSION_TYPE)
    return rows


def clear(port: Port, timeout: float = 1.5, retries: int = 3) -> None:
    port.drain()
    for _ in range(retries):
        port.send("mission_clear_all", MISSION_TYPE)
        ack = _recv_where(port, {"MISSION_ACK"}, lambda m: _mission_type(m) == MISSION_TYPE, timeout)
        if ack is None:
            continue
        if ack.type != MAV_MISSION_ACCEPTED:
            raise FcError(f"FC가 미션 삭제를 거부함: {MISSION_RESULT.get(ack.type, ack.type)}")
        return
    raise FcError("FC 응답 없음 (미션 삭제)")


def set_mode(port: Port, custom_mode: int, timeout: float = 1.5, retries: int = 3) -> None:
    """ArduPilot 모드 번호(custom_mode)로 바꾼다. FC가 거부하면 FcError."""
    port.drain()
    for attempt in range(retries):
        # COMMAND_LONG: command, confirmation, param1~7
        port.send("command_long", MAV_CMD_DO_SET_MODE, attempt, MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, custom_mode, 0, 0, 0, 0, 0)
        ack = _recv_where(port, {"COMMAND_ACK"}, lambda m: m.command == MAV_CMD_DO_SET_MODE, timeout)
        if ack is None:
            continue
        if ack.result != MAV_RESULT_ACCEPTED:
            raise FcError(f"FC가 모드 변경을 거부함 ({COMMAND_RESULT.get(ack.result, ack.result)})")
        return
    raise FcError("FC 응답 없음 (모드 변경)")


def reposition(port: Port, lat: float, lon: float, alt: float, timeout: float = 1.5, retries: int = 3) -> None:
    """GUIDED로 바꾸고 (lat, lon, 홈 기준 alt)로 보낸다. 도착하면 그 자리에 멈춘다. FC가 거부하면 FcError."""
    port.drain()
    for _ in range(retries):
        # COMMAND_INT: frame, command, current, autocontinue, param1(속도, -1=기본) param2(플래그) param3 param4(방향, NaN=그대로), x, y, z
        port.send(
            "command_int", MAV_FRAME_GLOBAL_RELATIVE_ALT, MAV_CMD_DO_REPOSITION, 0, 0,
            -1, MAV_DO_REPOSITION_FLAGS_CHANGE_MODE, 0, math.nan, round(lat * 1e7), round(lon * 1e7), alt,
        )
        ack = _recv_where(port, {"COMMAND_ACK"}, lambda m: m.command == MAV_CMD_DO_REPOSITION, timeout)
        if ack is None:
            continue
        if ack.result != MAV_RESULT_ACCEPTED:
            raise FcError(f"FC가 위치 이동을 거부함 ({COMMAND_RESULT.get(ack.result, ack.result)})")
        return
    raise FcError("FC 응답 없음 (위치 이동)")
