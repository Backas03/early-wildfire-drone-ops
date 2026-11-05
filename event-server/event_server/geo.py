"""탐지 박스 → 땅 위 위치.

땅은 평평하고(홈 고도), 카메라는 드론 앞쪽(heading)을 화면 위로 두고 아래를 본다고 본다.
sim/virtual_camera.py의 pixel_to_ground와 같은 계산이다 (테스트에서 둘이 맞는지 확인한다).
실제 기체에서는 기체 기울기(roll·pitch)만큼 오차가 생긴다. 짐벌이 없으면 수 m~십여 m.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_R = 6_371_000.0


@dataclass(frozen=True)
class Camera:
    """라즈베리파이 카메라 모듈 3 (표준 렌즈) 기본값."""

    hfov_deg: float = 66.0
    vfov_deg: float = 41.0
    tilt_deg: float = 0.0  # 0 = 바로 아래, 양수 = 앞쪽으로 기울임


def ground_offset(u: float, v: float, alt: float, heading: float, cam: Camera = Camera()) -> tuple[float, float] | None:
    """화면 점(u, v: 0~1, 왼쪽 위가 0) → 드론 바로 아래 기준 (동, 북) m. 하늘을 보면 None."""
    if alt <= 0:
        return None
    x = (2 * u - 1) * math.tan(math.radians(cam.hfov_deg / 2))  # 화면 오른쪽 성분
    y = (1 - 2 * v) * math.tan(math.radians(cam.vfov_deg / 2))  # 화면 위쪽 성분
    h, t = math.radians(heading), math.radians(cam.tilt_deg)
    sh, ch, st, ct = math.sin(h), math.cos(h), math.sin(t), math.cos(t)
    # 카메라 축 + x·(화면 오른쪽) + y·(화면 위쪽), (동, 북, 위)
    d_e = st * sh + x * ch + y * ct * sh
    d_n = st * ch - x * sh + y * ct * ch
    d_u = -ct + y * st
    if d_u >= -1e-6:
        return None
    s = alt / -d_u
    return s * d_e, s * d_n


def move(lat: float, lon: float, east: float, north: float) -> tuple[float, float]:
    """(lat, lon)에서 동·북으로 m만큼 옮긴 위치 (가까운 거리용)."""
    return (
        lat + math.degrees(north / EARTH_R),
        lon + math.degrees(east / (EARTH_R * math.cos(math.radians(lat)))),
    )


def offset(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """(lat0, lon0)에서 (lat, lon)까지 (동, 북) m."""
    return (
        math.radians(lon - lon0) * EARTH_R * math.cos(math.radians(lat0)),
        math.radians(lat - lat0) * EARTH_R,
    )


def distance(lat0: float, lon0: float, lat: float, lon: float) -> float:
    e, n = offset(lat0, lon0, lat, lon)
    return math.hypot(e, n)
