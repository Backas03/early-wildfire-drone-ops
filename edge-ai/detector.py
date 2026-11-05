"""YOLOv8 불·연기 탐지 (AlimTleuliyev/wildfire-detection 학습 모델).

PC에서는 .pt를 그대로, 라즈베리파이에서는 NCNN으로 바꾼 폴더(models/fire_n_ncnn_model)를 쓴다.
모델 안 클래스 번호는 0 = smoke, 1 = fire 이지만 번호가 아니라 이름으로 연결한다.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from protocol import Detection

KNOWN = {"fire", "smoke"}


def model_name(path: str | Path) -> str:
    """models/fire_n.pt → fire_n, models/fire_n_ncnn_model → fire_n"""
    stem = Path(path).name
    for suffix in (".pt", "_ncnn_model", "_openvino_model", ".onnx"):
        stem = stem.removesuffix(suffix)
    return stem


def to_detections(
    xyxyn: Iterable[Iterable[float]], cls: Iterable[float], conf: Iterable[float], names: dict[int, str]
) -> list[Detection]:
    """YOLO 결과 → 프로토콜 Detection (fire·smoke만, 신뢰도 높은 순, bbox는 0~1)."""
    out = []
    for box, c, p in zip(xyxyn, cls, conf):
        name = str(names.get(int(c), "")).lower()
        if name not in KNOWN:
            continue
        x1, y1, x2, y2 = (min(max(float(v), 0.0), 1.0) for v in box)
        if x2 <= x1 or y2 <= y1:
            continue
        out.append(Detection(cls=name, conf=round(float(p), 3), bbox=(round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4))))
    return sorted(out, key=lambda d: -d.conf)


class Detector:
    def __init__(self, model_path: str, imgsz: int = 640, conf: float = 0.25):
        from ultralytics import YOLO  # 무거우므로 실제로 쓸 때만 불러온다

        self.model = YOLO(model_path, task="detect")
        self.name = model_name(model_path)
        self.imgsz = imgsz
        self.conf = conf
        self.names = {int(k): v for k, v in self.model.names.items()}

    def detect(self, frame) -> tuple[list[Detection], float]:
        """(탐지 목록, 걸린 시간 ms)"""
        t0 = time.perf_counter()
        r = self.model(frame, imgsz=self.imgsz, conf=self.conf, verbose=False)[0]
        ms = (time.perf_counter() - t0) * 1000
        b = r.boxes
        return to_detections(b.xyxyn.tolist(), b.cls.tolist(), b.conf.tolist(), self.names), ms
