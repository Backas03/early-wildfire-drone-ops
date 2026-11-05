"""엣지 AI가 보낸 탐지 사진 저장.

drone/{id}/snapshot 메시지의 JPEG를 media/snapshots/{snapshot_id}.jpg 로 저장하고,
브라우저에는 사진 대신 주소(url)를 넘긴다. 저장된 사진은 /media/... 로 볼 수 있고,
나중에 서버 재확인(큰 모델)과 이벤트 서버가 같은 파일을 쓴다.
"""

import base64
import binascii
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from protocol import Snapshot

log = logging.getLogger("dashboard-api.media")

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", Path(__file__).resolve().parents[3] / "media"))
SNAPSHOT_DIR = MEDIA_DIR / "snapshots"
MAX_JPEG_BYTES = 5 * 1024 * 1024


def save_snapshot(data: object, directory: Path | None = None) -> dict | None:
    """스냅샷 메시지 → 파일 저장 후 브라우저용 메시지 (jpeg_b64 대신 url). 잘못된 메시지면 None."""
    directory = directory or SNAPSHOT_DIR
    try:
        snap = Snapshot.model_validate(data)
        raw = base64.b64decode(snap.jpeg_b64, validate=True)
    except (ValidationError, binascii.Error, ValueError) as e:
        log.warning("잘못된 스냅샷 무시: %s", str(e)[:200])
        return None
    if not raw.startswith(b"\xff\xd8") or len(raw) > MAX_JPEG_BYTES:
        log.warning("JPEG가 아니거나 너무 큰 스냅샷 무시: %s (%d bytes)", snap.snapshot_id, len(raw))
        return None
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{snap.snapshot_id}.jpg").write_bytes(raw)  # snapshot_id는 영문·숫자·-_ 만 허용됨
    out = snap.model_dump(exclude={"jpeg_b64"})
    out["url"] = f"/media/snapshots/{snap.snapshot_id}.jpg"
    return out
