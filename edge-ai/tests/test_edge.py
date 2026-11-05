"""엣지 AI 한 바퀴: 가짜 카메라·가짜 모델로 무엇을 언제 발행하는지 확인한다."""

import base64
from types import SimpleNamespace

import cv2
import numpy as np

from detector import model_name, to_detections
from edge import BOX_COLORS, EdgeRunner, SnapshotPolicy, annotate
from protocol import Detection

NAMES = {0: "smoke", 1: "fire"}  # wildfire-detection 모델 순서


def test_to_detections_maps_by_name_and_cleans_boxes():
    dets = to_detections(
        xyxyn=[[0.1, 0.2, 0.3, 0.4], [0.5, 0.5, 1.2, 0.9], [0.2, 0.2, 0.2, 0.3], [0.1, 0.1, 0.2, 0.2]],
        cls=[0, 1, 1, 7],
        conf=[0.41, 0.83, 0.9, 0.99],
        names={**NAMES, 7: "person"},
    )
    # 신뢰도 순, 0번=smoke·1번=fire, 화면 밖 좌표는 1로, 폭 0인 상자와 모르는 클래스는 버림
    assert [(d.cls, d.conf, d.bbox) for d in dets] == [("fire", 0.83, (0.5, 0.5, 1.0, 0.9)), ("smoke", 0.41, (0.1, 0.2, 0.3, 0.4))]


def test_annotate_draws_boxes_on_a_copy():
    frame = np.zeros((360, 640, 3), np.uint8)
    fire = Detection(cls="fire", conf=0.83, bbox=(0.25, 0.4, 0.75, 0.8))
    smoke = Detection(cls="smoke", conf=0.6, bbox=(0.0, 0.0, 1.0, 1.0))  # 화면 전체 (이름표는 맨 위)
    out = annotate(frame, [fire, smoke], "fire_n | sim")
    assert out.shape == frame.shape and not frame.any()  # 원본은 그대로
    assert tuple(out[216, 160]) == BOX_COLORS["fire"]  # 불 상자 왼쪽 변 (x=0.25*640, y는 상자 중간)
    assert tuple(out[3, 3]) == BOX_COLORS["smoke"]  # 맨 위 이름표가 상태 줄에 가리지 않음
    assert out[345:355, 8:40].any() and not out[345:355, 8:40].all()  # 아래쪽 상태 줄 (글자)
    assert not out[100, 400].any()  # 상자 밖은 그대로


def test_model_name():
    assert model_name("models/fire_n.pt") == "fire_n"
    assert model_name("models/fire_s_ncnn_model") == "fire_s"


def test_snapshot_policy():
    p = SnapshotPolicy(min_conf=0.4, interval_s=5)
    weak = [Detection(cls="smoke", conf=0.3, bbox=(0, 0, 1, 1))]
    strong = [Detection(cls="fire", conf=0.6, bbox=(0, 0, 1, 1))]
    assert not p.should([], 0) and not p.should(weak, 0)
    assert p.should(strong, 1)  # 처음 보이면 바로
    assert not p.should(strong, 3)  # 계속 보이면 5초마다
    assert p.should(strong, 6.1)


class FakeSource:
    kind = "video"

    def __init__(self, frames):
        self.frames = list(frames)

    def read(self):
        return self.frames.pop(0) if self.frames else None


class FakeDetector:
    name = "fire_n"

    def __init__(self, results):
        self.results = list(results)

    def detect(self, frame):
        return self.results.pop(0), 30.0


def run(results, frames=None):
    frame = np.full((720, 1280, 3), 90, np.uint8)
    frames = frames if frames is not None else [(frame, 1000 + i) for i in range(len(results))]
    sent = []
    args = SimpleNamespace(
        snapshot_conf=0.4, snapshot_interval=5, preview_hz=1, preview_width=480, imgsz=640, rate=3
    )
    r = EdgeRunner("drone1", FakeSource(frames), FakeDetector(results), lambda *a: sent.append(a), args)
    for i in range(len(frames)):
        r.step(i / 3)  # 초당 3번
    return sent


def test_runner_publishes_every_frame_and_snapshots_sparingly():
    fire = [Detection(cls="fire", conf=0.7, bbox=(0.4, 0.4, 0.5, 0.5))]
    sent = run([[], fire, fire, fire, []])
    topics = [t for t, *_ in sent]
    assert topics.count("inference/drone1/detections") == 5  # 못 찾은 프레임도
    assert topics.count("drone/drone1/snapshot") == 1  # 1.7초 안에는 한 장만
    assert topics.count("drone/drone1/edge/preview") == 2  # 1초에 한 장 (0초, 1초)
    assert topics.count("drone/drone1/edge") == 1  # 상태는 5초마다

    snap = next(m for t, m, *_ in sent if t == "drone/drone1/snapshot")
    assert snap.frame_ts == 1001 and snap.width == 1280 and snap.detections == fire
    img = cv2.imdecode(np.frombuffer(base64.b64decode(snap.jpeg_b64), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (720, 1280, 3)  # 원본 크기 그대로
    preview = next(m for t, m, *_ in sent if t == "drone/drone1/edge/preview")
    assert (preview.width, preview.height) == (480, 270)
    status = next(m for t, m, *_ in sent if t == "drone/drone1/edge")
    assert status.online and status.model == "fire_n" and status.infer_ms == 30.0


def test_runner_waits_for_camera():
    sent = []
    args = SimpleNamespace(snapshot_conf=0.4, snapshot_interval=5, preview_hz=1, preview_width=480, imgsz=640, rate=3)
    r = EdgeRunner("drone1", FakeSource([]), FakeDetector([]), lambda *a: sent.append(a), args)
    r.source.kind = "sim"
    assert r.step(0) is None
    (topic, status, qos, retain), = sent
    assert topic == "drone/drone1/edge" and retain and "텔레메트리" in status.detail
