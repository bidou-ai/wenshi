"""Command line entry point for setup, live runs, replay and reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any

import yaml

from ..config import load_config, load_viewpoints, require_joint_pose, resolve_config_path
from ..jaka import JakaClient
from ..agv import AGVMotionClient, AGVStatusClient
from .capture import HttpRgbdSource, ModelSuite
from .models import HeightTestConfig
from .report import build_report, write_report
from .runner import HeightTestRunner
from .setup import SetupSession
from .storage import HeightTestStore


class _ArmAdapter:
    def __init__(self, config: dict[str, Any], setup: dict[str, Any] | None = None):
        arm = config["jaka"]
        self.config = config
        self.client = JakaClient(str(arm["ip"]), int(arm.get("port", 10001)), joint_tolerance_deg=float(arm.get("joint_tolerance_deg", .5)), command_interval_s=float(arm.get("command_interval_s", .1)), motion_start_wait_s=float(arm.get("motion_start_wait_s", .5)), motion_stall_timeout_s=float(arm.get("motion_stall_timeout_s", 10.0)))
        viewpoints = load_viewpoints(config)
        registered = (setup or {}).get("viewpoints", {}) if isinstance(setup, dict) else {}
        self.poses = {
            "camera_left": [float(value) for value in registered.get("left", viewpoints.get("camera_left"))["joint"]] if registered.get("left") else require_joint_pose(viewpoints, "camera_left"),
            "camera": [float(value) for value in registered.get("center", viewpoints.get("camera"))["joint"]] if registered.get("center") else require_joint_pose(viewpoints, "camera"),
            "camera_right": [float(value) for value in registered.get("right", viewpoints.get("camera_right"))["joint"]] if registered.get("right") else require_joint_pose(viewpoints, "camera_right"),
        }
        home = registered.get("home_safe")
        if not home:
            home = viewpoints.get("home_safe")
        if not isinstance(home, dict) or not isinstance(home.get("joint"), list) or len(home["joint"]) != 6:
            raise RuntimeError("setup has no verified home_safe viewpoint; complete setup before motion")
        self.safe = [float(value) for value in home["joint"]]
        self.speed = float(arm.get("move_to_camera_speed_deg_s", 60.0)); self.accel = float(arm.get("accel_deg_s2", 80.0)); self.timeout = float(arm.get("motion_timeout_s", 120.0))

    def connect(self) -> None:
        if not self.client.connect(timeout=3.0) or not self.client.wait_for_joint_state(timeout=3.0):
            raise RuntimeError(self.client.last_error or "JAKA connection failed")

    def move_to_view(self, view: str) -> bool:
        name = {"left": "camera_left", "center": "camera", "right": "camera_right"}[view]
        return bool(self.client.joint_move(self.poses[name], self.speed, self.accel, self.timeout))

    def move_to_safe(self) -> None:
        self.client.joint_move(self.safe, self.speed, self.accel, self.timeout)

    def stop(self) -> None:
        self.client.stop()

    def close(self) -> None:
        self.client.disconnect()


class _StationRouteAdapter:
    """Small waypoint adapter used only by the height-test full-route mode."""
    def __init__(self, config: dict[str, Any]):
        agv = config["agv"]
        self.status = AGVStatusClient(str(agv["ip"]), int(agv.get("status_port", 19204)), interval_ms=int(agv.get("status_interval_ms", 200)), response_timeout_s=float(agv.get("status_response_timeout_s", .8)))
        self.motion = AGVMotionClient(str(agv["ip"]), int(agv.get("motion_port", 19205)), send_rate_hz=float(config.get("control", {}).get("rate_hz", 20.0)), watchdog_s=float(config.get("safety", {}).get("command_watchdog_s", .3)))
        control = config.get("control", {}); self.tolerance = float(control.get("endpoint_tolerance_m", .10)); self.speed = float(config.get("field_test", {}).get("route_speed_mps", .10))

    def connect(self, require_motion: bool = True) -> None:
        if not self.status.connect() or (require_motion and not self.motion.connect()) or not self.status.wait_for_status(timeout=3.0, max_age=1.0):
            raise RuntimeError("AGV status/motion connection or fresh status failed")

    def get_status(self) -> dict[str, Any]:
        return self.status.get_status()

    def move_to_station(self, pose: dict[str, Any]) -> bool:
        target = pose.get("pose", pose); tx, ty = float(target["x"]), float(target["y"])
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            if not self.status.wait_for_status(timeout=.8, max_age=1.0):
                self.stop(); return False
            current = self.status.get_status()
            if current.get("emergency") or current.get("blocked"):
                self.stop(); return False
            dx, dy = tx - float(current.get("x", 0.0)), ty - float(current.get("y", 0.0)); distance = (dx * dx + dy * dy) ** .5
            if distance <= self.tolerance:
                self.stop(); return True
            heading = float(current.get("angle", 0.0)); desired = __import__("math").atan2(dy, dx); error = (desired - heading + __import__("math").pi) % (2 * __import__("math").pi) - __import__("math").pi
            self.motion.set_velocity(min(self.speed, max(.025, distance)), max(-.35, min(.35, 1.6 * error)))
            time.sleep(.05)
        self.stop(); return False

    def stop(self) -> None:
        self.motion.stop()

    def close(self) -> None:
        self.stop(); self.motion.disconnect(); self.status.disconnect()


def _load_setup(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("stations"), dict):
        raise ValueError("setup snapshot is invalid or unpublished")
    return value


def _start_stop_listener(runner: HeightTestRunner) -> threading.Event:
    done = threading.Event()
    def listen() -> None:
        while not done.is_set():
            try:
                line = sys.stdin.readline()
            except (OSError, EOFError):
                return
            if not line:
                return
            if line.strip().lower() in {"q", "quit", "stop"}:
                runner.stop("operator input")
                return
    threading.Thread(target=listen, name="height-test-stop-listener", daemon=True).start()
    return done


def _common_config(args: argparse.Namespace) -> tuple[dict[str, Any], HeightTestConfig]:
    config = load_config(args.config)
    return config, HeightTestConfig.from_project(config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wenshi RGB-D multi-method height test")
    parser.add_argument("command", choices=("setup", "arm-only", "full-route", "replay", "report"))
    parser.add_argument("path", nargs="?", help="run directory for replay/report")
    parser.add_argument("--config", default=str(Path(__file__).parents[3] / "config" / "wenshi.yaml"))
    parser.add_argument("--run-root", default=None)
    parser.add_argument("--setup", dest="setup_path", default=None)
    parser.add_argument("--group", default="left-01")
    parser.add_argument("--confirm-motion", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--interactive", action="store_true", help="setup mode: collect IDs, offsets and station poses from stdin")
    args = parser.parse_args(argv)

    root = Path(__file__).parents[3]
    if args.command in {"replay", "report"}:
        if not args.path:
            parser.error("replay/report require a run directory")
        summary = write_report(Path(args.path))
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2)); return 0 if all(summary.flags.values()) else 2

    config, height = _common_config(args)
    if args.command == "setup":
        stamp = time.strftime("setup_%Y%m%d_%H%M%S")
        setup_root = root / "runtime" / "height_tests" / stamp
        session = SetupSession.begin(setup_root, height)
        print(f"setup workspace: {setup_root}")
        if not args.interactive:
            print("请使用 --interactive 进入现场登记；也可在 Python 中调用 SetupSession.record_tag/record_station/record_water_offset。")
            return 0
        session.operator = input("操作员姓名/编号: ").strip()
        print("逐株输入 Tag ID。C 排仍登记但不要求水面补偿；输入 q 可中止。")
        from .models import TagObservation
        for plant_id in (plant.plant_id for plant in height.plants):
            value = input(f"{plant_id} Tag ID: ").strip()
            if value.lower() == "q": return 2
            photo_path = input(f"{plant_id} Tag 照片路径(回车跳过): ").strip()
            photo = None
            if photo_path:
                import cv2
                photo = cv2.imread(photo_path)
                if photo is None: raise ValueError(f"无法读取 Tag 照片: {photo_path}")
            session.record_tag(plant_id, TagObservation(int(value), family=height.tag_family), "side", photo)
            if plant_id in height.active_plant_ids:
                offset = input(f"{plant_id} 卡槽顶到水面高度(m): ").strip()
                session.record_water_offset(plant_id, float(offset))
        print("逐站输入 AGV 地图坐标 x y angle(rad)，共 16 站。")
        for group_id in height.groups:
            value = input(f"{group_id} x y angle: ").strip().split()
            if len(value) != 3: raise ValueError("station pose requires x y angle")
            session.record_station(group_id, {"x": float(value[0]), "y": float(value[1]), "angle": float(value[2])})
        print("登记机械臂四个视角。left/center/right 使用当前 viewpoints.json，home_safe 读取 JAKA 当前关节并由操作员确认。")
        viewpoints = load_viewpoints(config)
        for name, source_name in (("left", "camera_left"), ("center", "camera"), ("right", "camera_right")):
            session.record_viewpoint(name, require_joint_pose(viewpoints, source_name))
        arm_cfg = config["jaka"]
        probe = JakaClient(str(arm_cfg["ip"]), int(arm_cfg.get("port", 10001)))
        if not probe.connect(timeout=3.0) or not probe.wait_for_joint_state(timeout=3.0):
            probe.disconnect(); raise RuntimeError("无法读取 JAKA 当前关节，不能发布未经验证的 home_safe")
        current_joint = probe.snapshot().get("joint"); probe.disconnect()
        if not isinstance(current_joint, list) or len(current_joint) != 6:
            raise RuntimeError("JAKA 当前关节无效，不能发布 home_safe")
        if input("确认当前 JAKA 姿态是安全回撤姿态？输入 yes: ").strip().lower() != "yes":
            raise RuntimeError("操作员未确认 home_safe")
        session.record_viewpoint("home_safe", current_joint)
        destination = root / "runtime" / "height_tests" / "field_height_setup.json"
        session.publish(destination)
        print(f"published setup: {destination}")
        return 0

    if not args.confirm_motion:
        print("拒绝运动：arm-only/full-route 必须显式提供 --confirm-motion", file=sys.stderr); return 2
    setup_path = Path(args.setup_path) if args.setup_path else root / "runtime" / "height_tests" / "field_height_setup.json"
    if not setup_path.is_file():
        print(f"未找到已发布 setup: {setup_path}", file=sys.stderr); return 2
    setup = _load_setup(setup_path)
    run_root = Path(args.run_root) if args.run_root else root / "runtime" / "height_tests"
    store = HeightTestStore.create(run_root, height.to_dict())
    store.write_setup_snapshot(setup)
    for group_id, station in setup.get("stations", {}).items():
        store.write_station(group_id, station)
    store.append_event("run_started", mode=args.command, setup=str(setup_path))
    camera_url = str(config["camera"]["server_url"]); source = HttpRgbdSource(camera_url, float(config["camera"].get("timeout_s", 1.5)))
    models = ModelSuite(resolve_config_path(config, height.plant_model_path), resolve_config_path(config, height.panicle_model_path))
    arm = _ArmAdapter(config, setup); arm.connect()
    # Both modes require a fresh AGV stop status; full-route additionally uses
    # the motion port for station waypoints.
    agv = _StationRouteAdapter(config)
    runner = None
    listener_done = threading.Event()
    try:
        # The base may move only after the arm has returned to the verified safe pose.
        if args.command == "full-route":
            arm.move_to_safe()
        agv.connect(require_motion=args.command == "full-route")
        runner = HeightTestRunner(height, source, models, store, arm=arm, agv=agv, setup=setup)
        listener_done = _start_stop_listener(runner)
        outcome = runner.run_arm_only(args.group) if args.command == "arm-only" else runner.run_full_route()
        store.finish("finished" if outcome.ok else "needs_review")
        summary = write_report(store.path)
        print(f"run_dir: {store.path}")
        print(json.dumps({"ok": outcome.ok, "errors": outcome.errors, "flags": summary.flags}, ensure_ascii=False, indent=2))
        return 0 if outcome.ok else 2
    finally:
        try:
            if runner is not None:
                runner.stop("completed")
        except Exception: pass
        try: listener_done.set()
        except Exception: pass
        arm.close()
        agv.close()


if __name__ == "__main__":
    raise SystemExit(main())
