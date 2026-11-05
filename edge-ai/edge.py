"""엣지 AI: 드론의 라즈베리파이에서 카메라 영상으로 불·연기를 찾아 서버에 알린다.

  # 시뮬레이션 (이 PC): SITL 드론 위치에서 본 가상 카메라 장면으로 탐지
  python edge-ai/edge.py --source sim

  # 라즈베리파이 5 + 카메라 모듈 3 (모델은 NCNN으로 바꿔서)
  python edge-ai/edge.py --source picamera --model models/fire_n_ncnn_model --mqtt-host <서버 주소>

  # 영상 파일·웹캠으로 시험
  python edge-ai/edge.py --source video:sample.mp4
  python edge-ai/edge.py --source cam:0

  # 탐지 결과를 화면 창으로 보기 (--show). 라즈베리파이 바탕화면에 띄우면 VNC로 볼 수 있다.
  # SSH에서 실행할 때는 바탕화면을 알려 준다: DISPLAY=:0 XAUTHORITY=~/.Xauthority python edge-ai/edge.py ... --show

하는 일 (초당 --rate 번)
- 프레임마다 탐지 결과 발행: inference/{id}/detections (못 찾아도 빈 목록으로)
- 불·연기를 찾으면 원본 사진 발행: drone/{id}/snapshot (같은 탐지가 이어지면 --snapshot-interval 초마다 한 장)
  → 서버가 저장하고 큰 모델로 다시 확인한다
- 관제용 작은 미리보기: drone/{id}/edge/preview (--preview-hz)
- 상태(속도·온도): drone/{id}/edge (5초마다, retained)
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import signal
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from protocol import CameraPreview, Detection, DetectionFrame, EdgeStatus, Snapshot, topics

log = logging.getLogger("edge-ai")
ROOT = Path(__file__).resolve().parent.parent
STATUS_EVERY_S = 5.0


def jpeg_b64(frame: np.ndarray, quality: int) -> str:
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG 변환 실패")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def cpu_temp_c() -> float | None:
    """라즈베리파이 CPU 온도. 다른 컴퓨터에서는 None."""
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        return None


BOX_COLORS = {"fire": (0, 0, 255), "smoke": (255, 200, 0)}  # BGR: 불 빨강, 연기 하늘색
FONT = cv2.FONT_HERSHEY_SIMPLEX


def annotate(frame: np.ndarray, dets: list[Detection], info: str) -> np.ndarray:
    """탐지 상자·신뢰도와 아래쪽 상태 한 줄을 그린 사본. cv2 글꼴은 한글을 못 그려서 영어로 쓴다.
    상태 줄은 아래에 둔다 (위에 두면 화면 맨 위에서 시작하는 상자의 이름표를 가린다)."""
    img = frame.copy()
    h, w = img.shape[:2]
    scale = max(0.4, w / 1600)
    thick = max(1, round(w / 640))
    for d in dets:
        x1, y1, x2, y2 = (round(v * s) for v, s in zip(d.bbox, (w, h, w, h)))
        color = BOX_COLORS.get(d.cls, (0, 255, 0))
        cv2.rectangle(img, (x1, y1), (x2, y2), color, thick + 1)
        label = f"{d.cls} {d.conf:.2f}"
        (tw, th), base = cv2.getTextSize(label, FONT, scale, thick)
        ty = max(y1, th + base + 2)  # 상자가 화면 맨 위에 붙어 있으면 글자를 상자 안쪽에
        cv2.rectangle(img, (x1, ty - th - base - 2), (x1 + tw + 4, ty), color, -1)
        cv2.putText(img, label, (x1 + 2, ty - base), FONT, scale, (255, 255, 255), thick, cv2.LINE_AA)
    (tw, th), base = cv2.getTextSize(info, FONT, scale, thick)
    cv2.rectangle(img, (0, h - th - base - 12), (tw + 12, h), (0, 0, 0), -1)
    cv2.putText(img, info, (6, h - base - 6), FONT, scale, (255, 255, 255), thick, cv2.LINE_AA)
    return img


class Viewer:
    """--show: 탐지 결과 창. 창을 닫거나 q를 눌러도 탐지·발행은 계속한다 (창만 꺼짐)."""

    TITLE = "edge-ai: fire / smoke detection"

    def __init__(self, width: int = 960):
        self.width = width
        self.shown = False
        self.on = sys.platform == "win32" or bool(os.environ.get("DISPLAY"))
        if not self.on:
            # 화면 없이 Qt 창을 열면 프로그램이 통째로 죽는다 → 미리 걸러서 탐지는 계속
            log.warning("화면(DISPLAY)이 없어 탐지 창 없이 계속한다. SSH에서는 DISPLAY=:0 을 붙여 실행")
            return
        cv2.namedWindow(self.TITLE, cv2.WINDOW_NORMAL)

    def show(self, img: np.ndarray) -> None:
        if not self.on:
            return
        if not self.shown:
            h, w = img.shape[:2]
            cv2.resizeWindow(self.TITLE, self.width, round(h * self.width / w))
        cv2.imshow(self.TITLE, img)
        self.shown = True

    def wait(self, seconds: float) -> None:
        """창이 멈춰 보이지 않게 화면 이벤트를 처리하면서 기다린다."""
        key = cv2.waitKey(max(1, round(seconds * 1000))) & 0xFF
        if key == ord("q") or (self.shown and cv2.getWindowProperty(self.TITLE, cv2.WND_PROP_VISIBLE) < 1):
            self.close()

    def close(self) -> None:
        if self.on:
            cv2.destroyWindow(self.TITLE)
            self.on = False
            log.info("탐지 창을 닫았다 (탐지는 계속)")


class SnapshotPolicy:
    """불·연기를 찾으면 바로 한 장, 계속 보이면 interval초마다 한 장."""

    def __init__(self, min_conf: float, interval_s: float):
        self.min_conf = min_conf
        self.interval_s = interval_s
        self.last = -1e9

    def should(self, dets: list[Detection], now: float) -> bool:
        if not any(d.conf >= self.min_conf for d in dets):
            return False
        if now - self.last < self.interval_s:
            return False
        self.last = now
        return True


class EdgeRunner:
    """카메라 → 탐지 → 발행 한 바퀴(step). publish(topic, model, qos, retain)는 밖에서 넣어 준다."""

    def __init__(self, drone_id: str, source, detector, publish, args):
        self.drone_id = drone_id
        self.source = source
        self.detector = detector
        self.publish = publish
        self.args = args
        self.snap = SnapshotPolicy(args.snapshot_conf, args.snapshot_interval)
        self.recent: deque[tuple[float, float]] = deque()  # (시각, 추론 ms) 최근 5초
        self.next_preview = 0.0
        self.next_status = 0.0
        self.detail: str | None = None
        self.last_frame: np.ndarray | None = None  # --show 창에 그릴 마지막 프레임

    def info_line(self) -> str:
        """탐지 창 위쪽에 쓰는 한 줄: 모델 | 카메라 | 초당 횟수 | 추론 시간 | CPU 온도"""
        s = self.status()
        parts = [f"{s.model}", f"{s.source}"]
        if s.fps:
            parts.append(f"{s.fps:.1f} Hz")
        if s.infer_ms:
            parts.append(f"infer {s.infer_ms:.0f} ms")
        if s.cpu_temp_c:
            parts.append(f"CPU {s.cpu_temp_c:.0f} C")
        return " | ".join(parts)

    def status(self, online: bool = True) -> EdgeStatus:
        n = len(self.recent)
        span = (self.recent[-1][0] - self.recent[0][0]) if n > 1 else 0
        return EdgeStatus(
            drone_id=self.drone_id,
            online=online,
            model=self.detector.name,
            source=self.source.kind,
            imgsz=self.args.imgsz,
            target_hz=self.args.rate,
            fps=round((n - 1) / span, 2) if span > 0 else None,
            infer_ms=round(sum(ms for _, ms in self.recent) / n, 1) if n else None,
            cpu_temp_c=cpu_temp_c(),
            detail=self.detail,
        )

    def step(self, now: float) -> list[Detection] | None:
        got = self.source.read()
        if got is None:
            self.detail = "카메라 기다리는 중" + (" (SITL 드론 텔레메트리 필요)" if self.source.kind == "sim" else "")
            self._maybe_status(now)
            return None
        self.detail = None
        frame, frame_ts = got
        h, w = frame.shape[:2]
        dets, ms = self.detector.detect(frame)
        self.last_frame = frame
        self.recent.append((now, ms))
        while self.recent and now - self.recent[0][0] > 5:
            self.recent.popleft()

        d = self.drone_id
        self.publish(
            topics.detections(d),
            DetectionFrame(
                drone_id=d, frame_ts=frame_ts, stream=d, width=w, height=h,
                model=self.detector.name, detections=dets, infer_ms=round(ms, 1),
            ),
            0, False,
        )  # fmt: skip
        if self.snap.should(dets, now):
            sid = f"{d}-{frame_ts}-{uuid.uuid4().hex[:6]}"
            self.publish(
                topics.snapshot(d),
                Snapshot(
                    drone_id=d, snapshot_id=sid, frame_ts=frame_ts, width=w, height=h,
                    model=self.detector.name, detections=dets, jpeg_b64=jpeg_b64(frame, 85),
                ),
                1, False,
            )  # fmt: skip
            log.info("스냅샷 %s: %s", sid, ", ".join(f"{x.cls} {x.conf:.2f}" for x in dets))
        if self.args.preview_hz > 0 and now >= self.next_preview:
            self.next_preview = now + 1 / self.args.preview_hz
            pw = self.args.preview_width
            small = cv2.resize(frame, (pw, round(h * pw / w)), interpolation=cv2.INTER_AREA)
            self.publish(
                topics.edge_preview(d),
                CameraPreview(
                    drone_id=d, frame_ts=frame_ts, width=small.shape[1], height=small.shape[0],
                    detections=dets, jpeg_b64=jpeg_b64(small, 70),
                ),
                0, False,
            )  # fmt: skip
        self._maybe_status(now)
        return dets

    def _maybe_status(self, now: float) -> None:
        if now >= self.next_status:
            self.next_status = now + STATUS_EVERY_S
            self.publish(topics.edge(self.drone_id), self.status(), 1, True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drone-id", default="drone1")
    ap.add_argument("--source", default="sim", help="picamera | sim | video:경로 | cam:번호")
    ap.add_argument("--model", default=str(ROOT / "models" / "fire_n.pt"))
    ap.add_argument("--imgsz", type=int, default=640, help="모델 입력 크기. 높이 날수록 불이 작게 찍히므로 640 권장")
    ap.add_argument("--rate", type=float, default=3.0, help="초당 탐지 횟수")
    ap.add_argument("--conf", type=float, default=0.25, help="이보다 낮은 탐지는 버림")
    ap.add_argument("--snapshot-conf", type=float, default=0.4, help="이 이상이면 사진을 서버로 보냄")
    ap.add_argument("--snapshot-interval", type=float, default=5.0, help="같은 탐지가 이어질 때 사진 간격(초)")
    ap.add_argument("--preview-hz", type=float, default=1.0, help="관제용 미리보기 (0 = 끔)")
    ap.add_argument("--preview-width", type=int, default=480)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--scenario", default=str(ROOT / "sim" / "scenarios" / "campus.json"), help="sim: 가상 화재 시나리오")
    ap.add_argument("--tilt", type=float, default=0.0, help="sim: 카메라 앞쪽 기울기 deg (0 = 바로 아래)")
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--show", action="store_true", help="탐지 결과를 화면 창에 띄움 (닫아도 탐지는 계속)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import paho.mqtt.client as mqtt

    from detector import Detector
    from sources import open_source

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    log.info("모델 불러오는 중: %s", args.model)
    detector = Detector(args.model, args.imgsz, args.conf)
    source = open_source(
        args.source, drone_id=args.drone_id, mqtt_host=args.mqtt_host, mqtt_port=args.mqtt_port,
        scenario=args.scenario, width=args.width, height=args.height, tilt=args.tilt,
    )  # fmt: skip

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"edge-{args.drone_id}")
    offline = EdgeStatus(drone_id=args.drone_id, online=False)
    client.will_set(topics.edge(args.drone_id), offline.model_dump_json(), qos=1, retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect_async(args.mqtt_host, args.mqtt_port, keepalive=10)
    client.loop_start()

    def publish(topic, model, qos, retain):
        client.publish(topic, model.model_dump_json(), qos=qos, retain=retain)

    runner = EdgeRunner(args.drone_id, source, detector, publish, args)
    viewer = Viewer() if args.show else None

    def pause(seconds: float) -> None:
        if viewer and viewer.on:
            viewer.wait(seconds)
        else:
            stop.wait(seconds)

    log.info("시작: %s, %s, 초당 %.1f번 (Ctrl+C로 종료)", source.kind, detector.name, args.rate)
    period = 1 / args.rate
    next_t = time.monotonic()
    while not stop.is_set():
        now = time.monotonic()
        dets = runner.step(now)
        if dets is None:
            pause(0.3)  # 카메라가 아직 준비 안 됨
            next_t = time.monotonic()
            continue
        if viewer and viewer.on and runner.last_frame is not None:
            viewer.show(annotate(runner.last_frame, dets, runner.info_line()))
        next_t += period
        if next_t < time.monotonic():  # 밀렸으면 따라잡으려 하지 않는다
            next_t = time.monotonic()
        pause(max(0.0, next_t - time.monotonic()))

    log.info("종료")
    if viewer:
        viewer.close()
    client.publish(topics.edge(args.drone_id), runner.status(online=False).model_dump_json(), qos=1, retain=True).wait_for_publish(2)
    source.close()
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
