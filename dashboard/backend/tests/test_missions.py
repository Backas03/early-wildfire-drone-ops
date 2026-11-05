import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import MissionStore, get_store

PLAN = {
    "name": "뒷산 순찰 A",
    "home": {"lat": 35.8964, "lon": 128.8495, "alt_msl": 100},
    "items": [
        {"seq": 0, "command": "takeoff", "alt": 30},
        {"seq": 1, "command": "waypoint", "lat": 35.897, "lon": 128.85, "alt": 40},
        {"seq": 2, "command": "rtl"},
    ],
}


@pytest.fixture
def client(tmp_path):
    store = MissionStore(tmp_path / "test.db")
    app.dependency_overrides[get_store] = lambda: store
    # with 없이 만들면 lifespan(MQTT 연결)이 돌지 않는다
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_crud(client):
    assert client.get("/api/missions").json() == []

    r = client.post("/api/missions", json={**PLAN, "mission_id": "client-id-ignored"})
    assert r.status_code == 201
    saved = r.json()
    mid = saved["mission_id"]
    assert mid and mid != "client-id-ignored"

    listed = client.get("/api/missions").json()
    assert [(m["mission_id"], m["item_count"], m["waypoint_count"]) for m in listed] == [(mid, 3, 1)]

    changed = {**PLAN, "name": "뒷산 순찰 B"}
    assert client.put(f"/api/missions/{mid}", json=changed).json()["name"] == "뒷산 순찰 B"
    assert client.get(f"/api/missions/{mid}").json()["name"] == "뒷산 순찰 B"

    assert client.delete(f"/api/missions/{mid}").status_code == 204
    assert client.get(f"/api/missions/{mid}").status_code == 404
    assert client.put(f"/api/missions/{mid}", json=PLAN).status_code == 404


def test_invalid_mission_rejected(client):
    bad = {**PLAN, "items": [{"seq": 0, "command": "waypoint", "lat": 35.9, "lon": 128.8, "alt": 200}]}
    r = client.post("/api/missions", json=bad)
    assert r.status_code == 422
    assert "150" in r.text
    assert client.get("/api/missions").json() == []


def test_waypoints_export_import(client):
    text = client.post("/api/missions/waypoints/export", json=PLAN).text
    assert text.startswith("QGC WPL 110")

    r = client.post("/api/missions/waypoints/import", json={"name": "가져옴", "text": text})
    assert r.status_code == 200
    plan = r.json()
    assert plan["name"] == "가져옴" and plan["mission_id"] is None
    assert [it["command"] for it in plan["items"]] == ["takeoff", "waypoint", "rtl"]

    r = client.post("/api/missions/waypoints/import", json={"name": "x", "text": "not a file"})
    assert r.status_code == 400
    assert "QGC WPL" in r.json()["detail"]
