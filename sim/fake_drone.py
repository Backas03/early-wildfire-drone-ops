"""가짜 드론: SITL 없이 drone-agent처럼 MQTT로 텔레메트리를 보내고 대시보드 명령을 받는다.

진짜 비행 프로그램이 아니라 간단한 흉내다. 대시보드·가상 카메라 시험용.
비행 컨트롤러가 정말 받아들이는지(미션 형식, 모드 전환 조건)는 SITL + drone-agent로 확인한다.

- 처음에는 시동이 걸린 채 홈 주변을 원으로 돈다 (AUTO, 미션 없음)
- 일시 정지(BRAKE·LOITER·GUIDED): 그 자리에 멈춘다. 미션 시작·재개(AUTO): 멈춘 곳부터 이어서
- 귀환(RTL): 15 m보다 낮으면 올라간 뒤 홈으로 와서 착륙. 바로 착륙(LAND): 그 자리에 착륙
- 미션 업로드·삭제: drone-agent처럼 AUTO로 비행 중에는 거부한다
- 착륙해 있을 때 AUTO를 받으면 조종기로 시동을 건 셈 치고 다시 뜬다 (가짜 드론만 이렇게 한다)

  python sim/fake_drone.py                         # drone1, 대구대 근처
  python sim/fake_drone.py --radius 90 --alt 35    # 가상 화재 위를 지나는 원 (--source sim 시험용)
  python sim/fake_drone.py --drone-id drone2 --lat 35.9 --lon 128.85
"""

import argparse
import math
import signal
import threading

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from protocol import (
    BatteryInfo,
    CommandAck,
    CommandAdapter,
    DroneMission,
    DroneStatus,
    FlightInfo,
    GpsInfo,
    LinkInfo,
    MissionClearCmd,
    MissionDownloadCmd,
    MissionPlan,
    MissionProgress,
    MissionUploadCmd,
    Position,
    SetModeCmd,
    Telemetry,
    Velocity,
    topics,
)

EARTH_R = 6_371_000.0
HOLD_MODES = {"BRAKE", "LOITER", "GUIDED"}  # 제자리 유지
CLIMB_MS, DESCEND_MS, LAND_MS = 2.5, 1.5, 1.0  # ArduCopter 기본값과 비슷하게
RTL_ALT_M = 15.0
BATTERY_DRAIN_V_S = 0.0016  # 12.6 V → 10.5 V 약 22분
BUSY_AUTO = "AUTO로 비행 중에는 미션을 {} 수 없어요. 일시 정지(BRAKE)로 멈춘 뒤 다시 하세요."


class FakeDrone:
    """비행 흉내 (MQTT와 무관). 위치는 홈 기준 북·동 m와 홈 기준 고도로 들고 있는다."""

    def __init__(self, lat: float, lon: float, *, home_msl=100.0, radius=150.0, speed=6.0, alt=50.0):
        self.lat0, self.lon0, self.home_msl = lat, lon, home_msl
        self.radius, self.cruise, self.circle_alt = max(radius, 1.0), speed, alt
        self.n, self.e, self.alt = self.radius, 0.0, alt
        self.mode, self.armed = "AUTO", True
        self.speed = speed  # 미션의 change_speed로 바뀐다
        self.mission: MissionPlan | None = None
        self.seq = 0
        self.done = False  # 미션 마지막 항목까지 끝냄
        self.hold_left: float | None = None  # 웨이포인트 도착 후 남은 대기 시간
        self.rtl_alt: float | None = None
        self.ground_speed = self.climb = 0.0
        self.heading = 90.0
        self.battery = 12.6

    # ------------------------------------------------------------------ 명령
    def handle(self, cmd) -> dict:
        """명령을 처리하고 마지막 ack 내용(status, error, mission)을 돌려준다."""
        if isinstance(cmd, SetModeCmd):
            self.set_mode(cmd.mode)
            return {"status": "succeeded"}
        if isinstance(cmd, MissionDownloadCmd):
            return {"status": "succeeded", "mission": self.mission}
        if self.armed and self.mode == "AUTO":
            verb = "바꿀" if isinstance(cmd, MissionUploadCmd) else "지울"
            return {"status": "failed", "error": BUSY_AUTO.format(verb)}
        if isinstance(cmd, MissionUploadCmd):
            self.mission = cmd.mission
        elif isinstance(cmd, MissionClearCmd):
            self.mission = None
        self.seq, self.done, self.hold_left, self.speed = 0, False, None, self.cruise
        return {"status": "succeeded"}

    def set_mode(self, mode: str) -> None:
        if mode == "AUTO":
            self.armed = True  # 착륙해 있었다면: 가짜 드론만 조종기로 시동을 건 셈 친다
            if self.done:  # 끝난 미션은 처음부터 다시
                self.seq, self.done = 0, False
        self.mode, self.hold_left, self.rtl_alt = mode, None, None

    # ------------------------------------------------------------------ 비행
    def step(self, dt: float) -> None:
        n0, e0, a0 = self.n, self.e, self.alt
        if self.armed:
            if self.mode == "AUTO":
                self._auto(dt)
            elif self.mode == "RTL":
                self._rtl(dt)
            elif self.mode == "LAND":
                self._land(dt)
            # HOLD_MODES와 그 밖의 모드: 제자리
            self.battery = max(self.battery - BATTERY_DRAIN_V_S * dt, 10.5)
        dn, de = self.n - n0, self.e - e0
        self.ground_speed = math.hypot(dn, de) / dt
        self.climb = (self.alt - a0) / dt
        if self.ground_speed > 0.1:
            self.heading = math.degrees(math.atan2(de, dn)) % 360

    def _auto(self, dt: float) -> None:
        if self.mission is None:
            self._circle(dt)
            return
        if self.done:
            return  # 마지막 웨이포인트에서 제자리 유지 (ArduCopter와 같음)
        it = self.mission.items[self.seq]
        if it.command == "change_speed":
            self.speed = it.speed
            self._next()
        elif it.command == "takeoff":
            if self._move_to(self.n, self.e, self._alt_of(it), dt):
                self._next()
        elif it.command in ("waypoint", "loiter_time"):
            if self.hold_left is None:
                n, e = self._to_ne(it.lat, it.lon)
                if not self._move_to(n, e, self._alt_of(it), dt):
                    return
                self.hold_left = it.hold_s
            self.hold_left -= dt
            if self.hold_left <= 0:
                self._next()
        elif it.command == "rtl":
            self._rtl(dt)
        elif it.command == "land":
            self._land(dt)

    def _next(self) -> None:
        self.hold_left, self.rtl_alt = None, None
        if self.seq + 1 < len(self.mission.items):
            self.seq += 1
        else:
            self.done = True

    def _circle(self, dt: float) -> None:
        theta = math.atan2(self.e, self.n)
        if abs(math.hypot(self.n, self.e) - self.radius) > 2.0:  # 원 밖이면 먼저 원으로
            self._move_to(self.radius * math.cos(theta), self.radius * math.sin(theta), self.circle_alt, dt)
            return
        theta += self.speed / self.radius * dt
        self.n, self.e = self.radius * math.cos(theta), self.radius * math.sin(theta)
        self._move_to(self.n, self.e, self.circle_alt, dt)

    def _rtl(self, dt: float) -> None:
        if self.rtl_alt is None:
            self.rtl_alt = max(self.alt, RTL_ALT_M)
        if math.hypot(self.n, self.e) > 0.01:
            if self.alt < self.rtl_alt - 0.5:  # 낮으면 먼저 올라간다
                self._move_to(self.n, self.e, self.rtl_alt, dt)
            else:
                self._move_to(0.0, 0.0, self.rtl_alt, dt)
        else:
            self._land(dt)

    def _land(self, dt: float) -> None:
        self.alt = max(self.alt - LAND_MS * dt, 0.0)
        if self.alt == 0.0:
            self.armed = False  # 착륙하면 시동이 꺼진다
            if self.mode == "AUTO" and self.mission is not None:
                self.done = True

    def _move_to(self, n: float, e: float, alt: float, dt: float) -> bool:
        """(n, e, alt) 쪽으로 dt만큼 간다. 도착했으면 True."""
        dn, de = n - self.n, e - self.e
        dist, step = math.hypot(dn, de), self.speed * dt
        if dist <= step:
            self.n, self.e = n, e
        else:
            self.n, self.e = self.n + dn / dist * step, self.e + de / dist * step
        dz = alt - self.alt
        rate = (CLIMB_MS if dz > 0 else DESCEND_MS) * dt
        self.alt += max(-rate, min(rate, dz))
        return dist <= step and abs(alt - self.alt) < 0.3

    def _alt_of(self, it) -> float:
        return it.alt - self.home_msl if it.frame == "absolute" else it.alt

    # ------------------------------------------------------------------ 좌표·메시지
    def _to_ne(self, lat: float, lon: float) -> tuple[float, float]:
        n = math.radians(lat - self.lat0) * EARTH_R
        e = math.radians(lon - self.lon0) * EARTH_R * math.cos(math.radians(self.lat0))
        return n, e

    def latlon(self) -> tuple[float, float]:
        lat = self.lat0 + math.degrees(self.n / EARTH_R)
        lon = self.lon0 + math.degrees(self.e / (EARTH_R * math.cos(math.radians(self.lat0))))
        return lat, lon

    def home(self) -> Position:
        return Position(lat=self.lat0, lon=self.lon0, alt_rel=0, alt_msl=self.home_msl)

    def telemetry(self, drone_id: str) -> Telemetry:
        lat, lon = self.latlon()
        heading = self.heading if self.heading < 360 else 0.0  # -1e-15 % 360 == 360.0 방지
        return Telemetry(
            drone_id=drone_id,
            position=Position(lat=lat, lon=lon, alt_rel=round(self.alt, 2), alt_msl=round(self.home_msl + self.alt, 2)),
            velocity=Velocity(ground_speed=round(self.ground_speed, 2), climb=round(self.climb, 2), heading=heading),
            battery=BatteryInfo(
                voltage=round(self.battery, 2),
                current=11.0 if self.armed else 0.5,
                remaining=int((self.battery - 10.5) / 2.1 * 100),
            ),
            gps=GpsInfo(fix_type=3, satellites=14, hdop=0.7),
            flight=FlightInfo(mode=self.mode, armed=self.armed),
            mission=MissionProgress(current_seq=self.seq, total=len(self.mission.items)) if self.mission else None,
            link=LinkInfo(lte_rssi=-70),
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drone-id", default="drone1")
    ap.add_argument("--lat", type=float, default=35.8964)
    ap.add_argument("--lon", type=float, default=128.8495)
    ap.add_argument("--home-msl", type=float, default=100.0)
    ap.add_argument("--radius", type=float, default=150.0, help="선회 반경 m")
    ap.add_argument("--speed", type=float, default=6.0, help="m/s")
    ap.add_argument("--alt", type=float, default=50.0)
    ap.add_argument("--rate", type=float, default=2.0, help="Hz")
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    args = ap.parse_args()

    did = args.drone_id
    drone = FakeDrone(args.lat, args.lon, home_msl=args.home_msl, radius=args.radius, speed=args.speed, alt=args.alt)
    lock = threading.Lock()  # 명령(MQTT 스레드)과 비행(메인 루프)이 drone을 같이 만진다

    def publish_status(client: mqtt.Client) -> None:
        msg = DroneStatus(drone_id=did, online=True, fc_connected=True, home=drone.home(), agent_version="fake")
        client.publish(topics.status(did), msg.model_dump_json(), qos=1, retain=True)

    def publish_mission(client: mqtt.Client) -> None:
        msg = DroneMission(drone_id=did, mission=drone.mission)
        client.publish(topics.mission(did), msg.model_dump_json(), qos=1, retain=True)

    def ack(client: mqtt.Client, cmd_id: str, **kw) -> None:
        client.publish(topics.cmd_ack(did), CommandAck(drone_id=did, cmd_id=cmd_id, **kw).model_dump_json(), qos=1)

    def on_connect(client, _userdata, _flags, reason_code, _props) -> None:
        if reason_code.is_failure:
            print(f"MQTT 연결 실패: {reason_code}")
            return
        client.subscribe(topics.cmd(did), qos=1)
        publish_status(client)
        with lock:
            publish_mission(client)

    def on_message(client, _userdata, msg) -> None:
        try:
            cmd = CommandAdapter.validate_json(msg.payload)
        except ValidationError as e:
            print(f"알 수 없는 명령이라 무시: {e.errors()[0]['msg']}")
            return
        ack(client, cmd.cmd_id, status="accepted")
        with lock:
            result = drone.handle(cmd)
            if result["status"] == "succeeded" and not isinstance(cmd, SetModeCmd):
                publish_mission(client)
        ack(client, cmd.cmd_id, **result)
        what = f"{cmd.type} {cmd.mode}" if isinstance(cmd, SetModeCmd) else cmd.type
        print(f"명령 {what}: {result['status']}" + (f" ({result['error']})" if result.get("error") else ""))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fake-{did}")
    client.will_set(topics.status(did), DroneStatus(drone_id=did, online=False).model_dump_json(), qos=1, retain=True)
    client.on_connect, client.on_message = on_connect, on_message
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=10)
    client.loop_start()
    print(f"가짜 드론 {did} 발행 중 → {topics.telemetry(did)}, 명령 대기 ← {topics.cmd(did)}  (Ctrl+C로 종료)")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    dt = 1.0 / args.rate
    while not stop.wait(dt):
        with lock:
            drone.step(dt)
            tel = drone.telemetry(did)
        client.publish(topics.telemetry(did), tel.model_dump_json(), qos=0)

    client.publish(topics.status(did), DroneStatus(drone_id=did, online=False).model_dump_json(), qos=1, retain=True).wait_for_publish(2)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
