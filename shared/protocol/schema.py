"""메시지 스키마를 JSON Schema 파일로 내보낸다 (프론트엔드 TS 타입 생성용).

실행: python -m protocol.schema
결과: shared/schema/*.json
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from . import messages, mission

MODELS = {
    "Telemetry": messages.Telemetry,
    "DroneStatus": messages.DroneStatus,
    "Command": messages.Command,
    "CommandAck": messages.CommandAck,
    "DroneMission": messages.DroneMission,
    "StatusText": messages.StatusText,
    "DetectionFrame": messages.DetectionFrame,
    "EdgeStatus": messages.EdgeStatus,
    "Snapshot": messages.Snapshot,
    "SnapshotCheck": messages.SnapshotCheck,
    "CameraPreview": messages.CameraPreview,
    "SimFires": messages.SimFires,
    "EventMessage": messages.EventMessage,
    "ServerStatus": messages.ServerStatus,
    "MissionPlan": mission.MissionPlan,
}


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "schema"
    out.mkdir(exist_ok=True)
    for name, model in MODELS.items():
        schema = TypeAdapter(model).json_schema()
        (out / f"{name}.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(MODELS)}개 스키마 → {out}")


if __name__ == "__main__":
    main()
