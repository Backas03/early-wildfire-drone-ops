import pytest

from protocol import (
    MissionItem,
    MissionPlan,
    PlanHome,
    WaypointsFileError,
    dump_waypoints,
    item_to_mav,
    load_waypoints,
)

# Mission Planner가 저장하는 형식 그대로 (CRLF, 탭 구분, 이륙은 좌표 0)
MP_FILE = (
    "QGC WPL 110\r\n"
    "0\t1\t0\t16\t0\t0\t0\t0\t35.896400\t128.849500\t100.000000\t1\r\n"
    "1\t0\t3\t22\t0.00000000\t0.00000000\t0.00000000\t0.00000000\t0.00000000\t0.00000000\t30.000000\t1\r\n"
    "2\t0\t3\t178\t0.00000000\t5.00000000\t0.00000000\t0.00000000\t0.00000000\t0.00000000\t0.000000\t1\r\n"
    "3\t0\t3\t16\t2.00000000\t0.00000000\t0.00000000\t0.00000000\t35.89700000\t128.85000000\t40.000000\t1\r\n"
    "4\t0\t3\t19\t10.00000000\t0.00000000\t0.00000000\t0.00000000\t35.89800000\t128.85100000\t40.000000\t1\r\n"
    "5\t0\t3\t20\t0.00000000\t0.00000000\t0.00000000\t0.00000000\t0.00000000\t0.00000000\t0.000000\t1\r\n"
)


def _plan() -> MissionPlan:
    return MissionPlan(
        name="왕복",
        home=PlanHome(lat=35.8964, lon=128.8495, alt_msl=100),
        items=[
            MissionItem(seq=0, command="takeoff", alt=30),
            MissionItem(seq=1, command="change_speed", speed=5),
            MissionItem(seq=2, command="waypoint", lat=35.897, lon=128.85, alt=40, hold_s=2),
            MissionItem(seq=3, command="loiter_time", lat=35.898, lon=128.851, alt=40, hold_s=10),
            MissionItem(seq=4, command="waypoint", lat=35.899, lon=128.852, alt=500, frame="absolute"),
            MissionItem(seq=5, command="land", lat=35.8965, lon=128.8496),
            MissionItem(seq=6, command="rtl"),
        ],
    )


def test_roundtrip():
    plan = _plan()
    text = dump_waypoints(plan)
    lines = text.splitlines()
    assert lines[0] == "QGC WPL 110"
    assert lines[1].split("\t")[:4] == ["0", "1", "0", "16"]  # 0번 = 홈
    assert len(lines) == 2 + len(plan.items)

    again = load_waypoints(text, plan.name)
    assert again.home == plan.home
    assert again.items == plan.items


def test_load_mission_planner_file():
    plan = load_waypoints(MP_FILE, "뒷산")
    assert plan.name == "뒷산"
    assert plan.home == PlanHome(lat=35.8964, lon=128.8495, alt_msl=100)
    assert [it.command for it in plan.items] == ["takeoff", "change_speed", "waypoint", "loiter_time", "rtl"]
    assert [it.seq for it in plan.items] == [0, 1, 2, 3, 4]
    assert plan.items[0].alt == 30 and plan.items[0].lat is None
    assert plan.items[1].speed == 5
    assert plan.items[2].hold_s == 2 and plan.items[2].frame == "relative"
    assert plan.items[3].hold_s == 10


def test_change_speed_params():
    mav = item_to_mav(MissionItem(seq=0, command="change_speed", speed=7))
    assert (mav.command, mav.param1, mav.param2, mav.param3) == (178, 1, 7, -1)


def test_unsupported_command_is_rejected():
    bad = MP_FILE + "6\t0\t3\t206\t20.0\t0\t1\t0\t0\t0\t0\t1\r\n"  # DO_SET_CAM_TRIGG_DIST
    with pytest.raises(WaypointsFileError, match="MAV_CMD 206"):
        load_waypoints(bad, "x")


def test_altitude_limit_reported_with_line():
    bad = MP_FILE.replace("40.000000\t1\r\n4", "200.000000\t1\r\n4")
    with pytest.raises(WaypointsFileError, match=r"5번째 줄.*150"):
        load_waypoints(bad, "x")


def test_not_a_waypoints_file():
    with pytest.raises(WaypointsFileError, match="QGC WPL"):
        load_waypoints("hello\n1 2 3", "x")
    with pytest.raises(WaypointsFileError, match="항목이 없음"):
        load_waypoints("QGC WPL 110\n0\t1\t0\t16\t0\t0\t0\t0\t35.9\t128.8\t100\t1\n", "x")
