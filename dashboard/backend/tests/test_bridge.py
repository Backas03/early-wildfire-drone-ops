"""MQTT 중계: 서버 재확인 결과가 보관 중인 탐지 사진에 붙는지 (MQTT 연결 없이)."""

from app.mqtt_bridge import MqttBridge


def snap(sid: str) -> dict:
    return {"drone_id": "drone1", "snapshot_id": sid, "url": f"/media/snapshots/{sid}.jpg", "detections": []}


def check(sid: str, verdict: str) -> dict:
    return {"drone_id": "drone1", "snapshot_id": sid, "model": "fire_s", "verdict": verdict}


def test_check_attaches_to_snapshot_in_either_order():
    b = MqttBridge()
    b._fanout("drone/drone1/snapshot", snap("a"))
    b._fanout("drone/drone1/snapshot/check", check("a", "confirmed"))
    b._fanout("drone/drone1/snapshot/check", check("b", "rejected"))  # 사진보다 먼저 온 결과
    b._fanout("drone/drone1/snapshot", snap("b"))
    b._fanout("drone/drone1/snapshot", snap("c"))  # 아직 재확인 전

    kept = {d["snapshot_id"]: d["check"] for t, d in b.snapshot() if t == "drone/drone1/snapshot"}
    assert kept["a"]["verdict"] == "confirmed"
    assert kept["b"]["verdict"] == "rejected"
    assert kept["c"] is None
    assert not b._early_checks
