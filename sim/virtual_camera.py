"""가상 카메라: 드론이 내려다보는 장면을 위성사진 + 가상 화재로 그린다 (시뮬레이션 전용).

라즈베리파이 카메라 모듈 3 대신 쓴다. 드론의 위치·고도·기수 방향(텔레메트리)으로 카메라가 볼 땅을
계산하고, 위성사진(Esri World Imagery)을 원근 변환으로 그린 뒤, 시나리오의 가상 화재(불꽃·그을린 땅·연기)를 얹는다.
화재의 정답 위치를 알고 있으므로 탐지 위치가 얼마나 맞는지 잴 수 있다.

  # 한 장 그려 보기 (드론 위치·고도·방향을 직접 준다)
  python sim/virtual_camera.py --lat 35.8968 --lon 128.8507 --alt 40 --heading 0 --out frame.jpg

좌표계: 드론 기준 동(E)·북(N)·위(U) m. 카메라는 기수 방향을 보고, tilt 0 = 바로 아래, 30 = 앞쪽으로 30° 기울임.
위성 타일은 sim/cache/tiles/ 에 저장해 두고 다시 쓴다.
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.request
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TILE_CACHE = ROOT / "cache" / "tiles"
EARTH_CIRC = 40075016.686  # 적도 둘레 m (웹 메르카토르)
MAX_ZOOM = 19  # 이 지역 Esri 위성사진의 최대 줌 (20은 "Map data not yet available")
MAX_TILES = 64


# ---------------------------------------------------------------------------
# 카메라·자세
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CameraSpec:
    """라즈베리파이 카메라 모듈 3 (표준 렌즈) 기본값."""

    width: int = 1280
    height: int = 720
    hfov_deg: float = 66.0
    vfov_deg: float = 41.0
    tilt_deg: float = 0.0  # 0 = 바로 아래, 양수 = 앞쪽으로 기울임

    @property
    def fx(self) -> float:
        return (self.width / 2) / math.tan(math.radians(self.hfov_deg / 2))

    @property
    def fy(self) -> float:
        return (self.height / 2) / math.tan(math.radians(self.vfov_deg / 2))


@dataclass(frozen=True)
class Pose:
    lat: float
    lon: float
    alt: float  # 홈(지면) 기준 고도 m. 지면은 평평하다고 본다
    heading: float  # 북 0, 시계 방향 deg


def _basis(pose: Pose, cam: CameraSpec):
    """카메라 축(a), 화면 오른쪽(r), 화면 위쪽(u) 단위 벡터 (E, N, U)."""
    h, t = math.radians(pose.heading), math.radians(cam.tilt_deg)
    fwd = np.array([math.sin(h), math.cos(h), 0.0])
    right = np.array([math.cos(h), -math.sin(h), 0.0])
    up = np.array([0.0, 0.0, 1.0])
    axis = math.sin(t) * fwd - math.cos(t) * up
    img_up = math.cos(t) * fwd + math.sin(t) * up
    return axis, right, img_up


def pixel_to_ground(px: float, py: float, pose: Pose, cam: CameraSpec) -> tuple[float, float] | None:
    """화면 점 → 땅 위 점 (드론 기준 동·북 m). 하늘을 보면 None."""
    a, r, u = _basis(pose, cam)
    d = a + ((px - cam.width / 2) / cam.fx) * r + ((cam.height / 2 - py) / cam.fy) * u
    if d[2] >= -1e-6 or pose.alt <= 0:
        return None
    s = pose.alt / -d[2]
    return float(s * d[0]), float(s * d[1])


def ground_to_pixel(east: float, north: float, pose: Pose, cam: CameraSpec) -> tuple[float, float] | None:
    """땅 위 점 (드론 기준 동·북 m) → 화면 점. 카메라 뒤쪽이면 None. 화면 밖 좌표도 돌려준다."""
    a, r, u = _basis(pose, cam)
    p = np.array([east, north, -pose.alt])
    z = float(p @ a)
    if z <= 1e-6:
        return None
    return cam.width / 2 + cam.fx * float(p @ r) / z, cam.height / 2 - cam.fy * float(p @ u) / z


def offset_m(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """(lat0, lon0)에서 (lat, lon)까지 동·북 거리 m (가까운 거리용 근사)."""
    north = math.radians(lat - lat0) * 6371000.0
    east = math.radians(lon - lon0) * 6371000.0 * math.cos(math.radians(lat0))
    return east, north


# ---------------------------------------------------------------------------
# 위성 타일
# ---------------------------------------------------------------------------
def _global_px(lat: float, lon: float, z: int) -> tuple[float, float]:
    """웹 메르카토르 전역 픽셀 좌표 (줌 z, 타일 256px)."""
    n = 256 * 2**z
    x = (lon + 180) / 360 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def _mpp(lat: float, z: int) -> float:
    """줌 z에서 픽셀 하나가 땅에서 몇 m인지."""
    return EARTH_CIRC * math.cos(math.radians(lat)) / (256 * 2**z)


class TileSource:
    """위성 타일을 받아 디스크에 저장해 두고 쓴다. 못 받으면 회녹색 타일로 대신한다."""

    def __init__(self, url: str = TILE_URL, cache_dir: Path = TILE_CACHE, offline: bool = False):
        self.url = url
        self.cache_dir = cache_dir
        self.offline = offline
        self.memory: dict[tuple[int, int, int], np.ndarray | None] = {}

    def get(self, z: int, x: int, y: int) -> np.ndarray | None:
        """타일 (256x256 BGR). 데이터가 없는 지역·실패는 None."""
        key = (z, x, y)
        if key in self.memory:
            return self.memory[key]
        path = self.cache_dir / str(z) / str(x) / f"{y}.jpg"
        img = cv2.imread(str(path)) if path.exists() else None
        if img is None and not self.offline:
            try:
                req = urllib.request.Request(self.url.format(z=z, x=x, y=y), headers={"User-Agent": "fire-drone-sim/0.1"})
                data = urllib.request.urlopen(req, timeout=8).read()
                img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if img is not None and img.std() < 10:  # "Map data not yet available" 회색 타일
                    img = None
                if img is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(path), img)
            except OSError:
                img = None
        if len(self.memory) > 400:
            self.memory.clear()
        self.memory[key] = img
        return img


FILL = (88, 104, 96)  # 타일이 없을 때 땅 색 (BGR)


# ---------------------------------------------------------------------------
# 가상 화재
# ---------------------------------------------------------------------------
@dataclass
class Fire:
    fire_id: str
    lat: float
    lon: float
    size_m: float = 6.0
    smoke: bool = True


@dataclass
class Scenario:
    name: str
    fires: list[Fire] = field(default_factory=list)
    wind_from_deg: float = 225.0  # 바람이 불어오는 방향 (연기는 반대로 흐름)

    @staticmethod
    def load(path: Path) -> Scenario:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return Scenario(
            name=d.get("name", Path(path).stem),
            fires=[Fire(**f) for f in d.get("fires", [])],
            wind_from_deg=float(d.get("wind_from_deg", 225.0)),
        )


def _noise(size: int, rng: np.random.Generator, octaves: int = 4) -> np.ndarray:
    """0~1 부드러운 잡음 (여러 크기 잡음을 겹침)."""
    out = np.zeros((size, size), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = 2 ** (o + 2)
        small = rng.random((cells, cells)).astype(np.float32)
        out += amp * cv2.resize(small, (size, size), interpolation=cv2.INTER_CUBIC)
        total += amp
        amp *= 0.5
    out /= total
    return np.clip((out - out.min()) / (np.ptp(out) + 1e-6), 0, 1)


def _blob(size: int, cx: float, cy: float, rx: float, ry: float) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) / size
    return np.exp(-(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2))


def fire_sprite(seed: int, t: float, size: int = 256) -> np.ndarray:
    """위에서 본 불: 그을린 땅 + 불꽃 덩어리. BGRA float 0~1, 스프라이트 한 변 = 화재 지름의 2배."""
    rng = np.random.default_rng(seed)
    flicker = np.random.default_rng(seed * 7919 + int(t * 4))  # 초당 4번 바뀌는 일렁임
    n = _noise(size, rng, 5)
    # 그을린 땅: 가장자리가 들쭉날쭉한 검은 얼룩
    scorch = np.clip(_blob(size, 0.5, 0.5, 0.42, 0.38) * (0.6 + 0.8 * n) - 0.35, 0, 1) * 1.6
    # 불꽃: 여러 덩어리 + 일렁이는 잡음
    heat = np.zeros((size, size), np.float32)
    for _ in range(6):
        cx, cy = 0.5 + rng.normal(0, 0.09, 2)
        r = rng.uniform(0.06, 0.14)
        heat += _blob(size, cx, cy, r, r * rng.uniform(0.7, 1.3))
    heat *= 0.55 + 0.9 * _noise(size, flicker, 3)
    heat = np.clip(heat / max(heat.max(), 1e-6) * 1.25 - 0.15, 0, 1)

    bgra = np.zeros((size, size, 4), np.float32)
    # 색: 붉은 테두리 → 주황 → 노랑 → 흰 노랑 (BGR)
    stops = np.array([[0.05, 0.10, 0.55], [0.05, 0.45, 0.98], [0.30, 0.80, 1.00], [0.80, 0.97, 1.00]], np.float32)
    idx = np.clip(heat * 3, 0, 2.999)
    i0 = idx.astype(int)
    frac = (idx - i0)[..., None]
    color = stops[i0] * (1 - frac) + stops[i0 + 1] * frac
    a_fire = np.clip(heat * 2.2, 0, 1)
    a_scorch = np.clip(scorch, 0, 0.85)
    ground = np.array([0.06, 0.07, 0.08], np.float32)  # 숯 색
    bgra[..., :3] = color * a_fire[..., None] + ground * (1 - a_fire[..., None])
    bgra[..., 3] = np.maximum(a_fire, a_scorch)
    return bgra


def smoke_sprite(seed: int, t: float, w: int = 512, h: int = 192) -> np.ndarray:
    """위에서 본 연기 기둥: 왼쪽(불 쪽)에서 오른쪽(바람 방향)으로 퍼지며 옅어진다. BGRA float."""
    rng = np.random.default_rng(seed + 17)
    drift = t * 0.04  # 연기가 천천히 흘러가는 느낌
    n = _noise(max(w, h), rng, 5)[:h, :w]
    n2 = _noise(max(w, h), np.random.default_rng(seed + 31 + int(t)), 3)[:h, :w]
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    yy = np.linspace(-0.5, 0.5, h, dtype=np.float32)[:, None]
    spread = 0.08 + 0.32 * xx  # 멀어질수록 넓어짐
    core = np.exp(-((yy + 0.04 * np.sin(xx * 9 + drift * 6)) / spread) ** 2)
    along = np.clip(1.15 - xx * 0.95, 0, 1) * np.clip(xx * 14, 0, 1)  # 불 바로 위에서 시작해 점점 옅어짐
    dens = core * along * (0.45 + 0.75 * (0.6 * n + 0.4 * n2))
    alpha = np.clip(dens * 1.05, 0, 0.9)
    shade = 0.70 + 0.22 * n  # 회백색 연기, 군데군데 짙게
    bgra = np.zeros((h, w, 4), np.float32)
    bgra[..., 0] = shade * 0.97
    bgra[..., 1] = shade * 0.98
    bgra[..., 2] = shade
    bgra[..., 3] = alpha
    return bgra


# ---------------------------------------------------------------------------
# 렌더러
# ---------------------------------------------------------------------------
class VirtualCamera:
    FLICKER_VARIANTS = 8  # 불꽃 모양을 미리 몇 가지 만들어 두고 돌려 쓴다 (매번 만들면 느림)

    def __init__(self, scenario: Scenario | None = None, cam: CameraSpec = CameraSpec(), tiles: TileSource | None = None):
        self.cam = cam
        self.scenario = scenario or Scenario("빈 시나리오")
        self.tiles = tiles or TileSource()
        self._sprites: dict[tuple[str, str, int], np.ndarray] = {}

    def _sprite(self, f: Fire, kind: str, t: float) -> np.ndarray:
        variant = int(t * 4) % self.FLICKER_VARIANTS
        key = (f.fire_id, kind, variant)
        if key not in self._sprites:
            seed = zlib.crc32(f.fire_id.encode())  # hash()는 실행마다 달라서 쓰지 않는다
            make = fire_sprite if kind == "fire" else smoke_sprite
            self._sprites[key] = make(seed, variant / 4)
        return self._sprites[key]

    def render(self, pose: Pose, t: float = 0.0) -> np.ndarray:
        # 알려진 한계: 고도 25 m 아래에서는 위성사진(픽셀 하나 ≈ 24 cm)을 크게 늘려 뿌옇게 보이고,
        # AI가 이를 연기로 착각하는 일이 많다 (실제 카메라는 선명함). 땅에 잔무늬를 얹어 보았지만
        # 오탐은 줄어도 진짜 화재 탐지가 절반 아래로 떨어져서 넣지 않았다. 낮은 고도 탐지는 서버에서 거른다.
        frame = self._background(pose)
        for f in self.scenario.fires:
            self._draw_fire(frame, pose, f, t)
        return self._sensor(frame, t)

    # --- 배경 (위성사진) ---------------------------------------------------
    def _corners(self, pose: Pose) -> list[tuple[float, float]]:
        cam = self.cam
        pts = []
        for px, py in ((0, 0), (cam.width, 0), (cam.width, cam.height), (0, cam.height)):
            g = pixel_to_ground(px, py, pose, cam)
            if g is None:  # 하늘이 보이면 수평선 근처까지만 (tilt를 크게 준 경우)
                g = pixel_to_ground(px, cam.height * 0.5 + (py - cam.height * 0.5) * 0.2, pose, cam) or (0.0, 0.0)
            pts.append(g)
        return pts

    def _background(self, pose: Pose) -> np.ndarray:
        cam = self.cam
        corners = self._corners(pose)
        gsd = max(pose.alt, 1.0) / cam.fx  # 화면 가운데에서 픽셀 하나가 땅에서 몇 m
        z = MAX_ZOOM
        while z > 12 and _mpp(pose.lat, z) < gsd * 0.5:  # 너무 높이 날면 줌을 낮춰도 충분
            z -= 1
        while True:
            mpp = _mpp(pose.lat, z)
            gx0, gy0 = _global_px(pose.lat, pose.lon, z)
            gpts = [(gx0 + e / mpp, gy0 - n / mpp) for e, n in corners]
            xs, ys = [p[0] for p in gpts], [p[1] for p in gpts]
            tx0, tx1 = int(min(xs) // 256), int(max(xs) // 256)
            ty0, ty1 = int(min(ys) // 256), int(max(ys) // 256)
            if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= MAX_TILES or z <= 12:
                break
            z -= 1
        mosaic = np.empty(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256, 3), np.uint8)
        mosaic[:] = FILL
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                tile = self.tiles.get(z, tx, ty)
                if tile is None and z > 12:  # 이 줌에 없으면 한 단계 낮은 줌을 늘려서 쓴다
                    parent = self.tiles.get(z - 1, tx // 2, ty // 2)
                    if parent is not None:
                        ox, oy = (tx % 2) * 128, (ty % 2) * 128
                        tile = cv2.resize(parent[oy : oy + 128, ox : ox + 128], (256, 256), interpolation=cv2.INTER_CUBIC)
                if tile is not None:
                    mosaic[(ty - ty0) * 256 : (ty - ty0 + 1) * 256, (tx - tx0) * 256 : (tx - tx0 + 1) * 256] = tile
        src = np.float32([(0, 0), (cam.width, 0), (cam.width, cam.height), (0, cam.height)])
        dst = np.float32([(x - tx0 * 256, y - ty0 * 256) for x, y in gpts])
        h = cv2.getPerspectiveTransform(src, dst)  # 화면 → 모자이크
        return cv2.warpPerspective(
            mosaic, h, (cam.width, cam.height), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderValue=FILL
        )

    # --- 화재 ---------------------------------------------------------------
    def _paste(self, frame: np.ndarray, get_sprite, ground_quad: list[tuple[float, float]], pose: Pose) -> None:
        """스프라이트 네 모서리를 땅 위 네 점(동·북 m)에 맞춰 화면에 얹는다. 화면 안일 때만 get_sprite()를 부른다."""
        img_pts = [ground_to_pixel(e, n, pose, self.cam) for e, n in ground_quad]
        if any(p is None for p in img_pts):
            return
        xs, ys = [p[0] for p in img_pts], [p[1] for p in img_pts]
        w, h = self.cam.width, self.cam.height
        # 스프라이트가 덮는 화면 영역만 계산한다 (전체 화면을 변환하면 느림)
        x0, x1 = max(int(min(xs)) - 1, 0), min(int(max(xs)) + 2, w)
        y0, y1 = max(int(min(ys)) - 1, 0), min(int(max(ys)) + 2, h)
        if x0 >= x1 or y0 >= y1:
            return  # 화면 밖
        sprite = get_sprite()
        sh, sw = sprite.shape[:2]
        src = np.float32([(0, 0), (sw, 0), (sw, sh), (0, sh)])
        m = cv2.getPerspectiveTransform(src, np.float32([(x - x0, y - y0) for x, y in img_pts]))
        warped = cv2.warpPerspective(sprite, m, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))
        a = warped[..., 3:4]
        roi = frame[y0:y1, x0:x1].astype(np.float32)
        frame[y0:y1, x0:x1] = np.clip(roi * (1 - a) + warped[..., :3] * 255 * a, 0, 255).astype(np.uint8)

    def _draw_fire(self, frame: np.ndarray, pose: Pose, f: Fire, t: float) -> None:
        e0, n0 = offset_m(pose.lat, pose.lon, f.lat, f.lon)
        reach = f.size_m * 8 + pose.alt * 3  # 이보다 멀면 그리지 않는다 (연기 길이 포함)
        if math.hypot(e0, n0) > reach + 200:
            return
        r = f.size_m  # 스프라이트 반 변 = 지름 (그을린 땅이 불꽃보다 넓게)
        fire_q = [(e0 - r, n0 + r), (e0 + r, n0 + r), (e0 + r, n0 - r), (e0 - r, n0 - r)]
        self._paste(frame, lambda: self._sprite(f, "fire", t), fire_q, pose)
        if f.smoke:
            # 연기: 불에서 바람 부는 쪽으로 지름의 8배 길이
            to = math.radians(self.scenario.wind_from_deg + 180)
            ux, uy = math.sin(to), math.cos(to)  # 흐르는 방향 (동, 북)
            vx, vy = uy, -ux  # 그에 수직
            length, half_w = f.size_m * 8, f.size_m * 1.6
            start = -f.size_m * 0.3
            quad = [
                (e0 + ux * start - vx * half_w, n0 + uy * start - vy * half_w),
                (e0 + ux * (start + length) - vx * half_w, n0 + uy * (start + length) - vy * half_w),
                (e0 + ux * (start + length) + vx * half_w, n0 + uy * (start + length) + vy * half_w),
                (e0 + ux * start + vx * half_w, n0 + uy * start + vy * half_w),
            ]
            self._paste(frame, lambda: self._sprite(f, "smoke", t), quad, pose)

    # --- 센서 느낌 ----------------------------------------------------------
    @staticmethod
    def _sensor(frame: np.ndarray, t: float) -> np.ndarray:
        """위성사진을 늘린 흐릿함을 조금 감추는 샤프닝 + 센서 잡음."""
        blur = cv2.GaussianBlur(frame, (0, 0), 1.2)
        sharp = cv2.addWeighted(frame, 1.5, blur, -0.5, 0)
        noise = np.empty(frame.shape, np.int16)
        cv2.randn(noise, 0, 3)
        return cv2.add(sharp.astype(np.int16), noise).clip(0, 255).astype(np.uint8)


def fires_in_view(scenario: Scenario, pose: Pose, cam: CameraSpec) -> list[tuple[Fire, tuple[float, float]]]:
    """화면 안에 들어온 화재와 그 화면 좌표 (탐지 정확도 채점용)."""
    out = []
    for f in scenario.fires:
        e, n = offset_m(pose.lat, pose.lon, f.lat, f.lon)
        p = ground_to_pixel(e, n, pose, cam)
        if p and 0 <= p[0] < cam.width and 0 <= p[1] < cam.height:
            out.append((f, p))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--alt", type=float, default=40)
    ap.add_argument("--heading", type=float, default=0)
    ap.add_argument("--tilt", type=float, default=0, help="카메라 앞쪽 기울기 deg (0 = 바로 아래)")
    ap.add_argument("--scenario", default=str(ROOT / "scenarios" / "campus.json"))
    ap.add_argument("--t", type=float, default=0, help="시각 (불꽃 일렁임)")
    ap.add_argument("--out", default="frame.jpg")
    args = ap.parse_args()

    cam = VirtualCamera(Scenario.load(Path(args.scenario)), CameraSpec(tilt_deg=args.tilt))
    pose = Pose(args.lat, args.lon, args.alt, args.heading)
    cv2.imwrite(args.out, cam.render(pose, args.t))
    for f, (px, py) in fires_in_view(cam.scenario, pose, cam.cam):
        print(f"화면 안 화재 {f.fire_id}: ({px:.0f}, {py:.0f}) px")
    print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
