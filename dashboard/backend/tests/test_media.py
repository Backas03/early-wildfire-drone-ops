"""엣지 AI 탐지 사진 저장."""

import base64

import cv2
import numpy as np

from app.media import save_snapshot


def snapshot(**over):
    ok, buf = cv2.imencode(".jpg", np.full((72, 128, 3), 120, np.uint8))
    msg = {
        "drone_id": "drone1",
        "snapshot_id": "drone1-1761552000400-ab12cd",
        "frame_ts": 1761552000400,
        "width": 128,
        "height": 72,
        "model": "fire_n",
        "detections": [{"cls": "fire", "conf": 0.7, "bbox": [0.4, 0.4, 0.5, 0.5]}],
        "jpeg_b64": base64.b64encode(buf.tobytes()).decode(),
    }
    return {**msg, **over}


def test_saves_jpeg_and_returns_url(tmp_path):
    out = save_snapshot(snapshot(), tmp_path)
    assert out["url"] == "/media/snapshots/drone1-1761552000400-ab12cd.jpg"
    assert "jpeg_b64" not in out and out["detections"][0]["cls"] == "fire"
    img = cv2.imread(str(tmp_path / "drone1-1761552000400-ab12cd.jpg"))
    assert img.shape == (72, 128, 3)


def test_rejects_bad_snapshots(tmp_path):
    assert save_snapshot(snapshot(snapshot_id="../../evil"), tmp_path) is None  # 경로 탈출
    assert save_snapshot(snapshot(jpeg_b64="!!!not base64"), tmp_path) is None
    assert save_snapshot(snapshot(jpeg_b64=base64.b64encode(b"GIF89a....").decode()), tmp_path) is None
    assert save_snapshot({"drone_id": "drone1"}, tmp_path) is None
    assert list(tmp_path.iterdir()) == []
