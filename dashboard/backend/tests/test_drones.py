"""드론 명령 API: 가짜 MQTT 브리지로 드론 응답을 흉내 낸다."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import drones
from app.main import app
from app.mqtt_bridge import get_bridge

MISSION = {
    "name": "순찰",
    "items": [
        {"seq": 0, "command": "takeoff", "alt": 30},
        {"seq": 1, "command": "waypoint", "lat": 35.897, "lon": 128.85, "alt": 40},
        {"seq": 2, "command": "rtl"},
    ],
}


class FakeBridge:
    def __init__(self, status=None, replies=("accepted", "succeeded"), error=None):
        self.status = status if status is not None else {"online": True, "fc_connected": True}
        self.replies = replies
        self.error = error
        self.published: list[tuple[str, dict]] = []
        self.waiters: dict[str, asyncio.Queue] = {}

    def drone_status(self, drone_id):
        return self.status

    def watch_acks(self, cmd_id):
        self.waiters[cmd_id] = asyncio.Queue()
        return self.waiters[cmd_id]

    def unwatch_acks(self, cmd_id):
        self.waiters.pop(cmd_id, None)

    def publish(self, topic, payload, qos=1):
        cmd = json.loads(payload)
        self.published.append((topic, cmd))
        for status in self.replies:  # 드론이 바로 응답한 것처럼
            ack = {"cmd_id": cmd["cmd_id"], "drone_id": "drone1", "status": status}
            if status == "failed":
                ack["error"] = self.error
            self.waiters[cmd["cmd_id"]].put_nowait(ack)


@pytest.fixture
def use_bridge():
    def install(bridge: FakeBridge) -> TestClient:
        app.dependency_overrides[get_bridge] = lambda: bridge
        return TestClient(app)

    yield install
    app.dependency_overrides.clear()


def test_set_mode_succeeds(use_bridge):
    bridge = FakeBridge()
    r = use_bridge(bridge).post("/api/drones/drone1/commands", json={"cmd_id": "c1", "type": "set_mode", "mode": "RTL"})
    assert r.status_code == 200 and r.json()["status"] == "succeeded"
    topic, cmd = bridge.published[0]
    assert topic == "drone/drone1/cmd" and cmd["mode"] == "RTL" and cmd["v"] == 1
    assert bridge.waiters == {}  # 끝나면 정리


def test_upload_failure_is_returned(use_bridge):
    bridge = FakeBridge(replies=("accepted", "in_progress", "failed"), error="FC가 미션을 거부함")
    body = {"cmd_id": "c2", "type": "mission_upload", "mission": MISSION}
    r = use_bridge(bridge).post("/api/drones/drone1/commands", json=body)
    assert r.json() == {"cmd_id": "c2", "drone_id": "drone1", "status": "failed", "error": "FC가 미션을 거부함"}


def test_rejects_bad_commands_before_sending(use_bridge):
    bridge = FakeBridge()
    client = use_bridge(bridge)
    # 시동(arm)은 프로토콜에 없다
    assert client.post("/api/drones/drone1/commands", json={"cmd_id": "c", "type": "arm"}).status_code == 422
    assert client.post("/api/drones/drone1/commands", json={"cmd_id": "c", "type": "set_mode", "mode": "STABILIZE"}).status_code == 422
    too_high = {**MISSION, "items": [{"seq": 0, "command": "takeoff", "alt": 200}]}
    r = client.post("/api/drones/drone1/commands", json={"cmd_id": "c", "type": "mission_upload", "mission": too_high})
    assert r.status_code == 422 and "150" in r.text
    assert bridge.published == []


def test_offline_drone(use_bridge):
    r = use_bridge(FakeBridge(status={})).post("/api/drones/x/commands", json={"cmd_id": "c", "type": "mission_download"})
    assert r.status_code == 409 and "오프라인" in r.json()["detail"]
    r = use_bridge(FakeBridge(status={"online": True, "fc_connected": False})).post(
        "/api/drones/x/commands", json={"cmd_id": "c", "type": "mission_download"}
    )
    assert r.status_code == 409 and "FC" in r.json()["detail"]


def test_no_reply_times_out(use_bridge, monkeypatch):
    monkeypatch.setattr(drones, "ACCEPT_TIMEOUT_S", 0.05)
    r = use_bridge(FakeBridge(replies=())).post("/api/drones/drone1/commands", json={"cmd_id": "c", "type": "mission_clear"})
    assert r.status_code == 504 and "응답하지" in r.json()["detail"]
