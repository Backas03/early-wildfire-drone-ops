"""서버 재확인: 엣지(드론)가 보낸 탐지 사진을 큰 모델(fire_s)로 다시 본다.

drone/{id}/snapshot 을 받아 → fire_s로 탐지 → drone/{id}/snapshot/check 발행.
판정: 큰 모델이 불·연기를 기준(threshold) 이상으로 찾으면 confirmed, 아니면 rejected(오탐 의심).

기준 0.6은 2026-11-05 소방 항공 영상에서 라즈베리파이가 보낸 사진 123장으로 정했다 (models/README.md):
실제 산불 95% 유지, 불과 무관한 장면(활주로·호수·풍경) 100% 거름, 물보라는 47%만 거름.
물보라·안개처럼 연기와 닮은 것은 이벤트 판정(여러 장·같은 위치)에서 더 걸러야 한다.
"""

from __future__ import annotations

import base64
import binascii
import sys
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from protocol import Detection, Snapshot, SnapshotCheck

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "models" / "fire_s.pt"
DEFAULT_THRESHOLD = 0.6


class Detector(Protocol):
    name: str

    def detect(self, frame: np.ndarray) -> tuple[list[Detection], float]: ...


def load_detector(model_path: str | Path, imgsz: int = 640) -> Detector:
    """엣지 AI의 탐지 코드(edge-ai/detector.py)를 그대로 쓴다. 0.25 미만은 결과에도 넣지 않는다."""
    sys.path.insert(0, str(ROOT / "edge-ai"))
    from detector import Detector as YoloDetector

    return YoloDetector(str(model_path), imgsz=imgsz, conf=0.25)


def judge(detections: list[Detection], threshold: float) -> str:
    return "confirmed" if any(d.conf >= threshold for d in detections) else "rejected"


def decode_jpeg(jpeg_b64: str) -> np.ndarray | None:
    try:
        raw = base64.b64decode(jpeg_b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    return img if img is not None and img.size else None


class Rechecker:
    def __init__(self, detector: Detector, threshold: float = DEFAULT_THRESHOLD):
        self.detector = detector
        self.threshold = threshold

    def check(self, snap: Snapshot) -> SnapshotCheck | None:
        """사진을 다시 본 결과. 사진을 읽을 수 없으면 None."""
        img = decode_jpeg(snap.jpeg_b64)
        if img is None:
            return None
        dets, ms = self.detector.detect(img)
        return SnapshotCheck(
            drone_id=snap.drone_id,
            snapshot_id=snap.snapshot_id,
            model=self.detector.name,
            verdict=judge(dets, self.threshold),
            threshold=self.threshold,
            detections=dets,
            infer_ms=round(ms, 1),
        )


def describe(check: SnapshotCheck, edge: list[Detection]) -> str:
    """로그 한 줄: 확인됨 smoke 0.87 (엣지 smoke 0.72)"""
    big = ", ".join(f"{d.cls} {d.conf:.2f}" for d in check.detections[:3]) or "없음"
    small = ", ".join(f"{d.cls} {d.conf:.2f}" for d in edge[:3]) or "없음"
    word = "확인됨" if check.verdict == "confirmed" else "오탐 의심"
    return f"{word}: {check.model} {big} (엣지 {small}, {check.infer_ms:.0f} ms)"
