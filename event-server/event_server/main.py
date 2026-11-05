"""이벤트 서버: 서버 재확인 + 화재 판정.

  - 서버 재확인: 엣지가 보낸 탐지 사진(drone/{id}/snapshot)을 큰 모델(fire_s)로 다시 보고
    drone/{id}/snapshot/check 로 결과(확인됨 / 오탐 의심)를 발행한다 (recheck.py)
  - 화재 판정: 탐지 프레임을 드론 위치로 땅 위에 찍고, 같은 곳에서 여러 번 잡히면서 재확인도
    통과하면 화재 이벤트로 만든다 (judge.py). 이벤트는 SQLite에 저장하고(store.py)
    events(생성·변경)와 events/recent(retained, 최근 목록)로 발행한다.
  - 대시보드에서 바꾼 이벤트 상태(events/cmd: 확인함·종료·오탐)를 저장하고 다시 발행한다.

남은 것: 비행 단위 텔레메트리 기록, 알림(문자·메신저).

실행 (event-server 폴더에서):  python -m event_server.main
    재확인 끄기: --no-recheck   기준 바꾸기: --recheck-threshold 0.5   판정 기준: --help
"""

import argparse
import logging
import queue
import signal
import threading
from pathlib import Path

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from protocol import (
    DetectionFrame,
    EventList,
    EventMessage,
    EventStatusCmd,
    ServerStatus,
    Snapshot,
    Telemetry,
    now_ms,
    topics,
)

from . import geo
from .judge import Change, Judge, JudgeConfig
from .recheck import DEFAULT_MODEL, DEFAULT_THRESHOLD, Rechecker, describe, load_detector
from .store import EventStore, default_path

log = logging.getLogger("event-server")
NAME = "event-server"
VERSION = "0.3.0"
RECHECK_QUEUE = 20  # 밀리면 오래된 사진부터 버린다 (재확인은 최신이 중요)
CLS_KO = {"fire": "불", "smoke": "연기"}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--recheck-model", default=str(DEFAULT_MODEL), help="서버 재확인 모델 (기본 models/fire_s.pt)")
    ap.add_argument("--recheck-threshold", type=float, default=DEFAULT_THRESHOLD, help="이 이상이면 확인됨")
    ap.add_argument("--no-recheck", action="store_true", help="서버 재확인 끄기 (판정은 프레임 수만 두 배로 봄)")
    d = JudgeConfig()
    ap.add_argument("--min-conf", type=float, default=d.min_conf, help="판정에 쓰는 엣지 박스 최소 신뢰도")
    ap.add_argument("--min-alt", type=float, default=d.min_alt, help="이보다 낮게 날 때의 탐지는 거름 (m)")
    ap.add_argument("--radius", type=float, default=d.radius_m, help="이 거리 안의 탐지는 같은 화재 (m)")
    ap.add_argument("--min-hits", type=int, default=d.min_hits, help="--window초 안에 잡혀야 하는 프레임 수")
    ap.add_argument("--window", type=float, default=d.window_s, help="초")
    ap.add_argument("--camera-tilt", type=float, default=0.0, help="카메라가 앞으로 기운 각도 (0 = 바로 아래)")
    ap.add_argument("--db", default=str(default_path()), help="이벤트 DB 파일")
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

    store = EventStore(args.db)
    cfg = JudgeConfig(
        min_conf=args.min_conf, min_alt=args.min_alt, radius_m=args.radius, min_hits=args.min_hits,
        window_s=args.window, need_check=rechecker is not None, camera=geo.Camera(tilt_deg=args.camera_tilt),
    )  # fmt: skip
    judge = Judge(cfg, store.recent())
    lock = threading.Lock()  # judge는 MQTT 스레드·재확인 스레드·메인 루프가 같이 쓴다
    log.info("이벤트 %d개 불러옴 (%s). 판정: 고도 %.0f m 이상, %.0f m 안, %.0f초에 %d장%s",
             len(judge.events(10_000)), args.db, cfg.min_alt, cfg.radius_m, cfg.window_s, cfg.min_hits,
             " + 서버 재확인" if cfg.need_check else " ×2 (재확인 없음)")  # fmt: skip
    pending: queue.Queue[Snapshot] = queue.Queue(maxsize=RECHECK_QUEUE)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=NAME)
    client.will_set(topics.server_status(NAME), ServerStatus(name=NAME, online=False).model_dump_json(), qos=1, retain=True)

    def publish_recent() -> None:
        # lock을 잡은 채로 부른다
        client.publish(topics.EVENTS_RECENT, EventList(events=judge.events()).model_dump_json(), qos=1, retain=True)

    def emit(changes: list[Change]) -> None:
        # lock을 잡은 채로 부른다
        for action, ev in changes:
            store.save(ev)
            client.publish(topics.EVENTS, EventMessage(action=action, event=ev).model_dump_json(), qos=1)
            if action == "created":
                kinds = "·".join(CLS_KO[c] for c in ev.classes)
                log.warning("화재 의심 %s: %s %.2f, 위치 %.6f, %.6f, 재확인 %d/%d",
                            ev.event_id, kinds, ev.max_conf, ev.lat, ev.lon,
                            ev.check_confirmed, ev.check_confirmed + ev.check_rejected)  # fmt: skip
        if changes:
            publish_recent()

    def on_connect(c, _u, _f, reason_code, _p):
        log.info("MQTT 연결: %s", reason_code)
        c.subscribe([(topics.ALL_TELEMETRY, 0), (topics.ALL_DETECTIONS, 0), (topics.ALL_SNAPSHOT, 1), (topics.EVENTS_CMD, 1)])
        c.publish(topics.server_status(NAME), ServerStatus(name=NAME, online=True, version=VERSION).model_dump_json(), qos=1, retain=True)
        with lock:
            publish_recent()

    def on_message(_c, _u, msg):
        try:
            if msg.topic.endswith("/telemetry"):
                tel = Telemetry.model_validate_json(msg.payload)
                counts["telemetry"] += 1
                with lock:
                    judge.on_telemetry(tel)
            elif msg.topic.endswith("/snapshot"):
                if rechecker is None:
                    return
                # 무거운 추론은 MQTT 스레드에서 하지 않고 작업 스레드로 넘긴다
                if pending.full():
                    pending.get_nowait()
                    log.warning("재확인이 밀려 오래된 사진을 건너뜀")
                pending.put_nowait(Snapshot.model_validate_json(msg.payload))
            elif msg.topic == topics.EVENTS_CMD:
                cmd = EventStatusCmd.model_validate_json(msg.payload)
                with lock:
                    ev = judge.set_status(cmd.event_id, cmd.status, now_ms())
                    if ev is None:
                        log.warning("없는 이벤트의 상태 변경 무시: %s", cmd.event_id)
                        return
                    log.info("이벤트 %s → %s", ev.event_id, ev.status)
                    emit([("updated", ev)])
            else:
                frame = DetectionFrame.model_validate_json(msg.payload)
                counts["detections"] += 1
                with lock:
                    emit(judge.on_frame(frame))
        except ValidationError as e:
            log.warning("스키마 불일치 %s: %s", msg.topic, e.errors()[:1])

    def recheck_worker():
        while not stop.is_set():
            try:
                snap = pending.get(timeout=0.5)
            except queue.Empty:
                continue
            check = rechecker.check(snap)
            if check is None:
                log.warning("사진을 읽을 수 없음: %s", snap.snapshot_id)
                continue
            client.publish(topics.snapshot_check(snap.drone_id), check.model_dump_json(), qos=1)
            log.info("재확인 %s %s", snap.snapshot_id, describe(check, snap.detections))
            with lock:
                emit(judge.on_check(snap, check))

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect_async(args.mqtt_host, args.mqtt_port, keepalive=10)
    client.loop_start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if rechecker:
        threading.Thread(target=recheck_worker, daemon=True).start()
    ticks = 0
    while not stop.wait(1):
        with lock:
            emit(judge.tick(now_ms()))
        ticks += 1
        if ticks % 10 == 0:
            with lock:
                cands, evs = judge.candidates(), sum(e.status in ("new", "acknowledged") for e in judge.events())
            log.info("최근 10초: 텔레메트리 %d, 탐지 프레임 %d · 화재 후보 %d, 진행 중 이벤트 %d",
                     counts["telemetry"], counts["detections"], cands, evs)  # fmt: skip
            counts.update(telemetry=0, detections=0)

    log.info("종료")
    client.publish(topics.server_status(NAME), ServerStatus(name=NAME, online=False).model_dump_json(), qos=1, retain=True).wait_for_publish(2)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
