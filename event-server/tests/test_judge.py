"""화재 판정: 위치 계산, 묶기, 이벤트로 만드는 조건, 사람이 바꾼 상태 (MQTT 없이)."""

import sys
from pathlib import Path

import pytest

from event_server import geo
from event_server.judge import Judge, JudgeConfig, Track
from event_server.store import EventStore
from protocol import (
    BatteryInfo,
    Detection,
    DetectionFrame,
    FlightInfo,
    GpsInfo,
    Position,
    Snapshot,
    SnapshotCheck,
    Telemetry,
    Velocity,
)

LAT, LON = 35.8964, 128.8495
CENTER = (0.45, 0.45, 0.55, 0.55)  # 화면 가운데 = 드론 바로 아래


def tel(ts, lat=LAT, lon=LON, alt=35.0, heading=0.0, drone="drone1"):
    return Telemetry(
        ts=ts, drone_id=drone,
        position=Position(lat=lat, lon=lon, alt_rel=alt, alt_msl=100 + alt),
        velocity=Velocity(ground_speed=5, climb=0, heading=heading),
        battery=BatteryInfo(voltage=12), gps=GpsInfo(fix_type=3, satellites=12),
        flight=FlightInfo(mode="AUTO", armed=True),
    )  # fmt: skip


def det(cls="smoke", conf=0.7, bbox=CENTER):
    return Detection(cls=cls, conf=conf, bbox=bbox)


def frame(ts, *dets, drone="drone1"):
    return DetectionFrame(drone_id=drone, frame_ts=ts, stream=drone, width=1280, height=720, model="fire_n", detections=list(dets))


def snap(ts, sid, *dets):
    return Snapshot(drone_id="drone1", snapshot_id=sid, frame_ts=ts, width=1280, height=720, model="fire_n", detections=list(dets), jpeg_b64="")


def check(sid, verdict, conf=0.8):
    return SnapshotCheck(
        drone_id="drone1", snapshot_id=sid, model="fire_s", verdict=verdict, threshold=0.6,
        detections=[det(conf=conf)] if verdict == "confirmed" else [], infer_ms=60,
    )  # fmt: skip


def fly_over(j: Judge, t0: int, n: int, **pose):
    """n 프레임 (0.33초 간격) 동안 같은 자리에서 연기를 찾는다."""
    out = []
    for i in range(n):
        ts = t0 + i * 333
        j.on_telemetry(tel(ts, **pose))
        out += j.on_frame(frame(ts, det()))
    return out


# ---------------------------------------------------------------------------- 위치
def test_ground_offset_matches_virtual_camera():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sim"))
    import virtual_camera as vc

    for tilt in (0.0, 20.0):
        cam = vc.CameraSpec(tilt_deg=tilt)
        for heading in (0.0, 75.0, 230.0):
            pose = vc.Pose(LAT, LON, 40.0, heading)
            for east, north in ((0.0, 0.0), (12.0, -5.0), (-8.0, 20.0)):
                px, py = vc.ground_to_pixel(east, north, pose, cam)
                got = geo.ground_offset(px / cam.width, py / cam.height, 40.0, heading, geo.Camera(tilt_deg=tilt))
                assert got == pytest.approx((east, north), abs=1e-6)


def test_box_ahead_of_drone_lands_ahead():
    # 동쪽(90°)을 보고 35 m에서 화면 위쪽 박스 → 드론보다 동쪽, 약 10 m
    e, n = geo.ground_offset(0.5, 0.1, 35.0, 90.0)
    assert e == pytest.approx(35 * 0.8 * 0.37388, abs=0.05) and abs(n) < 1e-9


# ---------------------------------------------------------------------------- 판정
def test_needs_several_frames_and_a_confirmed_check():
    j = Judge()
    assert fly_over(j, 1000, 5) == []  # 프레임만으로는 이벤트가 아니다 (재확인 기다림)
    assert j.candidates() == 1

    j.on_check(snap(1500, "s1", det()), check("s1", "rejected"))
    assert j.events() == []
    created = j.on_check(snap(2000, "s2", det()), check("s2", "confirmed", conf=0.9))
    assert [a for a, _ in created] == ["created"]
    ev = created[0][1]
    assert ev.status == "new" and ev.classes == ["smoke"]
    assert ev.check_confirmed == 1 and ev.check_rejected == 1 and ev.detection_count == 5
    assert ev.snapshot_url == "/media/snapshots/s2.jpg"  # 확인된 사진이 대표
    assert geo.distance(ev.lat, ev.lon, LAT, LON) < 0.5


def test_too_few_frames_or_mostly_rejected_is_not_an_event():
    j = Judge()
    fly_over(j, 1000, 2)
    assert j.on_check(snap(1300, "s1", det()), check("s1", "confirmed")) == []  # 2장뿐

    j = Judge()
    fly_over(j, 1000, 6)
    j.on_check(snap(1300, "a", det()), check("a", "rejected"))
    j.on_check(snap(1600, "b", det()), check("b", "rejected"))
    assert j.on_check(snap(1900, "c", det()), check("c", "confirmed")) == []  # 확인 1 < 의심 2


def test_low_altitude_detections_are_ignored():
    j = Judge()
    fly_over(j, 1000, 6, alt=20.0)
    assert j.on_check(snap(1300, "s1", det()), check("s1", "confirmed")) == []
    assert j.candidates() == 0


def test_far_apart_detections_are_separate_fires():
    j = Judge()
    fly_over(j, 1000, 4)
    far = geo.move(LAT, LON, 0, 100)
    fly_over(j, 3000, 4, lat=far[0], lon=far[1])
    assert j.candidates() == 2


def test_without_recheck_needs_twice_the_frames():
    j = Judge(JudgeConfig(need_check=False))
    assert fly_over(j, 1000, 5) == []
    assert [a for a, _ in fly_over(j, 2665, 1)] == ["created"]


def test_updates_are_throttled_and_candidates_expire():
    j = Judge()
    fly_over(j, 1000, 4)
    j.on_check(snap(1500, "s1", det()), check("s1", "confirmed"))
    fly_over(j, 3000, 3)
    assert j.tick(4000) == []  # 만든 지 5초가 안 됨
    (action, ev), = j.tick(7000)
    assert action == "updated" and ev.detection_count == 7
    assert j.tick(13000) == []  # 바뀐 게 없으면 안 보냄

    far = geo.move(LAT, LON, 200, 0)
    fly_over(j, 20000, 1, lat=far[0], lon=far[1])
    assert j.candidates() == 1
    j.tick(20000 + 61000)
    assert j.candidates() == 0 and len(j.events()) == 1


def test_false_alarm_absorbs_and_resolved_starts_over():
    j = Judge()
    fly_over(j, 1000, 4)
    (_, ev), = j.on_check(snap(1500, "s1", det()), check("s1", "confirmed"))
    assert j.set_status(ev.event_id, "false_alarm", 2000).status == "false_alarm"
    assert fly_over(j, 3000, 6) == []  # 오탐으로 표시한 곳은 다시 알리지 않는다
    assert j.candidates() == 0

    j.set_status(ev.event_id, "resolved", 9000)
    fly_over(j, 10000, 4)
    created = j.on_check(snap(10500, "s2", det()), check("s2", "confirmed"))
    assert [a for a, _ in created] == ["created"] and created[0][1].event_id != ev.event_id
    assert j.set_status("nope", "acknowledged", 1) is None


def test_store_roundtrip_and_resume(tmp_path):
    j = Judge()
    fly_over(j, 1000, 4)
    (_, ev), = j.on_check(snap(1500, "s1", det()), check("s1", "confirmed"))
    store = EventStore(tmp_path / "events.db")
    store.save(ev)
    store.save(ev.model_copy(update={"status": "acknowledged"}))
    loaded = store.recent()
    assert [e.status for e in loaded] == ["acknowledged"]

    # 다시 켜도 같은 곳의 탐지는 그 이벤트에 붙는다
    j2 = Judge(events=loaded)
    assert fly_over(j2, 5000, 3) == []
    assert j2.candidates() == 0
    assert Track.from_event(loaded[0]).snapshot_id == "s1"
