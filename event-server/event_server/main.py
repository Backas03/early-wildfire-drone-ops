"""이벤트·로깅 서버.

지금 하는 일
  - 서버 재확인: 엣지가 보낸 탐지 사진(drone/{id}/snapshot)을 큰 모델(fire_s)로 다시 보고
    drone/{id}/snapshot/check 로 결과(확인됨 / 오탐 의심)를 발행한다 (recheck.py)
  - 탐지·텔레메트리 개수를 10초마다 로그로 남긴다

4단계에서 구현할 것:
  1. 드론별 최근 텔레메트리 버퍼 (frame_ts와 가장 가까운 위치 찾기)
  2. 판정 규칙: 최근 T초 동안 conf ≥ θ 인 프레임 비율이 일정 이상이고, 서버 재확인이 confirmed면 화재 의심
     (낮은 고도의 탐지는 거른다: 가상 카메라·이착륙 중 오탐)
  3. 같은 화재 묶기 (가까운 위치·시간이면 기존 이벤트 갱신)
  4. DB 저장, 스냅샷 저장, `events` 토픽 발행
  5. 비행 단위 텔레메트리 로그 저장

실행 (event-server 폴더에서):  python -m event_server.main
    재확인 끄기: --no-recheck   기준 바꾸기: --recheck-threshold 0.5
"""

import argparse
import logging
import queue
import signal
import threading
from pathlib import Path

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from protocol import DetectionFrame, ServerStatus, Snapshot, Telemetry, topics

from .recheck import DEFAULT_MODEL, DEFAULT_THRESHOLD, Rechecker, describe, load_detector

log = logging.getLogger("event-server")
NAME = "event-server"
VERSION = "0.2.0"
RECHECK_QUEUE = 20  # 밀리면 오래된 사진부터 버린다 (재확인은 최신이 중요)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--recheck-model", default=str(DEFAULT_MODEL), help="서버 재확인 모델 (기본 models/fire_s.pt)")
    ap.add_argument("--recheck-threshold", type=float, default=DEFAULT_THRESHOLD, help="이 이상이면 확인됨")
    ap.add_argument("--no-recheck", action="store_true", help="서버 재확인 끄기")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = {"telemetry": 0, "detections": 0}

    rechecker = None
    if not args.no_recheck:
        if Path(args.recheck_model).exists():
            log.info("재확인 모델 불러오는 중: %s (기준 %.2f)", args.recheck_model, args.recheck_threshold)
            rechecker = Rechecker(load_detector(args.recheck_model), args.recheck_threshold)
        else:
            log.warning("재확인 모델이 없어 재확인 없이 계속한다: %s (models/README.md 참고)", args.recheck_model)
    pending: queue.Queue[bytes] = queue.Queue(maxsize=RECHECK_QUEUE)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=NAME)
    client.will_set(topics.server_status(NAME), ServerStatus(name=NAME, online=False).model_dump_json(), qos=1, retain=True)

    def on_connect(c, _u, _f, reason_code, _p):
        log.info("MQTT 연결: %s", reason_code)
        subs = [(topics.ALL_TELEMETRY, 0), (topics.ALL_DETECTIONS, 0)]
        if rechecker:
            subs.append((topics.ALL_SNAPSHOT, 1))
        c.subscribe(subs)
        c.publish(topics.server_status(NAME), ServerStatus(name=NAME, online=True, version=VERSION).model_dump_json(), qos=1, retain=True)

    def on_message(_c, _u, msg):
        try:
            if msg.topic.endswith("/telemetry"):
                Telemetry.model_validate_json(msg.payload)
                counts["telemetry"] += 1
            elif msg.topic.endswith("/snapshot"):
                # 무거운 추론은 MQTT 스레드에서 하지 않고 작업 스레드로 넘긴다
                if pending.full():
                    pending.get_nowait()
                    log.warning("재확인이 밀려 오래된 사진을 건너뜀")
                pending.put_nowait(msg.payload)
            else:
                DetectionFrame.model_validate_json(msg.payload)
                counts["detections"] += 1
        except ValidationError as e:
            log.warning("스키마 불일치 %s: %s", msg.topic, e.errors()[:1])

    def recheck_worker():
        while not stop.is_set():
            try:
                payload = pending.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                snap = Snapshot.model_validate_json(payload)
            except ValidationError as e:
                log.warning("잘못된 스냅샷 무시: %s", e.errors()[:1])
                continue
            check = rechecker.check(snap)
            if check is None:
                log.warning("사진을 읽을 수 없음: %s", snap.snapshot_id)
                continue
            client.publish(topics.snapshot_check(snap.drone_id), check.model_dump_json(), qos=1)
            log.info("재확인 %s %s", snap.snapshot_id, describe(check, snap.detections))

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect_async(args.mqtt_host, args.mqtt_port, keepalive=10)
    client.loop_start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    worker = threading.Thread(target=recheck_worker, daemon=True) if rechecker else None
    if worker:
        worker.start()
    while not stop.wait(10):
        log.info("최근 10초: 텔레메트리 %d, 탐지 프레임 %d", counts["telemetry"], counts["detections"])
        counts.update(telemetry=0, detections=0)

    log.info("종료")
    client.publish(topics.server_status(NAME), ServerStatus(name=NAME, online=False).model_dump_json(), qos=1, retain=True).wait_for_publish(2)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
