"""대시보드 API 서버.

- GET  /api/health    : 서버·MQTT 연결 상태
- /api/missions/...   : 미션 저장·불러오기, .waypoints 입출력 (missions.py)
- POST /api/drones/{id}/commands : 드론에 명령 (미션 업로드, 모드 변경 등. drones.py)
- GET  /api/places/search?q=... : 지도에서 장소 이름으로 이동 (OpenStreetMap 검색. places.py)
- GET  /media/...     : 엣지 AI가 보낸 탐지 사진 (media.py가 MQTT에서 받아 저장)
- WS   /ws            : MQTT 메시지(텔레메트리, 상태, ack, 드론 미션, FC 문장, 엣지 AI 탐지·사진, 이벤트)를 브라우저로 전달

실행 (저장소 루트에서):  uvicorn app.main:app --app-dir dashboard/backend --reload
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles

from . import drones, events, missions, places
from .media import MEDIA_DIR
from .mqtt_bridge import get_bridge

log = logging.getLogger("dashboard-api")
bridge = get_bridge()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bridge.start(asyncio.get_running_loop())
    yield
    bridge.stop()


app = FastAPI(title="산불 탐지 드론 대시보드 API", version="0.3.0", lifespan=lifespan)
app.include_router(missions.router)
app.include_router(drones.router)
app.include_router(events.router)
app.include_router(places.router)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


@app.get("/api/health")
def health():
    return {"ok": True, "mqtt_connected": bridge.connected}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    queue = bridge.subscribe()

    async def pump():
        # 접속 직후 retained 상태(드론·서버 온라인 여부)를 먼저 보낸다
        for topic, payload in bridge.snapshot():
            await websocket.send_text(json.dumps({"topic": topic, "data": payload}, ensure_ascii=False))
        while True:
            topic, payload = await queue.get()
            await websocket.send_text(json.dumps({"topic": topic, "data": payload}, ensure_ascii=False))

    async def wait_disconnect():
        # 브라우저는 보내는 게 없지만, 끊김(또는 서버 종료)을 바로 알아채려고 계속 읽는다.
        # 읽지 않으면 브라우저가 떠난 뒤에도 다음 MQTT 메시지를 보내 볼 때까지 연결이 남는다.
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    tasks = [asyncio.create_task(pump()), asyncio.create_task(wait_disconnect())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        bridge.unsubscribe(queue)
