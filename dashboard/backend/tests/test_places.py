"""장소 검색: Nominatim 응답을 바꿔 끼워서 변환·기억·실패 처리를 확인한다 (인터넷 없이 돈다)."""

import urllib.error

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.places import PlaceSearcher, get_searcher

RAW = [
    {
        "name": "대구대학교",
        "display_name": "대구대학교, 진량읍, 경산시, 경상북도, 대한민국",
        "lat": "35.8961",
        "lon": "128.8510",
        "boundingbox": ["35.89", "35.91", "128.84", "128.86"],
    },
    {"display_name": "대구대로, 진량읍, 경산시", "lat": "35.9", "lon": "128.85"},
    {"display_name": "위치 없는 항목"},
]


def use(fetch):
    searcher = PlaceSearcher(fetch=fetch, min_interval_s=0)
    app.dependency_overrides[get_searcher] = lambda: searcher
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear():
    yield
    app.dependency_overrides.clear()


def test_search_maps_results_and_remembers_query():
    calls = []
    client = use(lambda q: calls.append(q) or RAW)

    r = client.get("/api/places/search", params={"q": " 대구대학교 "})
    assert r.status_code == 200
    first, second = r.json()  # 위치 없는 항목은 버림
    assert first == {
        "name": "대구대학교",
        "detail": "대구대학교, 진량읍, 경산시, 경상북도, 대한민국",
        "lat": 35.8961,
        "lon": 128.851,
        "bbox": [35.89, 128.84, 35.91, 128.86],  # 남, 서, 북, 동
    }
    assert second["name"] == "대구대로" and second["bbox"] is None  # 이름이 없으면 주소 첫 부분

    client.get("/api/places/search", params={"q": "대구대학교"})  # 앞뒤 공백만 다른 같은 검색어
    assert calls == ["대구대학교"]


def test_search_failure_is_502_and_not_remembered():
    calls = []

    def down(q):
        calls.append(q)
        raise urllib.error.URLError("no internet")

    client = use(down)
    r = client.get("/api/places/search", params={"q": "경산시"})
    assert r.status_code == 502 and "좌표" in r.json()["detail"]
    client.get("/api/places/search", params={"q": "경산시"})
    assert len(calls) == 2  # 실패는 기억하지 않고 다시 묻는다


def test_blank_query_is_rejected():
    client = use(lambda q: pytest.fail("빈 검색어로 묻지 않는다"))
    assert client.get("/api/places/search", params={"q": "   "}).status_code == 422
