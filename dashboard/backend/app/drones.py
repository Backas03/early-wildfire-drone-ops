"""드론 명령 API.

POST /api/drones/{drone_id}/commands
  body: Command (docs/protocol.md 4.3). 예: {"cmd_id": "...", "type": "set_mode", "mode": "RTL"}
        cmd_id는 브라우저가 정한다. 진행률(in_progress ack)은 /ws로 오는 drone/{id}/cmd/ack로 따라간다.
  → 드론의 마지막 ack (status = succeeded | failed)
    409 드론 오프라인·FC 미연결, 504 드론 응답 없음
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from protocol import Command, topics

from .mqtt_bridge import MqttBridge, get_bridge

log = logging.getLogger("dashboard-api.drones")
router = APIRouter(prefix="/api/drones", tags=["drones"])

ACCEPT_TIMEOUT_S = 10.0  # 이 안에 accepted가 안 오면 드론이 응답하지 않는 것
FINISH_TIMEOUT_S = {"mission_upload": 90.0, "mission_download": 90.0, "mission_clear": 20.0, "set_mode": 20.0, "goto": 20.0}


@router.post("/{drone_id}/commands")
async def send_command(drone_id: str, cmd: Command, bridge: MqttBridge = Depends(get_bridge)) -> dict:
    status = bridge.drone_status(drone_id)
    if not status or not status.get("online"):
        raise HTTPException(409, f"{drone_id}가 오프라인이에요")
    if not status.get("fc_connected"):
        raise HTTPException(409, f"{drone_id}의 비행 컨트롤러(FC)가 연결되지 않았어요")

    acks = bridge.watch_acks(cmd.cmd_id)
    try:
        bridge.publish(topics.cmd(drone_id), cmd.model_dump_json(), qos=1)
        log.info("명령 → %s: %s (%s)", drone_id, cmd.type, cmd.cmd_id)
        timeout, accepted = ACCEPT_TIMEOUT_S, False
        while True:
            try:
                ack = await asyncio.wait_for(acks.get(), timeout)
            except TimeoutError:
                if not accepted:
                    raise HTTPException(504, f"{drone_id}가 응답하지 않아요 ({ACCEPT_TIMEOUT_S:.0f}초)") from None
                raise HTTPException(504, f"{drone_id}가 명령을 제시간에 끝내지 못했어요") from None
            if ack.get("status") in ("succeeded", "failed"):
                return ack
            if not accepted:  # accepted 이후로는 명령별 제한 시간
                accepted, timeout = True, FINISH_TIMEOUT_S[cmd.type]
    finally:
        bridge.unwatch_acks(cmd.cmd_id)
