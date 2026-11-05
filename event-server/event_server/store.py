"""화재 이벤트 저장소 (SQLite).

이벤트는 FireEvent JSON을 그대로 한 칸에 저장한다. 다시 켜면 최근 이벤트를 불러와
같은 곳의 탐지를 이어 붙이고, 대시보드 목록(events/recent)도 이어서 보여 준다.
DB 파일 위치: 환경 변수 EVENT_DB, 없으면 event-server/data/events.db
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from protocol import FireEvent

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "events.db"


def default_path() -> Path:
    return Path(os.getenv("EVENT_DB", DEFAULT_DB))


class EventStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn, conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    event_id   TEXT PRIMARY KEY,
                    status     TEXT NOT NULL,
                    first_seen INTEGER NOT NULL,
                    last_seen  INTEGER NOT NULL,
                    event_json TEXT NOT NULL
                )"""
            )

    def _connect(self) -> closing[sqlite3.Connection]:
        return closing(sqlite3.connect(self.path))

    def save(self, ev: FireEvent) -> None:
        with self._connect() as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?)",
                (ev.event_id, ev.status, ev.first_seen, ev.last_seen, ev.model_dump_json()),
            )

    def recent(self, limit: int = 200) -> list[FireEvent]:
        with self._connect() as conn:
            rows = conn.execute("SELECT event_json FROM events ORDER BY last_seen DESC LIMIT ?", (limit,)).fetchall()
        return [FireEvent.model_validate_json(r[0]) for r in rows]
