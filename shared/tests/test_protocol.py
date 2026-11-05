import pytest
from pydantic import ValidationError

from protocol import (
    CommandAdapter,
    MissionItem,
    MissionPlan,
    MissionUploadCmd,
    SetModeCmd,
    Telemetry,
    topics,
)


def _plan() -> MissionPlan:
    return MissionPlan(
        name="테스트 순찰",
        items=[
            MissionItem(seq=0, command="takeoff", alt=30),
            MissionItem(seq=1, command="waypoint", lat=35.896, lon=128.849, alt=50, hold_s=3),
            MissionItem(seq=2, command="rtl"),
        ],
    )


def test_telemetry_roundtrip():
    raw = {
        "drone_id": "drone1",
        "position": {"lat": 35.896, "lon": 128.849, "alt_rel": 30.2, "alt_msl": 130.2},
        "velocity": {"ground_speed": 5.1, "climb": 0.0, "heading": 270},
        "battery": {"voltage": 11.8, "remaining": 80},
        "gps": {"fix_type": 3, "satellites": 12, "hdop": 0.8},
        "flight": {"mode": "AUTO", "armed": True},
    }
    t = Telemetry.model_validate(raw)
    again = Telemetry.model_validate_json(t.model_dump_json())
    assert again == t
    assert t.v == 1 and t.ts > 0


def test_command_discriminator():
    cmd = MissionUploadCmd(cmd_id="abc", mission=_plan())
    parsed = CommandAdapter.validate_json(cmd.model_dump_json())
    assert isinstance(parsed, MissionUploadCmd)
    assert parsed.mission.items[1].hold_s == 3

    parsed = CommandAdapter.validate_python({"type": "set_mode", "cmd_id": "x", "mode": "RTL"})
    assert isinstance(parsed, SetModeCmd)


def test_arm_command_not_allowed():
    with pytest.raises(ValidationError):
        CommandAdapter.validate_python({"type": "arm", "cmd_id": "x"})
    with pytest.raises(ValidationError):
        SetModeCmd(cmd_id="x", mode="STABILIZE")


def test_mission_validation():
    with pytest.raises(ValidationError, match="lat/lon"):
        MissionItem(seq=0, command="waypoint", alt=50)
    with pytest.raises(ValidationError, match="150"):
        MissionItem(seq=0, command="waypoint", lat=35.9, lon=128.8, alt=200)
    with pytest.raises(ValidationError, match="seq"):
        MissionPlan(name="x", items=[MissionItem(seq=1, command="rtl")])


def test_topics():
    assert topics.telemetry("drone1") == "drone/drone1/telemetry"
    assert topics.drone_id_from("inference/drone7/detections") == "drone7"
    assert topics.snapshot_check("drone1") == "drone/drone1/snapshot/check"
    assert topics.drone_id_from(topics.snapshot_check("drone2")) == "drone2"
    with pytest.raises(ValueError):
        topics.drone_id_from("events")
