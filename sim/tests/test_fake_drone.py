"""가짜 드론의 명령 처리와 비행 흉내 (MQTT 없이)."""

import math

import pytest

from fake_drone import FakeDrone
from protocol import GotoCmd, MissionClearCmd, MissionDownloadCmd, MissionPlan, MissionUploadCmd, SetModeCmd, Telemetry

LAT, LON = 35.8964, 128.8495
DT = 0.5


def fly(d: FakeDrone, seconds: float) -> None:
    for _ in range(int(seconds / DT)):
        d.step(DT)


def mode(m: str) -> SetModeCmd:
    return SetModeCmd(cmd_id="c", mode=m)


def north_of_home(m: float) -> float:
    return LAT + math.degrees(m / 6_371_000)


PLAN = MissionPlan(
    name="시험",
    items=[
        {"seq": 0, "command": "takeoff", "alt": 20},
        {"seq": 1, "command": "waypoint", "lat": north_of_home(60), "lon": LON, "alt": 30},
        {"seq": 2, "command": "waypoint", "lat": north_of_home(120), "lon": LON, "alt": 30, "hold_s": 2},
        {"seq": 3, "command": "rtl"},
    ],
)


def test_pause_holds_position_and_resume_continues():
    d = FakeDrone(LAT, LON, radius=90, alt=35)
    fly(d, 3)
    assert d.handle(mode("BRAKE")) == {"status": "succeeded"}
    d.step(DT)
    paused = d.latlon()
    fly(d, 5)
    assert d.latlon() == paused and d.ground_speed == 0
    tel = d.telemetry("drone1")
    assert tel.flight.mode == "BRAKE" and tel.flight.armed

    d.handle(mode("AUTO"))
    fly(d, 2)
    assert d.latlon() != paused and d.ground_speed == pytest.approx(6, abs=0.1)
    assert math.hypot(d.n, d.e) == pytest.approx(90, abs=0.5)  # 같은 원을 이어서 돈다


def test_mission_upload_refused_while_flying_auto():
    d = FakeDrone(LAT, LON)
    r = d.handle(MissionUploadCmd(cmd_id="c", mission=PLAN))
    assert r["status"] == "failed" and "일시 정지" in r["error"]
    assert d.handle(MissionClearCmd(cmd_id="c"))["status"] == "failed"
    assert d.mission is None

    d.handle(mode("BRAKE"))
    assert d.handle(MissionUploadCmd(cmd_id="c", mission=PLAN)) == {"status": "succeeded"}
    assert d.handle(MissionDownloadCmd(cmd_id="c")) == {"status": "succeeded", "mission": PLAN}
    tel = d.telemetry("drone1")
    assert (tel.mission.current_seq, tel.mission.total) == (0, 4)


def test_flies_uploaded_mission_then_lands_home():
    d = FakeDrone(LAT, LON, radius=90, alt=35)
    d.handle(mode("BRAKE"))
    d.handle(MissionUploadCmd(cmd_id="c", mission=PLAN))
    d.handle(mode("AUTO"))
    seen = {d.seq}
    d.step(DT)
    assert d.seq == 1  # 35 m로 떠 있어서 이륙(20 m) 항목은 바로 끝 (ArduCopter와 같음)
    for _ in range(400):  # 200초
        d.step(DT)
        seen.add(d.seq)
        Telemetry.model_validate(d.telemetry("drone1").model_dump())
        if not d.armed:
            break
    assert seen == {0, 1, 2, 3}
    assert not d.armed and d.done
    assert (d.n, d.e, d.alt) == pytest.approx((0, 0, 0), abs=0.01)

    # 착륙 뒤 AUTO: 미션을 처음부터 다시 (가짜 드론만 시동을 대신 건다)
    d.handle(mode("AUTO"))
    assert d.armed and d.seq == 0 and not d.done
    fly(d, 3)
    assert d.alt > 0


def test_waypoint_hold_time():
    d = FakeDrone(LAT, LON, radius=90, alt=30)
    d.handle(mode("BRAKE"))
    d.handle(MissionUploadCmd(cmd_id="c", mission=PLAN))
    d.handle(mode("AUTO"))
    while d.seq < 2:
        d.step(DT)
    while d.hold_left is None:
        d.step(DT)
    fly(d, 1)
    assert d.seq == 2 and d.ground_speed == 0  # 2초 대기 중
    fly(d, 1.5)
    assert d.seq == 3


def test_rtl_climbs_then_returns_and_lands():
    d = FakeDrone(LAT, LON, radius=90, alt=8)
    fly(d, 1)
    d.handle(mode("RTL"))
    d.step(DT)
    assert d.alt > 8 and math.hypot(d.n, d.e) == pytest.approx(90, abs=4)  # 15 m까지 먼저 올라감
    fly(d, 60)
    assert not d.armed and (d.n, d.e, d.alt) == pytest.approx((0, 0, 0), abs=0.01)
    assert d.telemetry("drone1").flight.mode == "RTL"


def test_land_descends_in_place():
    d = FakeDrone(LAT, LON, alt=10)
    d.handle(mode("LAND"))
    d.step(DT)
    spot = d.latlon()
    fly(d, 12)
    assert not d.armed and d.alt == 0 and d.latlon() == spot
    tel = d.telemetry("drone1")
    assert tel.velocity.ground_speed == 0 and 0 <= tel.velocity.heading < 360


def test_goto_flies_there_and_holds():
    d = FakeDrone(LAT, LON, radius=90, alt=35)
    r = d.handle(GotoCmd(cmd_id="c", lat=north_of_home(-50), lon=LON, alt=25))
    assert r == {"status": "succeeded"} and d.mode == "GUIDED"
    fly(d, 60)
    assert (d.n, d.e, d.alt) == pytest.approx((-50, 0, 25), abs=0.01)
    assert d.ground_speed == 0 and d.armed

    d.handle(mode("BRAKE"))  # 다른 모드로 바꾸면 목표는 잊는다
    assert d.target is None


def test_goto_refused_on_ground():
    d = FakeDrone(LAT, LON, alt=3)
    d.handle(mode("LAND"))
    fly(d, 5)
    r = d.handle(GotoCmd(cmd_id="c", lat=LAT, lon=LON, alt=20))
    assert r["status"] == "failed" and "시동" in r["error"]
