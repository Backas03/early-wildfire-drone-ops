"""이벤트 상태 API: 가짜 MQTT 브리지로 이벤트 서버 응답을 흉내 낸다."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import events
from app.main import app
from app.mqtt_bridge import MqttBridge, get_bridge


class FakeBridge:
    def __init__(self, online=True, reply=True):
        self.online, self.reply = online, reply
        self.published: list[tuple[str, dict]] = []
        self.waiters: dict[str, asyncio.Queue] = {}

    def server_online(self, name):
        return self.online and name == "event-server"

    def watch_event(self, event_id):
        self.waiters[event_id] = asyncio.Queue()
        return self.waiters[event_id]

    def unwatch_event(self, event_id):
        self.waiters.pop(event_id, None)

    def publish(self, topic, payload, qos=1):
        cmd = json.loads(payload)
        self.published.append((topic, cmd))
        if self.reply:
            q = self.waiters[cmd["event_id"]]
            q.put_nowait({"event_id": cmd["event_id"], "status": "new", "detection_count": 9})  # 탐지로 온 갱신
            q.put_nowait({"event_id": cmd["event_id"], "status": cmd["status"], "detection_count": 9})


@pytest.fixture
def client_with():
    def install(bridge) -> TestClient:
        app.dependency_overrides[get_bridge] = lambda: bridge
        return TestClient(app)

    yield install
    app.dependency_overrides.clear()


def test_status_change_waits_for_event_server(client_with):
    bridge = FakeBridge()
    r = client_with(bridge).post("/api/events/evt_1/status", json={"status": "false_alarm"})
    assert r.status_code == 200 and r.json()["status"] == "false_alarm"
    assert bridge.published[0][0] == "events/cmd"
    assert bridge.published[0][1]["event_id"] == "evt_1" and bridge.published[0][1]["status"] == "false_alarm"
    assert not bridge.waiters


def test_status_change_errors(client_with, monkeypatch):
    assert client_with(FakeBridge(online=False)).post("/api/events/e/status", json={"status": "resolved"}).status_code == 409
    monkeypatch.setattr(events, "REPLY_TIMEOUT_S", 0.05)
    assert client_with(FakeBridge(reply=False)).post("/api/events/e/status", json={"status": "resolved"}).status_code == 504
    assert client_with(FakeBridge()).post("/api/events/e/status", json={"status": "burning"}).status_code == 422


def test_bridge_routes_event_updates_to_waiter():
    b = MqttBridge()
    q = b.watch_event("evt_9")
    b._fanout("events", {"action": "updated", "event": {"event_id": "evt_9", "status": "acknowledged"}})
    b._fanout("events", {"action": "updated", "event": {"event_id": "other", "status": "new"}})
    assert q.get_nowait()["status"] == "acknowledged" and q.empty()
    b._on_message(None, None, type("M", (), {"topic": "server/event-server/status", "payload": b'{"online": true}', "retain": True})())
    assert b.server_online("event-server") and not b.server_online("inference")


def test_bridge_keeps_latest_event_list_for_new_browsers():
    # 이벤트 서버가 retained로 다시 보내도, 이미 구독 중인 쪽에는 retain 표시 없이 온다
    b = MqttBridge()
    for n in (1, 2):
        b._on_message(None, None, type("M", (), {"topic": "events/recent", "payload": json.dumps({"events": [{"n": n}]}).encode(), "retain": False})())
    assert ("events/recent", {"events": [{"n": 2}]}) in b.snapshot()
