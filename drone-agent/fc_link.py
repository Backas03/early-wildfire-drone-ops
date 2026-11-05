"""FC(Pixhawk/SITL)와의 MAVLink 연결.

- 받은 메시지를 나눈다: 텔레메트리 값(MavState), 미션 절차용 우편함, 명령 응답 우편함, FC 문장(STATUSTEXT)
- 여러 스레드(텔레메트리 루프, 미션 작업, 모드 작업)가 같이 보내도 안전하게 잠금을 건다
- 연결이 끊기면(SITL 재시작, USB 뽑힘) 다시 연결한다
"""

import os

os.environ.setdefault("MAVLINK20", "1")  # mavutil을 import하기 전에 정해야 MAVLink 2를 쓴다

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from pymavlink import mavutil

from protocol import BatteryInfo, FlightInfo, GpsInfo, MissionProgress, Position, Telemetry, Velocity

log = logging.getLogger("drone-agent.fc")

OUR_SYSID = 255  # 지상국(GCS) 번호
FC_COMPONENT = 1
FC_TIMEOUT_S = 3.0
SILENT_RECONNECT_S = 15  # 이만큼 FC heartbeat가 없으면 포트를 다시 연다
MISSION_TYPES = {"MISSION_REQUEST", "MISSION_REQUEST_INT", "MISSION_ACK", "MISSION_COUNT", "MISSION_ITEM_INT"}
COMMAND_TYPES = {"COMMAND_ACK"}

m = mavutil.mavlink


def _raise_closed() -> None:
    raise ConnectionResetError("FC 연결이 닫힘")


def _items_in(mission_current) -> int | None:
    """MISSION_CURRENT.total → 미션 항목 수. MAVLink 정의상 홈은 빼고 센다
    (마지막 항목에서 seq == total). 0 = FC가 지원 안 함(모름), 65535 = 미션 없음."""
    total = getattr(mission_current, "total", 0) if mission_current else 0
    if total == 0:
        return None
    return 0 if total == 0xFFFF else total


class LinkPort:
    """mission_transfer.Port 구현: FcLink로 보내고, 자기 우편함에서 받는다."""

    def __init__(self, link: "FcLink"):
        self.link = link
        self.inbox: queue.Queue = queue.Queue(maxsize=200)

    def put(self, msg) -> None:
        try:
            self.inbox.put_nowait(msg)
        except queue.Full:  # 아무도 안 읽고 있으면 오래된 것부터 버린다
            self.drain()
            self.inbox.put_nowait(msg)

    def send(self, name: str, *args: Any) -> None:
        self.link.send_targeted(name, *args)

    def recv(self, types: set[str], timeout: float):
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                msg = self.inbox.get(timeout=left)
            except queue.Empty:
                return None
            if msg.get_type() in types:
                return msg
        return None

    def drain(self) -> None:
        while True:
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                return


class MavState:
    """텔레메트리를 만들 최신 값들."""

    def __init__(self):
        self.lock = threading.Lock()
        self.msgs: dict[str, Any] = {}
        self.mode = "UNKNOWN"
        self.mav_type = m.MAV_TYPE_QUADROTOR
        self.armed = False
        self.home: Position | None = None
        self.last_heartbeat = 0.0

    def fc_connected(self) -> bool:
        return time.monotonic() - self.last_heartbeat < FC_TIMEOUT_S

    def mission_total(self) -> int | None:
        """FC가 알려 준 미션 항목 수 (홈 제외). 모르면 None."""
        with self.lock:
            return _items_in(self.msgs.get("MISSION_CURRENT"))

    def telemetry(self, drone_id: str) -> Telemetry | None:
        with self.lock:
            pos = self.msgs.get("GLOBAL_POSITION_INT")
            hud = self.msgs.get("VFR_HUD")
            sys_status = self.msgs.get("SYS_STATUS")
            gps = self.msgs.get("GPS_RAW_INT")
            if not (pos and hud and sys_status and gps):
                return None
            cur = self.msgs.get("MISSION_CURRENT")
            total = _items_in(cur)
            mission = None
            # ArduPilot은 0번이 홈이므로 우리 seq = MAVLink seq - 1
            if total:
                mission = MissionProgress(current_seq=max(cur.seq - 1, 0), total=total)
            return Telemetry(
                drone_id=drone_id,
                position=Position(lat=pos.lat / 1e7, lon=pos.lon / 1e7, alt_rel=pos.relative_alt / 1000, alt_msl=pos.alt / 1000),
                velocity=Velocity(ground_speed=hud.groundspeed, climb=hud.climb, heading=hud.heading % 360),
                battery=BatteryInfo(
                    voltage=sys_status.voltage_battery / 1000,
                    current=None if sys_status.current_battery < 0 else sys_status.current_battery / 100,
                    remaining=None if sys_status.battery_remaining < 0 else sys_status.battery_remaining,
                ),
                gps=GpsInfo(
                    fix_type=gps.fix_type,
                    satellites=gps.satellites_visible,
                    hdop=None if gps.eph == 65535 else gps.eph / 100,
                ),
                flight=FlightInfo(mode=self.mode, armed=self.armed),
                mission=mission,
            )


class FcLink:
    def __init__(self, device: str, baud: int):
        self.device = device
        self.baud = baud
        self.master = None
        self.opened_at = 0.0
        self.fc_sysid = 0
        self.tx = threading.Lock()
        self.state = MavState()
        self.mission_port = LinkPort(self)
        self.command_port = LinkPort(self)
        # 콜백 (텔레메트리 루프 스레드에서 불린다)
        self.on_fc_connected: Callable[[], None] | None = None
        self.on_text: Callable[[int, str], None] | None = None
        self._last_gcs_heartbeat = 0.0

    # --- 연결 ---------------------------------------------------------------
    def connect(self, stop: threading.Event) -> bool:
        while not stop.is_set():
            try:
                self.master = mavutil.mavlink_connection(self.device, baud=self.baud, source_system=OUR_SYSID)
            except OSError as e:
                log.warning("MAVLink 연결 실패 (%s). 3초 뒤 다시 시도", e)
                stop.wait(3)
                continue
            if hasattr(self.master, "handle_eof"):
                # TCP: pymavlink은 끊겨도 "EOF on TCP socket"만 찍고 계속 빈 값을 준다. 예외로 바꿔 직접 다시 연결한다.
                self.master.handle_eof = self.master.handle_disconnect = _raise_closed
            self.opened_at = time.monotonic()
            log.info("MAVLink 포트 열림: %s (FC heartbeat 기다리는 중)", self.device)
            return True
        return False

    def _reconnect(self, stop: threading.Event) -> None:
        try:
            if self.master:
                self.master.close()
        except (OSError, AttributeError):  # pymavlink이 이미 소켓을 닫았으면 port가 None
            pass
        self.master = None
        self.state.last_heartbeat = 0.0
        stop.wait(2)
        self.connect(stop)

    # --- 보내기 -------------------------------------------------------------
    def send_targeted(self, name: str, *args: Any) -> None:
        if not self.master or not self.fc_sysid:
            raise ConnectionError("FC 연결 안 됨")
        with self.tx:
            getattr(self.master.mav, f"{name}_send")(self.fc_sysid, FC_COMPONENT, *args)

    def _send_heartbeat(self) -> None:
        with self.tx:
            self.master.mav.heartbeat_send(m.MAV_TYPE_GCS, m.MAV_AUTOPILOT_INVALID, 0, 0, m.MAV_STATE_ACTIVE)

    def request_streams(self) -> None:
        """필요한 메시지를 주기적으로 보내 달라고 요청한다."""
        for msg_id, hz in (
            (m.MAVLINK_MSG_ID_GLOBAL_POSITION_INT, 5),
            (m.MAVLINK_MSG_ID_VFR_HUD, 5),
            (m.MAVLINK_MSG_ID_SYS_STATUS, 1),
            (m.MAVLINK_MSG_ID_GPS_RAW_INT, 1),
            (m.MAVLINK_MSG_ID_MISSION_CURRENT, 1),
            (m.MAVLINK_MSG_ID_HOME_POSITION, 0.2),
        ):
            self.send_targeted("command_long", m.MAV_CMD_SET_MESSAGE_INTERVAL, 0, msg_id, int(1e6 / hz), 0, 0, 0, 0, 0)
        self.send_targeted("command_long", m.MAV_CMD_GET_HOME_POSITION, 0, 0, 0, 0, 0, 0, 0, 0)

    def mode_number(self, name: str) -> int | None:
        return (mavutil.mode_mapping_byname(self.state.mav_type) or {}).get(name)

    # --- 받기 (텔레메트리 루프에서 계속 부른다) ------------------------------
    def poll(self, stop: threading.Event, timeout: float = 0.05) -> None:
        if self.master is None:
            self._reconnect(stop)
            return
        now = time.monotonic()
        last = self.state.last_heartbeat or self.opened_at
        if now - last > SILENT_RECONNECT_S:
            log.warning("FC heartbeat가 %d초 동안 없음. 포트를 다시 연다", SILENT_RECONNECT_S)
            self._reconnect(stop)
            return
        try:
            if now - self._last_gcs_heartbeat >= 1.0:  # FC가 지상국이 살아 있음을 알게
                self._last_gcs_heartbeat = now
                self._send_heartbeat()
            msg = self.master.recv_match(blocking=True, timeout=timeout)
        except OSError as e:
            log.warning("MAVLink 연결 끊김 (%s). 다시 연결", e)
            self._reconnect(stop)
            return
        if msg is not None and msg.get_type() != "BAD_DATA":
            self._route(msg)

    def _route(self, msg) -> None:
        t = msg.get_type()
        if t == "HEARTBEAT":
            # FC의 heartbeat만 본다 (다른 GCS·짐벌·컴패니언 컴퓨터가 보낸 것은 무시)
            if msg.get_srcComponent() != FC_COMPONENT or not self.master.probably_vehicle_heartbeat(msg):
                return
            first = not self.state.fc_connected()
            self.fc_sysid = msg.get_srcSystem()
            with self.state.lock:
                self.state.mode = mavutil.mode_string_v10(msg)
                self.state.mav_type = msg.type
                self.state.armed = bool(msg.base_mode & m.MAV_MODE_FLAG_SAFETY_ARMED)
                self.state.last_heartbeat = time.monotonic()
            if first:
                log.info("FC 연결됨 (system %s, %s)", self.fc_sysid, self.state.mode)
                if self.on_fc_connected:
                    self.on_fc_connected()
            return
        if msg.get_srcSystem() != self.fc_sysid:
            return
        if t in MISSION_TYPES or t in COMMAND_TYPES:
            if getattr(msg, "target_system", 0) not in (0, OUR_SYSID):
                return  # 다른 지상국에 대한 응답
            (self.mission_port if t in MISSION_TYPES else self.command_port).put(msg)
            return
        if t == "STATUSTEXT":
            text = msg.text.rstrip("\x00").strip() if isinstance(msg.text, str) else msg.text.decode(errors="replace")
            if text and self.on_text:
                self.on_text(msg.severity, text)
            return
        with self.state.lock:
            self.state.msgs[t] = msg
            if t == "HOME_POSITION":
                self.state.home = Position(
                    lat=msg.latitude / 1e7, lon=msg.longitude / 1e7, alt_rel=0.0, alt_msl=msg.altitude / 1000
                )
