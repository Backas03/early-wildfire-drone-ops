"""서버 재확인: 가짜 큰 모델로 판정·메시지를 확인한다 (모델 파일 없이 돈다)."""

import base64

import cv2
import numpy as np

from event_server.recheck import Rechecker, describe, judge
from protocol import Detection, Snapshot

SMOKE = Detection(cls="smoke", conf=0.84, bbox=(0.1, 0.1, 0.9, 0.9))
WEAK = Detection(cls="smoke", conf=0.52, bbox=(0.1, 0.1, 0.9, 0.9))


class FakeDetector:
    name = "fire_s"

    def __init__(self, dets):
        self.dets = dets
        self.seen: list[tuple[int, int]] = []

    def detect(self, frame):
        self.seen.append(frame.shape[:2])
        return self.dets, 64.04


def snapshot(jpeg_b64: str | None = None) -> Snapshot:
    if jpeg_b64 is None:
        ok, buf = cv2.imencode(".jpg", np.full((48, 64, 3), 128, np.uint8))
        jpeg_b64 = base64.b64encode(buf.tobytes()).decode()
    return Snapshot(
        drone_id="drone1", snapshot_id="drone1-1-abc", frame_ts=1, width=64, height=48,
        model="fire_n", detections=[Detection(cls="smoke", conf=0.72, bbox=(0, 0, 1, 1))], jpeg_b64=jpeg_b64,
    )  # fmt: skip


def test_judge_uses_threshold():
    assert judge([SMOKE], 0.6) == "confirmed"
    assert judge([WEAK], 0.6) == "rejected"
    assert judge([], 0.6) == "rejected"
    assert judge([WEAK], 0.5) == "confirmed"


def test_check_confirmed_keeps_ids_and_big_model_result():
    det = FakeDetector([SMOKE])
    check = Rechecker(det, 0.6).check(snapshot())
    assert det.seen == [(48, 64)]  # 원본 크기 그대로 큰 모델에 넣음
    assert (check.drone_id, check.snapshot_id, check.model) == ("drone1", "drone1-1-abc", "fire_s")
    assert check.verdict == "confirmed" and check.detections == [SMOKE] and check.infer_ms == 64.0
    assert describe(check, snapshot().detections) == "확인됨: fire_s smoke 0.84 (엣지 smoke 0.72, 64 ms)"


def test_check_rejected_when_big_model_disagrees():
    check = Rechecker(FakeDetector([WEAK]), 0.6).check(snapshot())
    assert check.verdict == "rejected" and check.threshold == 0.6
    assert describe(check, []).startswith("오탐 의심")


def test_unreadable_photo_is_skipped():
    det = FakeDetector([SMOKE])
    assert Rechecker(det).check(snapshot(base64.b64encode(b"not a jpeg").decode())) is None
    assert Rechecker(det).check(snapshot("***")) is None
    assert det.seen == []
