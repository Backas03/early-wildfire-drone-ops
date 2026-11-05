"""장소 검색: 지도에서 '대구대학교', '경산시 진량읍' 같은 이름으로 바로 이동할 때 쓴다.

- GET /api/places/search?q=검색어 → 후보 최대 5개 [{name, detail, lat, lon, bbox}]

OpenStreetMap Nominatim(https://nominatim.org)에 이 서버가 대신 묻는다. 이용 규칙에 맞춰
앱 이름을 User-Agent로 밝히고, 초당 1번을 넘지 않게 기다리고, 같은 검색어는 기억해 둔다.
화면에서는 Enter를 눌렀을 때만 묻는다 (글자마다 묻는 자동완성은 규칙 위반).
좌표(위도, 경도)는 화면에서 바로 처리하므로 여기까지 오지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

log = logging.getLogger("dashboard-api.places")
router = APIRouter(prefix="/api/places", tags=["places"])

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "early-wildfire-drone-ops/0.3 (+https://github.com/Backas03/early-wildfire-drone-ops)"
CACHE_SIZE = 200


class Place(BaseModel):
    name: str
    detail: str
    lat: float
    lon: float
    bbox: tuple[float, float, float, float] | None = None  # 남, 서, 북, 동


def fetch_nominatim(q: str) -> list[dict]:
    """Nominatim 원본 응답. 한국 안에서, 한국어 이름으로."""
    params = {"q": q, "format": "jsonv2", "limit": 5, "countrycodes": "kr", "accept-language": "ko"}
    req = urllib.request.Request(f"{NOMINATIM}?{urllib.parse.urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=8) as res:
        return json.load(res)


def to_place(raw: dict) -> Place | None:
    """Nominatim 항목 하나 → Place. 위치가 없는 항목은 버린다."""
    try:
        lat, lon = float(raw["lat"]), float(raw["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    detail = str(raw.get("display_name") or "")
    name = str(raw.get("name") or detail.split(",")[0]).strip() or f"{lat:.5f}, {lon:.5f}"
    bbox = None
    bb = raw.get("boundingbox")  # [남, 북, 서, 동] 문자열
    if isinstance(bb, list) and len(bb) == 4:
        try:
            s, n, w, e = (float(v) for v in bb)
            bbox = (s, w, n, e)
        except (TypeError, ValueError):
            pass
    return Place(name=name, detail=detail, lat=lat, lon=lon, bbox=bbox)


class PlaceSearcher:
    def __init__(self, fetch: Callable[[str], list[dict]] = fetch_nominatim, min_interval_s: float = 1.0):
        self.fetch = fetch
        self.min_interval_s = min_interval_s
        self.cache: OrderedDict[str, list[Place]] = OrderedDict()
        self.lock = asyncio.Lock()
        self.last = -1e9

    async def search(self, q: str) -> list[Place]:
        key = " ".join(q.split()).lower()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        async with self.lock:  # 한 번에 하나씩, 초당 1번까지
            if key in self.cache:
                return self.cache[key]
            wait = self.last + self.min_interval_s - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                raw = await asyncio.to_thread(self.fetch, q)
            finally:
                self.last = time.monotonic()
        places = [p for p in map(to_place, raw if isinstance(raw, list) else []) if p]
        self.cache[key] = places  # 실패(예외)는 기억하지 않는다
        while len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        return places


_searcher = PlaceSearcher()


def get_searcher() -> PlaceSearcher:
    return _searcher


@router.get("/search", response_model=list[Place])
async def search(q: str = Query(max_length=200), searcher: PlaceSearcher = Depends(get_searcher)):
    q = q.strip()
    if not q:
        raise HTTPException(422, "검색어를 입력하세요")
    try:
        return await searcher.search(q)
    except (OSError, ValueError) as e:  # 연결 실패·시간 초과(OSError), 이상한 응답(ValueError)
        log.warning("장소 검색 실패 %r: %s", q, e)
        raise HTTPException(502, "장소 검색 서버(OpenStreetMap)에 연결하지 못했어요. 인터넷 연결을 확인하거나 좌표(위도, 경도)로 입력하세요.") from e
