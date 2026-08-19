"""Interactive read/save teaching workflow for eight arm viewpoints."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

try:
    from .paths import load_json, save_json_atomic
    from .teach_protocol import TeachingClient
except ImportError:  # direct module execution
    from paths import load_json, save_json_atomic
    from teach_protocol import TeachingClient


VIEWPOINT_NAMES = (
    "home_safe", "camera", "camera_left", "camera_right",
    "left_pre", "left_photo", "right_pre", "right_photo",
)


class TeachingSession:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        self.value = load_json(self.path) if self.path.exists() else {}

    def save(self, name: str, joint: list[float], tcp: list[float] | None) -> dict[str, Any]:
        if name not in VIEWPOINT_NAMES:
            raise ValueError(f"unknown viewpoint: {name}")
        if len(joint) != 6:
            raise ValueError("joint pose must contain six values")
        entry = {
            "joint": [float(item) for item in joint],
            "tcp": [float(item) for item in tcp[:6]] if tcp and len(tcp) >= 6 else None,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.value[name] = entry
        save_json_atomic(self.path, self.value)
        return entry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="读取并保存 JAKA 八个示教点（只读连接）")
    parser.add_argument("--host", default="192.168.192.160")
    parser.add_argument("--port", type=int, default=10001)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", choices=VIEWPOINT_NAMES)
    parser.add_argument("--save", action="store_true", help="读取当前关节并保存指定点")
    args = parser.parse_args(argv)
    if args.save and not args.name:
        parser.error("--save requires --name")
    client = TeachingClient(args.host, args.port)
    client.connect()
    try:
        joint = client.read_joint()
        tcp = client.read_tcp()
    finally:
        client.close()
    print(json.dumps({"joint": joint, "tcp": tcp}, ensure_ascii=False, indent=2))
    if args.save:
        print(TeachingSession(args.output).save(args.name, joint, tcp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

