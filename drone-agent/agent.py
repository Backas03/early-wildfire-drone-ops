"""drone-agent: Pixhawk(MAVLink) ⇄ MQTT 중계.

라즈베리파이에서는 시리얼로, 개발 PC에서는 SITL(가상 드론)에 TCP로 연결한다.

  python drone-agent/agent.py --mavlink tcp:127.0.0.1:5760          # SITL
  python drone-agent/agent.py --mavlink /dev/ttyAMA0 --baud 921600  # RPi

하는 일
- 텔레메트리(2Hz)·상태(변할 때)·FC 문장(STATUSTEXT)·FC에 들어 있는 미션을 MQTT로 발행
- 대시보드 명령 처리: 미션 업로드/다운로드/삭제, 모드 변경
  (시동·이륙은 프로토콜에 없다. 시동은 조종기로 한다)
"""

import argparse
import json
import logging
import queue
import re
import signal
import threading
import time
from collections import deque
from pathlib import Path

import paho.mqtt.client as mqtt
from pydantic import ValidationError

import mission_transfer as mt
from fc_link import FcLink  # MAVLINK20 환경 변수를 정하므로 pymavlink보다 먼저 import
from protocol import (
    CommandAck,
    CommandAdapter,
    DroneMission,
    DroneStatus,
    GotoCmd,
    MissionClearCmd,
    MissionDownloadCmd,
    MissionPlan,
    MissionUploadCmd,
    PlanHome,
    SetModeCmd,
    StatusText,
    topics,
)

AGENT_VERSION = "0.3.0"
log = logging.getLogger("drone-agent")

SYNC = "sync"  # 미션 작업 큐에 넣는 내부 작업: FC 미션을 읽어 발행
QUIET_AFTER_TRANSFER_S = 3.0  # 우리가 바꾼 미션을 "다른 GCS가 바꿨다"로 착각하지 않게
GOTO_MIN_ALT_M = 1.0  # 이보다 낮으면 땅에 있는 것으로 본다 (GUIDED는 땅에서 스스로 뜨지 않음)
# 마지막으로 올린 미션 (FC는 이름을 저장하지 않으므로, agent를 다시 켜도 이름을 살리려고 남긴다)
STATE_DIR = Path(__file__).resolve().parent / "state"


class Agent:
    def __init__(self, args):
        self.drone_id: str = args.drone_id
        self.rate: float = args.rate
        self.stop = threading.Event()
        self.link = FcLink(args.mavlink, args.baud)
        self.link.on_fc_connected = self._on_fc_connected
        self.link.on_text = self._on_text
        self.mission_jobs: queue.Queue = queue.Queue()
        self.mode_jobs: queue.Queue = queue.Queue()
        # FC에 들어 있는 미션 (mission_known이 False면 아직 모름)
        self.mission: MissionPlan | None = None
        self.mission_known = False
        self.fc_items = 0  # 마지막으로 확인한 FC 미션 항목 수 (읽지 못한 미션도 센다)
        self.mission_busy = threading.Event()
        self.quiet_until = 0.0
        self.seen_mission_id: int | None = None
        self.texts: deque[tuple[float, int, str]] = deque(maxlen=30)
        self.last_status: tuple | None = None
        self.state_file = STATE_DIR / f"{re.sub(r'[^A-Za-z0-9_-]', '_', self.drone_id)}-mission.json"
        self.uploaded = self._recall()
        self.mqtt = self._make_mqtt(args.mqtt_host, args.mqtt_port)

    # ------------------------------------------------------------------ 마지막으로 올린 미션
    def _recall(self) -> MissionPlan | None:
        try:
            return MissionPlan.model_validate_json(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            return None

    def _remember(self, plan: MissionPlan) -> None:
        self.uploaded = plan
        try:
            STATE_DIR.mkdir(exist_ok=True)
            self.state_file.write_text(plan.model_dump_json(), encoding="utf-8")
        except OSError as e:
            log.warning("올린 미션을 기록하지 못함: %s", e)

    # ------------------------------------------------------------------ MQTT
    def _make_mqtt(self, host: str, port: int) -> mqtt.Client:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"agent-{self.drone_id}")
        offline = DroneStatus(drone_id=self.drone_id, online=False, agent_version=AGENT_VERSION)
        c.will_set(topics.status(self.drone_id), offline.model_dump_json(), qos=1, retain=True)
        c.reconnect_delay_set(min_delay=1, max_delay=10)
        c.on_connect = self._on_mqtt_connect
        c.on_message = lambda _c, _u, msg: self._on_command(msg.payload)
        c.connect_async(host, port, keepalive=10)
        return c

    def _on_mqtt_connect(self, c, _userdata, _flags, reason_code, _props):
        log.info("MQTT 연결됨 (%s)", reason_code)
        c.subscribe(topics.cmd(self.drone_id), qos=1)
        self.last_status = None  # 브로커가 재시작됐을 수 있으니 상태를 다시 발행
        if self.mission_known:
            self._publish_mission()

    def _publish(self, topic: str, model, retain: bool = False) -> None:
        self.mqtt.publish(topic, model.model_dump_json(), qos=1, retain=retain)

    def ack(self, cmd_id: str, status: str, **kw) -> None:
        self._publish(topics.cmd_ack(self.drone_id), CommandAck(drone_id=self.drone_id, cmd_id=cmd_id, status=status, **kw))

    def _publish_mission(self, error: str | None = None) -> None:
        msg = DroneMission(drone_id=self.drone_id, mission=self.mission, error=error)
        self._publish(topics.mission(self.drone_id), msg, retain=True)

    # ------------------------------------------------------------------ FC 이벤트 (텔레메트리 루프 스레드)
    def _on_fc_connected(self) -> None:
        try:
            self.link.request_streams()
        except ConnectionError:
            return
        self.mission_jobs.put(SYNC)

    def _on_text(self, severity: int, text: str) -> None:
        log.info("FC: %s", text)
        self.texts.append((time.monotonic(), severity, text))
        self._publish(topics.statustext(self.drone_id), StatusText(drone_id=self.drone_id, severity=severity, text=text))

    def _texts_since(self, t0: float) -> list[str]:
        """t0 이후 FC가 보낸 경고 이상 문장 (명령이 거부된 이유를 알려 주는 경우가 많다)."""
        return [text for t, sev, text in list(self.texts) if t >= t0 and sev <= 4]

    # ------------------------------------------------------------------ 명령 (MQTT 스레드)
    def _on_command(self, payload: bytes) -> None:
        try:
            cmd = CommandAdapter.validate_json(payload)
        except ValidationError as e:
            err = e.errors()[0]
            log.warning("잘못된 명령: %s", err)
            try:
                cmd_id = json.loads(payload).get("cmd_id")
            except (ValueError, AttributeError):
                cmd_id = None
            if isinstance(cmd_id, str) and cmd_id:
                self.ack(cmd_id, "failed", error=f"잘못된 명령: {'.'.join(map(str, err['loc']))} {err['msg']}")
            return
        log.info("명령 수신: %s (%s)", cmd.type, cmd.cmd_id)
        if not self.link.state.fc_connected():
            self.ack(cmd.cmd_id, "failed", error="FC 연결 안 됨")
            return
        self.ack(cmd.cmd_id, "accepted")
        (self.mode_jobs if isinstance(cmd, (SetModeCmd, GotoCmd)) else self.mission_jobs).put(cmd)

    def _progress(self, cmd_id: str):
        last = [-1.0]

        def report(p: float) -> None:
            if p >= 1 or p - last[0] >= 0.1:
                last[0] = p
                self.ack(cmd_id, "in_progress", progress=round(min(p, 1.0), 3))

        return report

    def _flying_auto(self) -> bool:
        s = self.link.state
        return s.armed and s.mode == "AUTO"

    # ------------------------------------------------------------------ 미션 작업 스레드
    def mission_worker(self) -> None:
        while not self.stop.is_set():
            try:
                job = self.mission_jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            self.mission_busy.set()
            try:
                if job == SYNC:
                    self._sync()
                    continue
                try:
                    result = self._run_mission(job)
                    self.ack(job.cmd_id, "succeeded", mission=result)
                except (mt.FcError, ConnectionError) as e:
                    log.warning("%s 실패: %s", job.type, e)
                    self.ack(job.cmd_id, "failed", error=str(e))
                except Exception as e:  # 예상 못 한 오류도 대시보드에 알린다
                    log.exception("%s 처리 중 오류", job.type)
                    self.ack(job.cmd_id, "failed", error=f"agent 오류: {e}")
            finally:
                self.quiet_until = time.monotonic() + QUIET_AFTER_TRANSFER_S
                self.mission_busy.clear()

    def _run_mission(self, cmd) -> MissionPlan | None:
        port = self.link.mission_port
        if isinstance(cmd, MissionUploadCmd):
            if self._flying_auto():
                raise mt.FcError("AUTO로 비행 중에는 미션을 바꿀 수 없어요. 일시 정지(BRAKE)로 멈춘 뒤 다시 하세요.")
            h = self.link.state.home
            home = PlanHome(lat=h.lat, lon=h.lon, alt_msl=h.alt_msl) if h else None
            mt.upload(port, mt.plan_to_rows(cmd.mission, home), self._progress(cmd.cmd_id))
            self.mission, self.mission_known, self.fc_items = cmd.mission, True, len(cmd.mission.items)
            self._remember(cmd.mission)
            self._publish_mission()
            log.info("미션 업로드 완료: %s (%d개)", cmd.mission.name, len(cmd.mission.items))
            return None
        if isinstance(cmd, MissionDownloadCmd):
            plan = self._read_mission(self._progress(cmd.cmd_id))
            self.mission, self.mission_known = plan, True
            self._publish_mission()
            return plan
        if isinstance(cmd, MissionClearCmd):
            if self._flying_auto():
                raise mt.FcError("AUTO로 비행 중에는 미션을 지울 수 없어요. 일시 정지(BRAKE)로 멈춘 뒤 다시 하세요.")
            mt.clear(port)
            self.mission, self.mission_known, self.fc_items = None, True, 0
            self._publish_mission()
            return None
        raise AssertionError(cmd)

    def _read_mission(self, progress=None) -> MissionPlan | None:
        rows = mt.download(self.link.mission_port, progress)
        self.fc_items = max(len(rows) - 1, 0)
        plan = mt.plan_from_rows(rows, f"{self.drone_id}에서 받은 미션")
        # 우리가 올린 미션이 그대로면 이름·ID를 살린다 (FC는 이름을 저장하지 않음)
        for ref in (self.mission, self.uploaded):
            if plan and ref and mt.same_items(plan, ref):
                return plan.model_copy(update={"name": ref.name, "mission_id": ref.mission_id})
        return plan

    def _sync(self) -> None:
        try:
            self.mission = self._read_mission()
            self.mission_known = True
            self._publish_mission()
            n = len(self.mission.items) if self.mission else 0
            log.info("FC 미션 읽음: %d개", n)
        except mt.FcError as e:
            log.warning("FC 미션을 읽지 못함: %s", e)
            self.mission, self.mission_known = None, True
            self._publish_mission(error=str(e))
        except ConnectionError:
            pass

    def _watch_mission(self) -> None:
        """다른 GCS(Mission Planner 등)가 미션을 바꾸면 다시 읽는다."""
        s = self.link.state
        with s.lock:
            cur = s.msgs.get("MISSION_CURRENT")
        mission_id = getattr(cur, "mission_id", 0) or None if cur else None
        total = s.mission_total()
        if self.mission_busy.is_set() or time.monotonic() < self.quiet_until or not self.mission_known:
            self.seen_mission_id = mission_id
            return
        changed = mission_id is not None and self.seen_mission_id is not None and mission_id != self.seen_mission_id
        if total is not None and total != self.fc_items:
            changed = True
        self.seen_mission_id = mission_id
        if changed and self.mission_jobs.empty():
            log.info("FC 미션이 바뀐 것 같아 다시 읽음")
            self.quiet_until = time.monotonic() + 5
            self.mission_jobs.put(SYNC)

    # ------------------------------------------------------------------ 모드 작업 스레드
    def mode_worker(self) -> None:
        while not self.stop.is_set():
            try:
                cmd: SetModeCmd | GotoCmd = self.mode_jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            t0 = time.monotonic()
            if isinstance(cmd, GotoCmd):
                self._goto(cmd, t0)
                continue
            try:
                number = self.link.mode_number(cmd.mode)
                if number is None:
                    raise mt.FcError(f"이 기체에는 {cmd.mode} 모드가 없어요")
                if cmd.mode == "AUTO" and self.mission_known and self.mission is None:
                    raise mt.FcError("드론에 미션이 없어요. 먼저 미션을 업로드하세요.")
                mt.set_mode(self.link.command_port, number)
                log.info("모드 변경: %s", cmd.mode)
                self.ack(cmd.cmd_id, "succeeded")
            except (mt.FcError, ConnectionError) as e:
                time.sleep(0.3)  # 거부 이유 문장(STATUSTEXT)이 조금 늦게 올 수 있다
                reasons = self._texts_since(t0 - 0.2)
                error = f"{e} — {' / '.join(reasons[-2:])}" if reasons else str(e)
                log.warning("모드 변경 실패: %s", error)
                self.ack(cmd.cmd_id, "failed", error=error)

    def _goto(self, cmd: GotoCmd, t0: float) -> None:
        try:
            s = self.link.state
            with s.lock:
                pos = s.msgs.get("GLOBAL_POSITION_INT")
            if not s.armed:
                raise mt.FcError("시동이 꺼져 있어요. 떠 있는 드론만 보낼 수 있어요 (시동·이륙은 조종기로).")
            if pos is None or pos.relative_alt / 1000 < GOTO_MIN_ALT_M:
                raise mt.FcError("드론이 아직 땅에 있어요. 이륙한 뒤에 보내세요.")
            mt.reposition(self.link.command_port, cmd.lat, cmd.lon, cmd.alt)
            log.info("위치로 보냄: %.6f, %.6f, %.0f m", cmd.lat, cmd.lon, cmd.alt)
            self.ack(cmd.cmd_id, "succeeded")
        except (mt.FcError, ConnectionError) as e:
            time.sleep(0.3)
            reasons = self._texts_since(t0 - 0.2)
            error = f"{e} — {' / '.join(reasons[-2:])}" if reasons else str(e)
            log.warning("위치로 보내기 실패: %s", error)
            self.ack(cmd.cmd_id, "failed", error=error)

    # ------------------------------------------------------------------ 메인 루프
    def run(self) -> None:
        self.mqtt.loop_start()
        if not self.link.connect(self.stop):
            return
        for target in (self.mission_worker, self.mode_worker):
            threading.Thread(target=target, daemon=True).start()

        period = 1.0 / self.rate
        next_pub = 0.0
        while not self.stop.is_set():
            self.link.poll(self.stop)
            now = time.monotonic()
            if now < next_pub:
                continue
            next_pub = now + period

            s = self.link.state
            status = DroneStatus(
                drone_id=self.drone_id,
                online=True,
                fc_connected=s.fc_connected(),
                home=s.home,
                agent_version=AGENT_VERSION,
            )
            key = (status.fc_connected, status.home)
            if key != self.last_status:
                self._publish(topics.status(self.drone_id), status, retain=True)
                self.last_status = key
            if not status.fc_connected:
                continue
            tel = s.telemetry(self.drone_id)
            if tel:
                self.mqtt.publish(topics.telemetry(self.drone_id), tel.model_dump_json(), qos=0)
            self._watch_mission()

        log.info("종료")
        offline = DroneStatus(drone_id=self.drone_id, online=False, agent_version=AGENT_VERSION)
        info = self.mqtt.publish(topics.status(self.drone_id), offline.model_dump_json(), qos=1, retain=True)
        info.wait_for_publish(2)
        self.mqtt.loop_stop()
        self.mqtt.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drone-id", default="drone1")
    ap.add_argument("--mavlink", default="tcp:127.0.0.1:5760", help="pymavlink 연결 문자열")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--mqtt-host", default="127.0.0.1")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--rate", type=float, default=2.0, help="텔레메트리 발행 주기(Hz)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    agent = Agent(args)
    signal.signal(signal.SIGINT, lambda *_: agent.stop.set())
    agent.run()


if __name__ == "__main__":
    main()
