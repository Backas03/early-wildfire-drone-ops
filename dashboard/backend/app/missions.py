"""미션 REST API.

GET    /api/missions                    저장된 미션 목록
POST   /api/missions                    새로 저장 (mission_id는 서버가 정함)
GET    /api/missions/{id}               미션 하나
PUT    /api/missions/{id}               덮어쓰기
DELETE /api/missions/{id}               삭제
POST   /api/missions/waypoints/import   .waypoints 텍스트 → MissionPlan (저장하지 않음)
POST   /api/missions/waypoints/export   MissionPlan → .waypoints 텍스트

검증은 shared/protocol의 MissionPlan이 한다 (고도 150m, 위치 누락 등). 틀리면 422.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse
from protocol import MissionPlan, WaypointsFileError, dump_waypoints, load_waypoints
from pydantic import BaseModel, Field

from .store import MissionStore, MissionSummary, get_store

router = APIRouter(prefix="/api/missions", tags=["missions"])


class WaypointsImport(BaseModel):
    name: str = Field(default="가져온 미션", max_length=200)
    text: str = Field(max_length=2_000_000)


@router.post("/waypoints/import")
def import_waypoints(body: WaypointsImport) -> MissionPlan:
    try:
        return load_waypoints(body.text, body.name.strip())
    except WaypointsFileError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.post("/waypoints/export", response_class=PlainTextResponse)
def export_waypoints(plan: MissionPlan) -> str:
    return dump_waypoints(plan)


@router.get("")
def list_missions(store: MissionStore = Depends(get_store)) -> list[MissionSummary]:
    return store.list()


@router.post("", status_code=201)
def create_mission(plan: MissionPlan, store: MissionStore = Depends(get_store)) -> MissionPlan:
    return store.create(plan)


@router.get("/{mission_id}")
def get_mission(mission_id: str, store: MissionStore = Depends(get_store)) -> MissionPlan:
    plan = store.get(mission_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="미션을 찾을 수 없음")
    return plan


@router.put("/{mission_id}")
def update_mission(mission_id: str, plan: MissionPlan, store: MissionStore = Depends(get_store)) -> MissionPlan:
    saved = store.update(mission_id, plan)
    if saved is None:
        raise HTTPException(status_code=404, detail="미션을 찾을 수 없음")
    return saved


@router.delete("/{mission_id}", status_code=204)
def delete_mission(mission_id: str, store: MissionStore = Depends(get_store)) -> Response:
    if not store.delete(mission_id):
        raise HTTPException(status_code=404, detail="미션을 찾을 수 없음")
    return Response(status_code=204)
