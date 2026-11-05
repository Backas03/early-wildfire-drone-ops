"""가상 카메라의 기하(땅 ⇄ 화면)와 화재 그리기. 위성 타일은 받지 않는다 (offline)."""

import math

import numpy as np
import pytest

import virtual_camera as vc

CAM = vc.CameraSpec()


def test_straight_down_heading_north():
    pose = vc.Pose(35.9, 128.85, 40, 0)
    cx, cy = CAM.width / 2, CAM.height / 2
    assert vc.ground_to_pixel(0, 0, pose, CAM) == pytest.approx((cx, cy))
    px, py = vc.ground_to_pixel(0, 10, pose, CAM)  # 북쪽 10 m → 화면 위쪽
    assert px == pytest.approx(cx) and py < cy
    px, py = vc.ground_to_pixel(10, 0, pose, CAM)  # 동쪽 10 m → 화면 오른쪽
    assert px > cx and py == pytest.approx(cy)
    # 화면 폭 = 2 · 고도 · tan(66°/2) ≈ 51.9 m
    east, north = vc.pixel_to_ground(0, cy, pose, CAM)
    assert east == pytest.approx(-40 * math.tan(math.radians(33)), rel=1e-6) and north == pytest.approx(0, abs=1e-9)


def test_heading_rotates_view():
    pose = vc.Pose(35.9, 128.85, 40, 90)  # 동쪽을 봄
    cx, cy = CAM.width / 2, CAM.height / 2
    px, py = vc.ground_to_pixel(10, 0, pose, CAM)  # 앞(동쪽) → 화면 위
    assert px == pytest.approx(cx) and py < cy
    px, py = vc.ground_to_pixel(0, 10, pose, CAM)  # 북쪽 = 왼쪽
    assert px < cx


def test_tilted_camera_round_trip():
    cam = vc.CameraSpec(tilt_deg=30)
    pose = vc.Pose(35.9, 128.85, 50, 137)
    rng = np.random.default_rng(1)
    for px, py in rng.uniform([0, 0], [cam.width, cam.height], (20, 2)):
        g = vc.pixel_to_ground(px, py, pose, cam)
        assert g is not None
        assert vc.ground_to_pixel(*g, pose, cam) == pytest.approx((px, py), abs=1e-6)
    # 기울이면 화면 가운데는 드론 앞쪽 땅 (50 · tan30 ≈ 28.9 m)
    e, n = vc.pixel_to_ground(cam.width / 2, cam.height / 2, pose, cam)
    assert math.hypot(e, n) == pytest.approx(50 * math.tan(math.radians(30)))


def test_render_draws_fire_where_it_is(tmp_path):
    fire = vc.Fire("A", 35.9, 128.85, size_m=6)
    cam = vc.VirtualCamera(
        vc.Scenario("t", [fire], wind_from_deg=225), vc.CameraSpec(), vc.TileSource(cache_dir=tmp_path, offline=True)
    )
    img = cam.render(vc.Pose(35.9, 128.85, 40, 0), t=0.5)
    assert img.shape == (720, 1280, 3)
    b, g, r = (img[340:380, 620:660].astype(float).mean(axis=(0, 1)))
    assert r > g > b and r > 150  # 가운데에 불꽃 색
    # 연기는 바람(남서풍)을 따라 북동쪽 = 화면 오른쪽 위로 흐른다
    upper_right = img[150:330, 680:900].astype(float).mean()
    lower_left = img[390:570, 380:600].astype(float).mean()
    assert upper_right > lower_left + 20
    # 정답 위치 채점용
    (f, (px, py)), = vc.fires_in_view(cam.scenario, vc.Pose(35.9, 128.85, 40, 0), cam.cam)
    assert f.fire_id == "A" and (px, py) == pytest.approx((640, 360))


def test_fire_out_of_view_is_not_drawn(tmp_path):
    fire = vc.Fire("far", 35.91, 128.85)  # 1 km 북쪽
    cam = vc.VirtualCamera(vc.Scenario("t", [fire]), vc.CameraSpec(), vc.TileSource(cache_dir=tmp_path, offline=True))
    img = cam.render(vc.Pose(35.9, 128.85, 40, 0))
    assert abs(img.astype(float).mean() - np.mean(vc.FILL)) < 3
    assert vc.fires_in_view(cam.scenario, vc.Pose(35.9, 128.85, 40, 0), cam.cam) == []
