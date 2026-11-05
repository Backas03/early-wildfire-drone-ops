"""가상 화재 탐지율 재기: 시나리오의 각 화재를 여러 방향·고도·카메라 기울기에서 찍어 모델이 찾는지 센다.

  python sim/evaluate_detection.py                       # fire_n, 기울기 0·30
  python sim/evaluate_detection.py --model models/fire_s.pt --tilts 0 30 45

카메라가 기울면 화재가 화면 가운데 오도록 드론을 화재 뒤쪽에 둔다.
결과는 모델·카메라 장착 각도를 고를 때 참고하는 값이다 (가상 화재는 실제 불과 모양이 다르다).
"""

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import virtual_camera as vc  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=str(ROOT.parent / "models" / "fire_n.pt"))
    ap.add_argument("--scenario", default=str(ROOT / "scenarios" / "campus.json"))
    ap.add_argument("--alts", type=float, nargs="+", default=[30, 40])
    ap.add_argument("--tilts", type=float, nargs="+", default=[0, 30])
    ap.add_argument("--headings", type=int, default=12, help="방향 개수 (360°를 나눔)")
    ap.add_argument("--threshold", type=float, default=0.4)
    args = ap.parse_args()

    from ultralytics import YOLO

    model = YOLO(args.model)
    scn = vc.Scenario.load(Path(args.scenario))
    tiles = vc.TileSource()
    print(f"모델 {Path(args.model).name}, 시나리오 '{scn.name}' (화재 {len(scn.fires)}곳), 기준 {args.threshold}")
    for tilt in args.tilts:
        cam = vc.VirtualCamera(scn, vc.CameraSpec(tilt_deg=tilt), tiles)
        hits = total = 0
        for f in scn.fires:
            for k in range(args.headings):
                h = 360 * k / args.headings
                for alt in args.alts:
                    back = alt * math.tan(math.radians(tilt))  # 화재가 화면 가운데 오도록 뒤로 물림
                    lat = f.lat - back * math.cos(math.radians(h)) / 111_320
                    lon = f.lon - back * math.sin(math.radians(h)) / (111_320 * math.cos(math.radians(f.lat)))
                    img = cam.render(vc.Pose(lat, lon, alt, h), k * 0.37)
                    confs = model(img, imgsz=640, conf=0.1, verbose=False)[0].boxes.conf
                    hits += any(float(c) >= args.threshold for c in confs)
                    total += 1
        print(f"  카메라 기울기 {tilt:>4.0f}°: {hits}/{total} 장 탐지 ({hits / total:.0%})")


if __name__ == "__main__":
    main()
