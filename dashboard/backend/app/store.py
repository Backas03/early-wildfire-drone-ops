"""미션 저장소 (SQLite).

미션은 MissionPlan JSON을 그대로 한 칸에 저장한다. 미션 수가 많지 않아서 이걸로 충분하다.
DB 파일 위치: 환경 변수 DASHBOARD_DB, 없으면 dashboard/backend/data/dashboard.db
"""

import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from protocol import MissionPlan, now_ms
from pydantic import BaseModel

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "dashboard.db"


class MissionSummary(BaseModel):
    mission_id: str
    name: str
    item_count: int
    waypoint_count: int
    created_at: int
    updated_at: int


class MissionStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn, conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    name       TEXT NOT NULL,
                    plan_json  TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )"""
            )

    def _connect(self) -> closing[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    def list(self) -> list[MissionSummary]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM missions ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            plan = MissionPlan.model_validate_json(r["plan_json"])
            out.append(
                MissionSummary(
                    mission_id=r["mission_id"],
                    name=r["name"],
                    item_count=len(plan.items),
                    waypoint_count=sum(it.command in ("waypoint", "loiter_time") for it in plan.items),
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
            )
        return out

    def get(self, mission_id: str) -> MissionPlan | None:
        with self._connect() as conn:
            row = conn.execute("SELECT plan_json FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
        return MissionPlan.model_validate_json(row["plan_json"]) if row else None

    def create(self, plan: MissionPlan) -> MissionPlan:
        saved = plan.model_copy(update={"mission_id": uuid.uuid4().hex})
        ts = now_ms()
        with self._connect() as conn, conn:
            conn.execute(
                "INSERT INTO missions VALUES (?, ?, ?, ?, ?)",
                (saved.mission_id, saved.name, saved.model_dump_json(), ts, ts),
            )
        return saved

    def update(self, mission_id: str, plan: MissionPlan) -> MissionPlan | None:
        saved = plan.model_copy(update={"mission_id": mission_id})
        with self._connect() as conn, conn:
            cur = conn.execute(
                "UPDATE missions SET name = ?, plan_json = ?, updated_at = ? WHERE mission_id = ?",
                (saved.name, saved.model_dump_json(), now_ms(), mission_id),
            )
        return saved if cur.rowcount else None

    def delete(self, mission_id: str) -> bool:
        with self._connect() as conn, conn:
            cur = conn.execute("DELETE FROM missions WHERE mission_id = ?", (mission_id,))
        return cur.rowcount > 0


_store: MissionStore | None = None


def get_store() -> MissionStore:
    """FastAPI 의존성. 테스트에서는 app.dependency_overrides로 바꾼다."""
    global _store
    if _store is None:
        _store = MissionStore(os.environ.get("DASHBOARD_DB", DEFAULT_DB))
    return _store
