"""카메라(영상 입력). 실제 기체·시험·시뮬레이션에서 같은 방식으로 프레임을 꺼낸다.

  picamera     라즈베리파이 카메라 모듈 3 (picamera2, 라즈베리파이에서만)
  sim          가상 카메라: SITL 드론 위치로 위성사진 + 가상 화재를 그림 (sim/virtual_camera.py)
  video:경로    영상 파일을 실제 시간에 맞춰 반복 재생
  cam:0        USB 웹캠 등 (OpenCV 장치 번호)
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

log = logging.getLogger("edge-ai.source")
ROOT = Path(__file__).resolve().parent.parent


def now_ms() -> int:
    return int(time.time() * 1000)


class FrameSource(Protocol):
    kind: str

    def read(self) -> tuple[np.ndarray, int] | None:
        """(BGR 프레임, 촬영 시각 ms). 아직 준비가 안 됐으면 None."""

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
class PiCameraSource:
    """라즈베리파이 카메라 모듈 3. picamera2는 apt로 설치한다 (sudo apt install python3-picamera2)."""

    kind = "picamera"

    def __init__(self, width: int, height: int):
        from picamera2 import Picamera2  # 라즈베리파이에서만 있음

        self.cam = Picamera2()
        # libcamera의 RGB888은 메모리상 B,G,R 순서라 OpenCV에 그대로 쓸 수 있다
        self.cam.configure(self.cam.create_video_configuration(main={"size": (width, height), "format": "RGB888"}))
        self.cam.start()

    def read(self):
        return self.cam.capture_array("main"), now_ms()

    def close(self):
        self.cam.stop()


# ---------------------------------------------------------------------------
class VideoSource:
    """영상 파일(실제 시간에 맞춰 건너뛰며 반복 재생) 또는 OpenCV 장치."""

    def __init__(self, target: str | int):
        self.kind = "cam" if isinstance(target, int) else "video"
        self.cap = cv2.VideoCapture(target)
        if not self.cap.isOpened():
            raise RuntimeError(f"영상을 열 수 없음: {target}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.start = time.monotonic()
        self.pos = 0
        if self.kind == "cam":
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self):
        if self.kind == "cam":
            ok, frame = self.cap.read()
            return (frame, now_ms()) if ok else None
        # 파일: 지난 시간만큼 프레임을 건너뛰어 실제 카메라처럼 "지금" 장면을 준다
        target = int((time.monotonic() - self.start) * self.fps)
        frame = None
        while self.pos <= target:
            ok, f = self.cap.read()
            if not ok:  # 끝 → 처음부터
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.start, self.pos, target = time.monotonic(), 0, 0
                continue
            frame, self.pos = f, self.pos + 1
        return (frame, now_ms()) if frame is not None else None

    def close(self):
        self.cap.release()


# ---------------------------------------------------------------------------
class SimSource:
    """가상 카메라. SITL 드론의 텔레메트리를 MQTT로 받아 그 자리에서 본 장면을 그린다.

    텔레메트리는 2Hz라서, 그 사이에는 속도·방향으로 위치를 앞으로 당겨 계산한다.
    시나리오(가상 화재 정답 위치)를 sim/fires에 retained로 발행해 대시보드가 보여 줄 수 있게 한다.
    """

    kind = "sim"
    STALE_S = 3.0

    def __init__(self, drone_id: str, mqtt_host: str, mqtt_port: int, scenario: Path, width: int, height: int, tilt: float):
        sys.path.insert(0, str(ROOT / "sim"))
        import paho.mqtt.client as mqtt
        import virtual_camera as vc

        from protocol import SimFire, SimFires, Telemetry, topics

        self.vc = vc
        self.scenario = vc.Scenario.load(scenario)
        self.camera = vc.VirtualCamera(self.scenario, vc.CameraSpec(width=width, height=height, tilt_deg=tilt))
        self.lock = threading.Lock()
        self.tel: Telemetry | None = None
        self.tel_at = 0.0
        self.t0 = time.monotonic()

        fires = SimFires(
            scenario=self.scenario.name,
            fires=[SimFire(fire_id=f.fire_id, lat=f.lat, lon=f.lon, size_m=f.size_m, smoke=f.smoke) for f in self.scenario.fires],
        )

        def on_connect(c, _u, _f, _rc, _p):
            c.subscribe(topics.telemetry(drone_id), qos=0)
            c.publish(topics.SIM_FIRES, fires.model_dump_json(), qos=1, retain=True)

        def on_message(_c, _u, msg):
            try:
                tel = Telemetry.model_validate_json(msg.payload)
            except ValueError:
                return
            with self.lock:
                self.tel, self.tel_at = tel, time.monotonic()

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"edge-sim-{drone_id}")
        self.client.on_connect = on_connect
        self.client.on_message = on_message
        self.client.connect_async(mqtt_host, mqtt_port, keepalive=10)
        self.client.loop_start()
        log.info("가상 카메라: 시나리오 '%s' (화재 %d곳), 기울기 %.0f°", self.scenario.name, len(self.scenario.fires), tilt)

    def pose(self):
        """지금 드론 자세 (텔레메트리 + 그 뒤 지난 시간만큼 앞으로 이동). 없거나 오래됐으면 None."""
        with self.lock:
            tel, at = self.tel, self.tel_at
        if tel is None or time.monotonic() - at > self.STALE_S or tel.gps.fix_type < 2:
            return None
        dt = min(max(now_ms() - tel.ts, 0) / 1000, 1.0)
        dist = tel.velocity.ground_speed * dt
        h = math.radians(tel.velocity.heading)
        lat = tel.position.lat + math.degrees(dist * math.cos(h) / 6371000.0)
        lon = tel.position.lon + math.degrees(dist * math.sin(h) / (6371000.0 * math.cos(math.radians(tel.position.lat))))
        alt = max(tel.position.alt_rel + tel.velocity.climb * dt, 1.0)  # 땅에 있을 때도 1 m 높이에서 본 것으로
        return self.vc.Pose(lat, lon, alt, tel.velocity.heading)

    def read(self):
        pose = self.pose()
        if pose is None:
            return None
        ts = now_ms()
        return self.camera.render(pose, time.monotonic() - self.t0), ts

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def open_source(spec: str, *, drone_id: str, mqtt_host: str, mqtt_port: int, scenario: str, width: int, height: int, tilt: float):
    if spec == "picamera":
        return PiCameraSource(width, height)
    if spec == "sim":
        return SimSource(drone_id, mqtt_host, mqtt_port, Path(scenario), width, height, tilt)
    if spec.startswith("video:"):
        return VideoSource(spec.removeprefix("video:"))
    if spec.startswith("cam:"):
        return VideoSource(int(spec.removeprefix("cam:")))
    raise ValueError(f"알 수 없는 --source: {spec} (picamera | sim | video:경로 | cam:번호)")
