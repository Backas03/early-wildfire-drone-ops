"""미션 업로드·다운로드·삭제·모드 변경 절차를 가짜 FC로 확인한다.

가짜 FC는 ArduPilot처럼 응답한다: 요청을 잃어버리면 다시 요청하고, 마지막에 MISSION_ACK를 보낸다.
SITL로 하는 실제 확인은 README의 "시뮬레이션" 절차를 따른다.
"""

from collections import deque
from types import SimpleNamespace

import pytest

import mission_transfer as mt
from protocol import MissionItem, MissionPlan, PlanHome


def msg(kind: str, **fields):
    return SimpleNamespace(get_type=lambda: kind, **fields)


class FakeFC:
    """mission_transfer.Port 구현. 시간은 흐르지 않고, recv가 None을 돌려주면 '시간 초과'로 본다."""

    def __init__(self, rows=None, *, lose_count=0, lose_item=None, reject_at=None, mode_result=0, silent=False):
        self.rows: list[mt.Row] = rows or [mt.Row(16, 0, lat=35.0, lon=128.0, alt=100.0)]
        self.out: deque = deque()
        self.lose_count = lose_count
        self.lose_item = lose_item
        self.reject_at = reject_at
        self.mode_result = mode_result
        self.silent = silent
        self.incoming: list[mt.Row | None] | None = None
        self.waiting: int | None = None
        self.modes: list[int] = []
        self.targets: list[tuple] = []

    # --- Port ---
    def send(self, name, *args):
        if not self.silent:
            getattr(self, f"on_{name}")(*args)

    def recv(self, types, timeout):
        while self.out:
            m = self.out.popleft()
            if m.get_type() in types:
                return m
        if self.waiting is not None:  # FC도 시간이 지나면 같은 항목을 다시 요청한다
            self.out.append(msg("MISSION_REQUEST_INT", seq=self.waiting, mission_type=0))
        return None

    def drain(self):
        self.out.clear()

    # --- 업로드 ---
    def on_mission_count(self, n, mission_type):
        if self.lose_count:
            self.lose_count -= 1
            return
        self.incoming = [None] * n
        self._request(0)

    def _request(self, seq):
        self.waiting = seq
        self.out.append(msg("MISSION_REQUEST_INT", seq=seq, mission_type=0))

    def on_mission_item_int(self, seq, frame, command, current, autocontinue, p1, p2, p3, p4, x, y, z, mission_type):
        if self.lose_item == seq:
            self.lose_item = None
            return
        if seq != self.waiting:
            return
        if seq == self.reject_at:
            self.waiting = None
            self.out.append(msg("MISSION_ACK", type=5, mission_type=0))
            return
        self.incoming[seq] = mt.Row(command, frame, p1, p2, p3, p4, x / 1e7, y / 1e7, z)
        if seq + 1 < len(self.incoming):
            self._request(seq + 1)
        else:
            self.waiting = None
            self.rows = self.incoming
            self.out.append(msg("MISSION_ACK", type=0, mission_type=0))

    # --- 다운로드·삭제 ---
    def on_mission_request_list(self, mission_type):
        self.out.append(msg("MISSION_COUNT", count=len(self.rows), mission_type=0))

    def on_mission_request_int(self, seq, mission_type):
        r = self.rows[seq]
        self.out.append(
            msg(
                "MISSION_ITEM_INT", seq=seq, frame=r.frame, command=r.command, mission_type=0,
                param1=r.param1, param2=r.param2, param3=r.param3, param4=r.param4,
                x=int(round(r.lat * 1e7)), y=int(round(r.lon * 1e7)), z=r.alt,
            )
        )  # fmt: skip

    def on_mission_ack(self, type_, mission_type):
        pass

    def on_mission_clear_all(self, mission_type):
        self.rows = self.rows[:1]
        self.out.append(msg("MISSION_ACK", type=0, mission_type=0))

    # --- 모드 ---
    def on_command_long(self, command, confirmation, *params):
        self.modes.append(int(params[1]))
        self.out.append(msg("COMMAND_ACK", command=command, result=self.mode_result))

    def on_command_int(self, frame, command, current, autocontinue, p1, p2, p3, p4, x, y, z):
        self.targets.append((frame, command, p2, x, y, z))
        self.out.append(msg("COMMAND_ACK", command=command, result=self.mode_result))


FAST = {"timeout": 0.01}


def plan() -> MissionPlan:
    return MissionPlan(
        name="테스트",
        mission_id="abc",
        home=PlanHome(lat=35.8964, lon=128.8495, alt_msl=100),
        items=[
            MissionItem(seq=0, command="takeoff", alt=30),
            MissionItem(seq=1, command="change_speed", speed=6),
            MissionItem(seq=2, command="waypoint", lat=35.8971, lon=128.8502, alt=40.5, hold_s=2),
            MissionItem(seq=3, command="loiter_time", lat=35.8975, lon=128.8510, alt=40.5, hold_s=10),
            MissionItem(seq=4, command="rtl"),
        ],
    )


def test_upload_then_download_round_trip():
    fc = FakeFC()
    progress = []
    drone_home = PlanHome(lat=35.9, lon=128.85, alt_msl=99)
    mt.upload(fc, mt.plan_to_rows(plan(), drone_home), progress.append, **FAST)

    assert len(fc.rows) == 6
    assert fc.rows[0].lat == pytest.approx(35.9)  # 0번은 드론이 아는 홈
    assert fc.rows[1].command == 22 and fc.rows[1].frame == 3  # takeoff, relative
    assert fc.rows[2][:4] == (178, 3, 1, 6)  # change_speed: 대지 속도 6 m/s
    assert progress[-1] == 1.0

    got = mt.plan_from_rows(mt.download(fc, **FAST), "받은 미션")
    assert got is not None
    assert [it.command for it in got.items] == ["takeoff", "change_speed", "waypoint", "loiter_time", "rtl"]
    assert got.items[2].lat == pytest.approx(35.8971) and got.items[2].hold_s == 2
    assert got.home is not None and got.home.lat == pytest.approx(35.9)
    assert mt.same_items(got, plan())


def test_upload_survives_lost_messages():
    fc = FakeFC(lose_count=1, lose_item=2)
    mt.upload(fc, mt.plan_to_rows(plan(), None), **FAST)
    assert len(fc.rows) == 6 and fc.rows[3].command == 16


def test_upload_rejected_by_fc():
    fc = FakeFC(reject_at=3)
    with pytest.raises(mt.FcError, match="잘못된 값"):
        mt.upload(fc, mt.plan_to_rows(plan(), None), **FAST)


def test_no_response():
    fc = FakeFC(silent=True)
    with pytest.raises(mt.FcError, match="응답 없음"):
        mt.upload(fc, mt.plan_to_rows(plan(), None), **FAST)
    with pytest.raises(mt.FcError, match="응답 없음"):
        mt.download(fc, **FAST)
    with pytest.raises(mt.FcError, match="응답 없음"):
        mt.set_mode(fc, 6, **FAST)


def test_download_empty_and_unsupported():
    assert mt.plan_from_rows(mt.download(FakeFC(), **FAST), "x") is None

    rows = mt.plan_to_rows(plan(), None)
    rows.insert(2, mt.Row(206, 0, 50))  # DO_SET_CAM_TRIGG_DIST: 우리 미션 모델에 없음
    with pytest.raises(mt.FcError, match="드론 미션 2번: 지원하지 않는 명령 MAV_CMD 206"):
        mt.plan_from_rows(mt.download(FakeFC(rows), **FAST), "x")


def test_download_normalizes_frames_of_commands_without_altitude():
    # ArduPilot은 RTL·속도 변경을 frame 0(absolute)으로 돌려주기도 한다
    rows = mt.plan_to_rows(plan(), None)
    rows[2] = rows[2]._replace(frame=0)
    rows[5] = rows[5]._replace(frame=0)
    got = mt.plan_from_rows(rows, "x")
    assert got.items[1].frame == "relative" and got.items[4].frame == "relative"


def test_clear():
    fc = FakeFC()
    mt.upload(fc, mt.plan_to_rows(plan(), None), **FAST)
    mt.clear(fc, **FAST)
    assert len(fc.rows) == 1


def test_set_mode():
    fc = FakeFC()
    mt.set_mode(fc, 6, **FAST)
    assert fc.modes == [6]
    with pytest.raises(mt.FcError, match="거부됨"):
        mt.set_mode(FakeFC(mode_result=2), 3, **FAST)


def test_reposition():
    fc = FakeFC()
    mt.reposition(fc, 35.8964123, 128.8495, 30, **FAST)
    # 홈 기준 고도 좌표계(3), DO_REPOSITION(192), GUIDED로 바꾸기 플래그(1), 좌표는 1e7 정수
    assert fc.targets == [(3, 192, 1, 358964123, 1288495000, 30)]
    with pytest.raises(mt.FcError, match="위치 이동을 거부함 \(거부됨\)"):
        mt.reposition(FakeFC(mode_result=2), 35.9, 128.8, 30, **FAST)
    with pytest.raises(mt.FcError, match="응답 없음"):
        mt.reposition(FakeFC(silent=True), 35.9, 128.8, 30, **FAST)


def test_same_items_tolerates_fc_rounding():
    a = plan()
    b = a.model_copy(deep=True)
    b.items[2].lat += 4e-8  # 1e7 정수 변환 오차
    b.items[2].alt = 40.500001  # float32
    assert mt.same_items(a, b)
    b.items[2].alt = 45
    assert not mt.same_items(a, b)
