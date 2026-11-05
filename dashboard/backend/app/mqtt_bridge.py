"""MQTT → asyncio 큐 중계.

paho는 자체 스레드에서 돌고, 받은 메시지를 call_soon_threadsafe로 이벤트 루프에 넘긴다.
(aiomqtt는 윈도우 기본 이벤트 루프에서 동작하지 않아서 이 방식을 쓴다.)
"""

import asyncio
import json
import logging
import os
from collections import defaultdict, deque

import paho.mqtt.client as mqtt

from protocol import ServerStatus, topics

from .media import save_snapshot

log = logging.getLogger("dashboard-api.mqtt")

# 브라우저로 넘길 토픽
FORWARD = [
    topics.ALL_TELEMETRY,
    topics.ALL_STATUS,
    topics.ALL_CMD_ACK,
    topics.ALL_MISSION,
    topics.ALL_STATUSTEXT,
    topics.ALL_DETECTIONS,
    topics.ALL_EDGE,
    topics.ALL_EDGE_PREVIEW,
    topics.ALL_SNAPSHOT,
    topics.SIM_FIRES,
    topics.EVENTS,
    topics.ALL_SERVER_STATUS,
]
QUEUE_MAX = 200  # 느린 브라우저 때문에 메모리가 쌓이지 않게
# 새로 연 브라우저에도 보여 주려고 드론마다 보관하는 개수
TEXTS_KEPT = 30
SNAPSHOTS_KEPT = 20
# 마지막 값만 기억했다가 새 브라우저에 먼저 보내는 토픽 (retained가 아니어도)
LATEST = ("/status", "/mission", "/edge")


class MqttBridge:
    def __init__(self):
        self.host = os.getenv("MQTT_HOST", "127.0.0.1")
        self.port = int(os.getenv("MQTT_PORT", "1883"))
        self.connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()
        self._retained: dict[str, object] = {}
        self._texts: defaultdict[str, deque] = defaultdict(lambda: deque(maxlen=TEXTS_KEPT))
        self._snapshots: defaultdict[str, deque] = defaultdict(lambda: deque(maxlen=SNAPSHOTS_KEPT))
        self._ack_waiters: dict[str, asyncio.Queue] = {}
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="dashboard-api")
        offline = ServerStatus(name="dashboard-api", online=False)
        self._client.will_set(topics.server_status("dashboard-api"), offline.model_dump_json(), qos=1, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # --- 수명 주기 -----------------------------------------------------
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._client.connect_async(self.host, self.port, keepalive=10)
        self._client.loop_start()

    def stop(self) -> None:
        offline = ServerStatus(name="dashboard-api", online=False)
        self._client.publish(topics.server_status("dashboard-api"), offline.model_dump_json(), qos=1, retain=True)
        self._client.loop_stop()
        self._client.disconnect()

    def publish(self, topic: str, payload: str, qos: int = 1) -> None:
        self._client.publish(topic, payload, qos=qos)

    # --- WebSocket 구독자 관리 -----------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._queues.discard(q)

    def snapshot(self) -> list[tuple[str, object]]:
        """새 브라우저에 먼저 보낼 것: retained 상태 + 드론별 최근 FC 문장·탐지 사진."""
        texts = [(topics.statustext(d), t) for d, q in list(self._texts.items()) for t in list(q)]
        snaps = [(topics.snapshot(d), s) for d, q in list(self._snapshots.items()) for s in list(q)]
        return list(self._retained.items()) + texts + snaps

    def drone_status(self, drone_id: str) -> dict | None:
        status = self._retained.get(topics.status(drone_id))
        return status if isinstance(status, dict) else None

    # --- 명령 ack 기다리기 (이벤트 루프에서만 호출) ----------------------
    def watch_acks(self, cmd_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._ack_waiters[cmd_id] = q
        return q

    def unwatch_acks(self, cmd_id: str) -> None:
        self._ack_waiters.pop(cmd_id, None)

    # --- paho 콜백 (paho 스레드에서 실행됨) -----------------------------
    def _on_connect(self, client, _userdata, _flags, reason_code, _props):
        self.connected = not reason_code.is_failure
        log.info("MQTT 연결: %s", reason_code)
        for t in FORWARD:
            client.subscribe(t, qos=1)
        online = ServerStatus(name="dashboard-api", online=True, version="0.3.0")
        client.publish(topics.server_status("dashboard-api"), online.model_dump_json(), qos=1, retain=True)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _props):
        self.connected = False
        log.warning("MQTT 끊김: %s", reason_code)

    def _on_message(self, _client, _userdata, msg):
        try:
            data = json.loads(msg.payload)
        except ValueError:
            log.warning("JSON 아님, 무시: %s", msg.topic)
            return
        if msg.topic.endswith("/snapshot"):
            # 사진은 파일로 저장하고 브라우저에는 주소만 넘긴다 (paho 스레드에서 저장해도 충분히 빠름)
            data = save_snapshot(data)
            if data is None:
                return
        if msg.retain or msg.topic.endswith(LATEST) or msg.topic == topics.SIM_FIRES:
            self._retained[msg.topic] = data
        if self._loop:
            self._loop.call_soon_threadsafe(self._fanout, msg.topic, data)

    def _fanout(self, topic: str, data: object) -> None:
        if topic.endswith("/statustext"):
            self._texts[topics.drone_id_from(topic)].append(data)
        elif topic.endswith("/snapshot"):
            self._snapshots[topics.drone_id_from(topic)].append(data)
        elif topic.endswith("/cmd/ack") and isinstance(data, dict):
            waiter = self._ack_waiters.get(data.get("cmd_id"))
            if waiter:
                waiter.put_nowait(data)
        for q in list(self._queues):
            if q.full():
                q.get_nowait()  # 가장 오래된 것 버림
            q.put_nowait((topic, data))


_bridge: MqttBridge | None = None


def get_bridge() -> MqttBridge:
    """앱 전체가 쓰는 브리지 하나. 테스트에서는 dependency_overrides로 바꾼다."""
    global _bridge
    if _bridge is None:
        _bridge = MqttBridge()
    return _bridge
