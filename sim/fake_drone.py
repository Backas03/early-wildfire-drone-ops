"""가짜 드론: SITL 없이 프로토콜 형식의 텔레메트리를 MQTT로 발행한다.

홈 주변을 원을 그리며 도는 드론을 흉내 낸다. 프론트엔드 개발이나 MQTT 확인용.
진짜 비행 동작(미션 업로드 등)을 확인하려면 SITL + drone-agent를 쓴다.

  python sim/fake_drone.py                    # drone1, 대구대 근처
  python sim/fake_drone.py --drone-id drone2 --lat 35.9 --lon 128.85
"""

import argparse
import math
import signal
import threading

import paho.mqtt.client as mqtt

from protocol import (
    BatteryInfo,
    DroneStatus,
    FlightInfo,
    GpsInfo,
    LinkInfo,
    Position,
    Telemetry,
    Velocity,
    topics,
)

EARTH_R = 6_371_000.0


def main() -> None:
    ap = argparse.ArgumentParser()
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
    home = Position(lat=args.lat, lon=args.lon, alt_rel=0, alt_msl=args.home_msl)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"fake-{did}")
    client.will_set(topics.status(did), DroneStatus(drone_id=did, online=False).model_dump_json(), qos=1, retain=True)
    client.connect(args.mqtt_host, args.mqtt_port, keepalive=10)
    client.loop_start()
    client.publish(
        topics.status(did),
        DroneStatus(drone_id=did, online=True, fc_connected=True, home=home, agent_version="fake").model_dump_json(),
        qos=1,
        retain=True,
    )
    print(f"가짜 드론 {did} 발행 중 → {topics.telemetry(did)}  (Ctrl+C로 종료)")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    dt = 1.0 / args.rate
    omega = args.speed / args.radius  # rad/s
    theta, battery = 0.0, 12.6

    while not stop.wait(dt):
        theta = (theta + omega * dt) % (2 * math.pi)
        north, east = args.radius * math.cos(theta), args.radius * math.sin(theta)
        lat = args.lat + math.degrees(north / EARTH_R)
        lon = args.lon + math.degrees(east / (EARTH_R * math.cos(math.radians(args.lat))))
        heading = (math.degrees(theta) + 90) % 360  # 반시계 → 시계 방향 접선
        battery = max(battery - 0.0008, 10.5)
        tel = Telemetry(
            drone_id=did,
            position=Position(lat=lat, lon=lon, alt_rel=args.alt, alt_msl=args.home_msl + args.alt),
            velocity=Velocity(ground_speed=args.speed, climb=0.0, heading=heading),
            battery=BatteryInfo(voltage=round(battery, 2), current=11.0, remaining=int((battery - 10.5) / 2.1 * 100)),
            gps=GpsInfo(fix_type=3, satellites=14, hdop=0.7),
            flight=FlightInfo(mode="AUTO", armed=True),
            link=LinkInfo(lte_rssi=-70),
        )
        client.publish(topics.telemetry(did), tel.model_dump_json(), qos=0)

    client.publish(topics.status(did), DroneStatus(drone_id=did, online=False).model_dump_json(), qos=1, retain=True).wait_for_publish(2)
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
