"""가상 조종사: SITL에서 조종기 대신 시동·이륙을 한다 (시뮬레이션 전용).

대시보드는 안전 원칙상 시동·이륙을 하지 않는다. 실제 기체는 조종사가 조종기로 시동을 걸고
이륙하거나 AUTO 스위치를 켠다. SITL에는 조종기가 없으므로 이 스크립트가 그 역할을 한다.
drone-agent와 따로 SITL 5763 포트에 붙는다.

  python sim/sitl/pilot.py start             # 시동 + 미션 시작 (AUTO에서 스로틀을 올린 것과 같음)
  python sim/sitl/pilot.py takeoff --alt 20  # 시동 + 이륙 후 제자리 비행 (그다음 대시보드에서 AUTO)
  python sim/sitl/pilot.py disarm            # 착륙 후 시동 끄기 (보통은 착륙하면 FC가 알아서 끈다)

SITL을 막 켰으면 GPS·EKF가 준비될 때까지 30초~1분 걸린다. 그동안은 시동이 거부되고
FC가 이유(PreArm: ...)를 알려 주는데, 이 스크립트는 준비될 때까지 다시 시도한다.
"""

import argparse
import os
import sys
import time

os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil  # noqa: E402

m = mavutil.mavlink


def say(text: str) -> None:
    print(text, flush=True)


class Pilot:
    def __init__(self, device: str):
        say(f"SITL 연결: {device}")
        self.master = mavutil.mavlink_connection(device, source_system=254)
        hb = self.master.wait_heartbeat(timeout=15)
        if hb is None:
            sys.exit("FC heartbeat 없음. SITL과 drone-agent가 켜져 있는지 확인하세요 (SITL은 5760에 누가 붙어야 시작함).")
        self.sys, self.comp = self.master.target_system, self.master.target_component

    def pump(self, seconds: float, until=None):
        """seconds 동안 메시지를 읽으며 FC 문장을 출력한다. until(msg)가 참이면 그 메시지를 돌려준다."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            msg = self.master.recv_match(blocking=True, timeout=0.2)
            if msg is None:
                continue
            if msg.get_type() == "STATUSTEXT":
                say(f"  FC: {msg.text}")
            if until and until(msg):
                return msg
        return None

    def command(self, cmd: int, *params: float) -> int | None:
        p = list(params) + [0] * (7 - len(params))
        self.master.mav.command_long_send(self.sys, self.comp, cmd, 0, *p)
        ack = self.pump(3, lambda msg: msg.get_type() == "COMMAND_ACK" and msg.command == cmd)
        return None if ack is None else ack.result

    def set_mode(self, name: str) -> bool:
        number = self.master.mode_mapping()[name]
        return self.command(m.MAV_CMD_DO_SET_MODE, m.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, number) == m.MAV_RESULT_ACCEPTED

    def arm(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.set_mode("GUIDED") and self.command(m.MAV_CMD_COMPONENT_ARM_DISARM, 1) == m.MAV_RESULT_ACCEPTED:
                say("시동 걸림")
                return
            say("  시동 거부됨 (GPS·EKF 준비 중일 수 있음). 5초 뒤 다시 시도")
            self.pump(5)
        sys.exit("시동 실패 (시간 초과)")

    def altitude(self) -> float | None:
        msg = self.pump(2, lambda x: x.get_type() == "GLOBAL_POSITION_INT")
        return None if msg is None else msg.relative_alt / 1000


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["start", "takeoff", "disarm"])
    ap.add_argument("--alt", type=float, default=20, help="takeoff 고도 m")
    ap.add_argument("--device", default="tcp:127.0.0.1:5763")
    args = ap.parse_args()

    p = Pilot(args.device)
    if args.action == "disarm":
        ok = p.command(m.MAV_CMD_COMPONENT_ARM_DISARM, 0) == m.MAV_RESULT_ACCEPTED
        say("시동 꺼짐" if ok else "시동 끄기 거부됨 (비행 중이면 착륙 후에)")
        return

    p.arm()
    if args.action == "start":
        # MISSION_START: AUTO로 바꾸고 미션을 처음부터 시작 (지상에서 시동 건 상태여야 함)
        if p.command(m.MAV_CMD_MISSION_START, 0, 0) != m.MAV_RESULT_ACCEPTED:
            sys.exit("미션 시작 거부됨 (드론에 미션이 있는지 확인)")
        say("미션 시작. 이제 대시보드에서 지켜보세요.")
        return

    if p.command(m.MAV_CMD_NAV_TAKEOFF, 0, 0, 0, 0, 0, 0, args.alt) != m.MAV_RESULT_ACCEPTED:
        sys.exit("이륙 거부됨")
    say(f"이륙 중 → {args.alt:.0f} m")
    for _ in range(60):
        alt = p.altitude()
        if alt is not None and alt >= args.alt * 0.95:
            say(f"{alt:.1f} m 도달. GUIDED로 제자리 비행 중. 대시보드에서 AUTO를 누르면 미션을 시작해요.")
            return
    say("고도 도달 확인 못 함 (계속 올라가는 중일 수 있음)")


if __name__ == "__main__":
    main()
