"""Interactive read/save teaching workflow for eight arm viewpoints."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from typing import TextIO

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


def capture_all_viewpoints(
    client: TeachingClient,
    session: TeachingSession,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> int:
    output_stream.write("只读取 JAKA 当前关节和 TCP，不会上电、使能，也不会发送运动命令。\n")
    saved = 0
    for name in VIEWPOINT_NAMES:
        while True:
            output_stream.write(f"请人工把机械臂移动到 {name}，确认安全后按回车保存；输入 q 结束：")
            output_stream.flush()
            command = input_stream.readline()
            if command == "" or command.strip().lower() == "q":
                return saved
            if command.strip():
                output_stream.write("只接受回车保存或 q 结束。\n")
                continue
            joint = client.read_joint()
            tcp = client.read_tcp()
            session.save(name, joint, tcp)
            saved += 1
            output_stream.write(f"已保存 {name} ({saved}/{len(VIEWPOINT_NAMES)})\n")
            break
    return saved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="读取并保存 JAKA 八个示教点（只读连接）")
    parser.add_argument("--host", default="192.168.192.160")
    parser.add_argument("--port", type=int, default=10001)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", choices=VIEWPOINT_NAMES)
    parser.add_argument("--save", action="store_true", help="读取当前关节并保存指定点")
    parser.add_argument("--all", action="store_true", help="一次连接依次保存八个示教点")
    args = parser.parse_args(argv)
    if args.save and not args.name:
        parser.error("--save requires --name")
    if args.all and (args.save or args.name):
        parser.error("--all cannot be combined with --save or --name")
    client = TeachingClient(args.host, args.port)
    client.connect()
    try:
        if args.all:
            saved = capture_all_viewpoints(client, TeachingSession(args.output))
            print(json.dumps({"saved": saved, "output": str(args.output)}, ensure_ascii=False))
            return 0 if saved == len(VIEWPOINT_NAMES) else 1
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
