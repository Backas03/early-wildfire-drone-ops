"""화재 판정: 탐지 프레임·서버 재확인을 모아 "화재 의심" 이벤트로 만든다.

1. 탐지 프레임(초당 2~5번)의 박스마다 그 시각의 드론 위치로 땅 위 위치를 구한다 (geo.py).
   드론이 낮게 날 때(이착륙 중)의 탐지는 거른다: 가상 카메라가 뿌옇게 그려 연기로 착각하고,
   실제로도 지면 가까이의 먼지·김이 잘못 잡히기 쉽다.
2. 가까운 탐지(radius_m 안)끼리 한 화재 후보로 묶는다.
3. 최근 window_s초 동안 서로 다른 프레임 min_hits장 이상에서 잡혔고, 서버 재확인에서
   확인된 사진이 오탐 의심 사진보다 적지 않으면(1장 이상) 이벤트로 만든다 (status=new).
   재확인을 끄면 프레임 수 기준만 두 배로 높여 쓴다.
4. 이벤트가 된 뒤의 탐지는 그 이벤트를 갱신한다 (위치 평균, 개수, 최대 신뢰도, 대표 사진).
   오탐(false_alarm)으로 표시한 곳의 탐지는 조용히 흡수해서 다시 알리지 않는다.
   종료(resolved)한 곳에서 다시 잡히면 새 이벤트가 된다 (다시 번졌을 수 있다).

시각은 메시지 안의 시각(ms)을 쓰고, 벽시계는 tick(now)으로만 받는다. 그래서 MQTT 없이 테스트할 수 있다.
"""

from __future__ import annotations

import math
import secrets
from collections import defaultdict, deque
from dataclasses import dataclass, field

from protocol import DetectionFrame, FireEvent, Snapshot, SnapshotCheck, Telemetry

from . import geo


@dataclass
class JudgeConfig:
    min_conf: float = 0.4  # 이보다 낮은 박스는 세지 않는다
    min_alt: float = 25.0  # m, 이보다 낮게 날 때의 탐지는 거른다
    radius_m: float = 40.0  # 이 안의 탐지는 같은 화재
    window_s: float = 10.0
    min_hits: int = 3  # window_s 안에 이만큼의 프레임에서 잡혀야 이벤트 후보 통과
    need_check: bool = True  # 서버 재확인 결과를 요구 (재확인을 끄면 False)
    candidate_ttl_s: float = 60.0  # 이벤트가 못 된 후보를 잊는 시간
    update_every_s: float = 5.0  # 같은 이벤트의 updated 발행 간격
    pose_gap_ms: int = 1500  # 프레임 시각과 이만큼 넘게 차이 나는 위치는 쓰지 않는다
    camera: geo.Camera = field(default_factory=geo.Camera)


@dataclass
class Pose:
    ts: int
    lat: float
    lon: float
    alt: float
    heading: float


class PoseBuffer:
    """드론별 최근 위치. 프레임 시각과 가장 가까운 위치를 찾는다."""

    def __init__(self, keep: int = 120):
        self.poses: defaultdict[str, deque[Pose]] = defaultdict(lambda: deque(maxlen=keep))

    def add(self, tel: Telemetry) -> None:
        p = tel.position
        self.poses[tel.drone_id].append(Pose(tel.ts, p.lat, p.lon, p.alt_rel, tel.velocity.heading))

    def at(self, drone_id: str, ts: int, max_gap_ms: int) -> Pose | None:
        best = min(self.poses.get(drone_id, ()), key=lambda p: abs(p.ts - ts), default=None)
        return best if best and abs(best.ts - ts) <= max_gap_ms else None


@dataclass
class Hit:
    ts: int
    lat: float
    lon: float
    cls: str
    conf: float


@dataclass
class Track:
    """화재 후보 하나. status가 None이면 아직 이벤트가 아니다."""

    event_id: str
    drone_id: str
    lat0: float  # 위치 평균을 m로 계산하는 기준점 (첫 탐지)
    lon0: float
    first_seen: int
    last_seen: int
    status: str | None = None
    count: int = 0
    sum_e: float = 0.0
    sum_n: float = 0.0
    sum_sq: float = 0.0
    classes: set[str] = field(default_factory=set)
    max_conf: float = 0.0
    frames: deque[int] = field(default_factory=deque)  # 최근 탐지 프레임 시각 (window_s 안)
    confirmed: int = 0
    rejected: int = 0
    snapshot_id: str | None = None
    snapshot_score: float = -1.0
    dirty: bool = False
    published_at: int = 0

    @property
    def center(self) -> tuple[float, float]:
        if not self.count:
            return self.lat0, self.lon0
        return geo.move(self.lat0, self.lon0, self.sum_e / self.count, self.sum_n / self.count)

    def spread(self) -> float | None:
        if self.count < 2:
            return None
        me, mn = self.sum_e / self.count, self.sum_n / self.count
        return math.sqrt(max(self.sum_sq / self.count - me * me - mn * mn, 0.0))

    def add(self, hit: Hit) -> None:
        e, n = geo.offset(self.lat0, self.lon0, hit.lat, hit.lon)
        self.count += 1
        self.sum_e += e
        self.sum_n += n
        self.sum_sq += e * e + n * n
        self.classes.add(hit.cls)
        self.max_conf = max(self.max_conf, hit.conf)
        self.last_seen = max(self.last_seen, hit.ts)
        if not self.frames or self.frames[-1] != hit.ts:
            self.frames.append(hit.ts)
        self.dirty = True

    def recent_frames(self, window_ms: int) -> int:
        while self.frames and self.frames[0] < self.last_seen - window_ms:
            self.frames.popleft()
        return len(self.frames)

    def event(self) -> FireEvent:
        lat, lon = self.center
        spread = self.spread()
        return FireEvent(
            event_id=self.event_id,
            drone_id=self.drone_id,
            status=self.status or "new",
            classes=sorted(self.classes),
            max_conf=round(self.max_conf, 3),
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            lat=round(lat, 7),
            lon=round(lon, 7),
            snapshot_url=f"/media/snapshots/{self.snapshot_id}.jpg" if self.snapshot_id else None,
            detection_count=self.count,
            check_confirmed=self.confirmed,
            check_rejected=self.rejected,
            radius_m=round(spread, 1) if spread is not None else None,
        )

    @classmethod
    def from_event(cls, ev: FireEvent) -> Track:
        """저장해 둔 이벤트로 되살린다 (다시 켰을 때 같은 곳의 탐지를 이어 붙이려고)."""
        t = cls(ev.event_id, ev.drone_id, ev.lat, ev.lon, ev.first_seen, ev.last_seen, status=ev.status)
        t.count = ev.detection_count
        t.sum_sq = (ev.radius_m or 0.0) ** 2 * ev.detection_count
        t.classes = set(ev.classes)
        t.max_conf = ev.max_conf
        t.confirmed, t.rejected = ev.check_confirmed, ev.check_rejected
        if ev.snapshot_url:
            t.snapshot_id = ev.snapshot_url.rsplit("/", 1)[-1].removesuffix(".jpg")
            t.snapshot_score = 0.0
        return t


# (action, event): action = "created" | "updated"
Change = tuple[str, FireEvent]


def new_event_id(ts: int) -> str:
    return f"evt_{ts}_{secrets.token_hex(3)}"


class Judge:
    def __init__(self, cfg: JudgeConfig | None = None, events: list[FireEvent] = ()):
        self.cfg = cfg or JudgeConfig()
        self.poses = PoseBuffer()
        self.tracks: dict[str, Track] = {t.event_id: t for t in map(Track.from_event, events)}

    # ------------------------------------------------------------------ 입력
    def on_telemetry(self, tel: Telemetry) -> None:
        self.poses.add(tel)

    def on_frame(self, frame: DetectionFrame) -> list[Change]:
        changes: list[Change] = []
        for hit in self._locate(frame.drone_id, frame.frame_ts, frame.detections):
            track = self._match(hit) or self._new_track(frame.drone_id, hit)
            track.add(hit)
            changes += self._maybe_promote(track)
        return changes

    def on_check(self, snap: Snapshot, check: SnapshotCheck) -> list[Change]:
        """서버 재확인 결과를 그 사진에 찍힌 화재 후보에 반영한다."""
        changes: list[Change] = []
        seen: set[str] = set()
        best = max((d.conf for d in check.detections), default=0.0)
        for hit in self._locate(snap.drone_id, snap.frame_ts, snap.detections):
            track = self._match(hit)
            if track is None or track.event_id in seen:
                continue
            seen.add(track.event_id)
            if check.verdict == "confirmed":
                track.confirmed += 1
                score = 1.0 + best  # 확인된 사진이 우선, 그중 큰 모델 신뢰도가 높은 것
            else:
                track.rejected += 1
                score = hit.conf
            if score > track.snapshot_score:
                track.snapshot_id, track.snapshot_score = snap.snapshot_id, score
            track.dirty = True
            changes += self._maybe_promote(track)
        return changes

    def set_status(self, event_id: str, status: str, now: int) -> FireEvent | None:
        track = self.tracks.get(event_id)
        if track is None or track.status is None:
            return None
        track.status = status
        track.dirty, track.published_at = False, now
        return track.event()

    def tick(self, now: int) -> list[Change]:
        """밀린 updated 발행, 오래된 후보 정리. 1초마다 부른다."""
        changes: list[Change] = []
        for tid, t in list(self.tracks.items()):
            if t.status is None:
                if now - t.last_seen > self.cfg.candidate_ttl_s * 1000:
                    del self.tracks[tid]
            elif t.dirty and now - t.published_at >= self.cfg.update_every_s * 1000:
                t.dirty, t.published_at = False, now
                changes.append(("updated", t.event()))
        return changes

    # ------------------------------------------------------------------ 조회
    def events(self, limit: int = 50) -> list[FireEvent]:
        evs = [t for t in self.tracks.values() if t.status is not None]
        evs.sort(key=lambda t: t.last_seen, reverse=True)
        return [t.event() for t in evs[:limit]]

    def candidates(self) -> int:
        return sum(t.status is None for t in self.tracks.values())

    # ------------------------------------------------------------------ 내부
    def _locate(self, drone_id: str, ts: int, detections) -> list[Hit]:
        dets = [d for d in detections if d.conf >= self.cfg.min_conf]
        if not dets:
            return []
        pose = self.poses.at(drone_id, ts, self.cfg.pose_gap_ms)
        if pose is None or pose.alt < self.cfg.min_alt:
            return []
        hits = []
        for d in dets:
            x1, y1, x2, y2 = d.bbox
            off = geo.ground_offset((x1 + x2) / 2, (y1 + y2) / 2, pose.alt, pose.heading, self.cfg.camera)
            if off is None:
                continue
            lat, lon = geo.move(pose.lat, pose.lon, *off)
            hits.append(Hit(ts, lat, lon, d.cls, d.conf))
        return hits

    def _match(self, hit: Hit) -> Track | None:
        """가장 가까운 화재 후보·이벤트 (종료한 이벤트는 빼고)."""
        best, best_d = None, self.cfg.radius_m
        for t in self.tracks.values():
            if t.status == "resolved":
                continue
            d = geo.distance(*t.center, hit.lat, hit.lon)
            if d <= best_d:
                best, best_d = t, d
        return best

    def _new_track(self, drone_id: str, hit: Hit) -> Track:
        t = Track(new_event_id(hit.ts), drone_id, hit.lat, hit.lon, hit.ts, hit.ts)
        self.tracks[t.event_id] = t
        return t

    def _maybe_promote(self, t: Track) -> list[Change]:
        if t.status is not None:
            return []
        frames = t.recent_frames(int(self.cfg.window_s * 1000))
        if self.cfg.need_check:
            ok = frames >= self.cfg.min_hits and t.confirmed >= 1 and t.confirmed >= t.rejected
        else:
            ok = frames >= self.cfg.min_hits * 2
        if not ok:
            return []
        t.status, t.dirty, t.published_at = "new", False, t.last_seen
        return [("created", t.event())]
