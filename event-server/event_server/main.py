"""이벤트·로깅 서버 (0단계 뼈대).

지금은 탐지·텔레메트리를 구독해서 개수만 로그로 남긴다. 4단계에서 구현할 것:
  1. 드론별 최근 텔레메트리 버퍼 (frame_ts와 가장 가까운 위치 찾기)
  2. 판정 규칙: 최근 T초 동안 conf ≥ θ 인 프레임 비율이 일정 이상이면 화재 의심
  3. 같은 화재 묶기 (가까운 위치·시간이면 기존 이벤트 갱신)
  4. DB 저장, 스냅샷 저장, `events` 토픽 발행
  5. 비행 단위 텔레메트리 로그 저장

실행: python -m event_server.main   (event-server 폴더에서)
"""

import logging
import signal
import threading

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from protocol import DetectionFrame, ServerStatus, Telemetry, topics

log = logging.getLogger("event-server")
NAME = "event-server"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = {"telemetry": 0, "detections": 0}

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=NAME)
    client.will_set(topics.server_status(NAME), ServerStatus(name=NAME, online=False).model_dump_json(), qos=1, retain=True)

    def on_connect(c, _u, _f, reason_code, _p):
        log.info("MQTT 연결: %s", reason_code)
        c.subscribe([(topics.ALL_TELEMETRY, 0), (topics.ALL_DETECTIONS, 0)])
        c.publish(topics.server_status(NAME), ServerStatus(name=NAME, online=True, version="0.1.0").model_dump_json(), qos=1, retain=True)

    def on_message(_c, _u, msg):
        try:
            if msg.topic.endswith("/telemetry"):
                Telemetry.model_validate_json(msg.payload)
                counts["telemetry"] += 1
            else:
                frame = DetectionFrame.model_validate_json(msg.payload)
                counts["detections"] += 1
                if frame.detections:
                    log.info("탐지: %s %s", frame.drone_id, [(d.cls, round(d.conf, 2)) for d in frame.detections])
        except ValidationError as e:
            log.warning("스키마 불일치 %s: %s", msg.topic, e.errors()[:1])

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect("127.0.0.1", 1883, keepalive=10)
    client.loop_start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.wait(10):
        log.info("최근 10초: 텔레메트리 %d, 탐지 프레임 %d", counts["telemetry"], counts["detections"])
        counts.update(telemetry=0, detections=0)

    client.publish(topics.server_status(NAME), ServerStatus(name=NAME, online=False).model_dump_json(), qos=1, retain=True).wait_for_publish(2)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
