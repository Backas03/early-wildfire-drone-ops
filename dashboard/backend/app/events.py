"""화재 이벤트 API.

POST /api/events/{event_id}/status
  body: {"status": "acknowledged" | "resolved" | "false_alarm" | "new"}
  → 이벤트 서버에 events/cmd로 보내고, 바뀐 이벤트(events의 updated)를 기다려 돌려준다.
    409 이벤트 서버 꺼짐, 504 이벤트 서버 응답 없음(없는 이벤트도 여기에 해당)

이벤트 목록은 API가 따로 주지 않는다. 이벤트 서버가 events/recent(retained)로 발행하는 것을
/ws로 그대로 넘긴다 (새로 연 브라우저에도 먼저 보낸다).
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from protocol import EventStatus, EventStatusCmd, topics

from .mqtt_bridge import MqttBridge, get_bridge

log = logging.getLogger("dashboard-api.events")
router = APIRouter(prefix="/api/events", tags=["events"])

REPLY_TIMEOUT_S = 5.0


class StatusBody(BaseModel):
    status: EventStatus


@router.post("/{event_id}/status")
async def set_status(event_id: str, body: StatusBody, bridge: MqttBridge = Depends(get_bridge)) -> dict:
    if not bridge.server_online("event-server"):
        raise HTTPException(409, "이벤트 서버가 꺼져 있어요")
    updates = bridge.watch_event(event_id)
    try:
        bridge.publish(topics.EVENTS_CMD, EventStatusCmd(event_id=event_id, status=body.status).model_dump_json(), qos=1)
        log.info("이벤트 %s → %s", event_id, body.status)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REPLY_TIMEOUT_S
        while True:
            try:
                ev = await asyncio.wait_for(updates.get(), max(deadline - loop.time(), 0.01))
            except TimeoutError:
                raise HTTPException(504, "이벤트 서버가 응답하지 않아요 (없는 이벤트일 수도 있어요)") from None
            if ev.get("status") == body.status:  # 그사이 탐지로 온 updated는 건너뛴다
                return ev
    finally:
        bridge.unwatch_event(event_id)
