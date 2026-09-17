"""Command line entry point for setup, live runs, replay and reports."""

from __future__ import annotations

import argparse
import math
import json
from pathlib import Path
import sys
import threading
import time
from typing import Any

import cv2
import numpy as np

from ..config import load_config, load_viewpoints, require_joint_pose, resolve_config_path
from ..jaka import JakaClient
from ..agv import AGVMotionClient, AGVStatusClient
from .capture import HttpRgbdSource, ModelSuite
from .models import HeightTestConfig
from .report import build_report, write_report
from .runner import HeightTestRunner
from .setup import SetupSession
from .setup import expected_field_tag_id
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


def _live_station_pose(status: Any) -> dict[str, float]:
    """Read a fresh, explicitly stopped AGV pose for a real plant station."""
    if not status.wait_for_status(timeout=1.5, max_age=0.8):
        raise RuntimeError("AGV定位状态过期，不能记录停车点")
    value = status.get_status()
    if value.get("emergency"):
        raise RuntimeError("AGV处于急停，不能记录停车点")
    if value.get("blocked"):
        raise RuntimeError("AGV处于阻挡状态，不能记录停车点")
    if value.get("is_stop") is not True:
        raise RuntimeError("AGV尚未停稳，不能记录停车点")
    try:
        pose = {name: float(value[name]) for name in ("x", "y", "angle")}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("AGV没有有效 x/y/angle 位姿") from exc
    if not all(math.isfinite(item) for item in pose.values()):
        raise RuntimeError("AGV位姿包含非有限值")
    return pose


def _interactive_photo(camera: Any | None, destination: Path, label: str) -> Any | None:
    """Capture one operator-requested RGB evidence image, or import a path."""
    answer = input(f"{label}：回车拍照，或输入已有图片路径: ").strip()
    if answer.lower() in {"skip", "s"}:
        raise RuntimeError("现场 setup 必须保存照片，不能跳过")
    if answer:
        image = cv2.imread(str(Path(answer).expanduser()), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"无法读取图片: {answer}")
    else:
        if camera is None:
            raise RuntimeError("未启用 D435；请提供已有图片路径或使用相机运行 setup")
        image = camera.color()
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise RuntimeError("D435 返回空 RGB 图片")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        raise OSError(f"图片写入失败: {destination}")
    print(f"照片已保存: {destination}")
    return image


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
    parser.add_argument("--interactive", action="store_true", help="setup mode: collect fixed Tag IDs, photos and real station poses from stdin")
    parser.add_argument("--no-camera", action="store_true", help="setup mode: do not connect D435; provide existing photo paths")
    parser.add_argument("--no-agv", action="store_true", help="setup mode: do not connect AGV; type station coordinates manually")
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
        session.require_photo_evidence = bool(args.interactive)
        print(f"setup workspace: {setup_root}")
        if not args.interactive:
            print("请使用 --interactive 进入现场登记；也可在 Python 中调用 SetupSession.record_tag/record_station/record_water_offset。")
            return 0
        session.operator = input("操作员姓名/编号: ").strip()
        setup_camera = None
        if not args.no_camera:
            from ..demo import DemoCamera
            setup_camera = DemoCamera(str(config["camera"]["server_url"]), float(config["camera"].get("timeout_s", 1.5)))
            health = setup_camera.health()
            if not health.get("ok"):
                raise RuntimeError(f"D435 健康检查失败: {health.get('error', 'unknown')}")
            print("D435 已连接：Tag 和停车点照片按回车直接拍摄。")
        else:
            print("已禁用 D435：每个 Tag/停车点必须输入已有 RGB 图片路径。")

        from .models import TagObservation
        print("先拍摄 ID 0 标定板（不属于任何植株）。输入 q 可中止。")
        board_photo = _interactive_photo(setup_camera, setup_root / "reference" / "tag-0-calibration-board.jpg", "标定板 Tag 0")
        session.record_calibration_board(board_photo)

        print("按现场贴纸登记 32 株：A=1..8，B-L=16..9，B-R=17..24，C=32..25。回车采用预期编号。")
        for plant in height.plants:
            plant_id = plant.plant_id
            expected = plant.tag_id if plant.tag_id is not None else expected_field_tag_id(plant_id)
            while True:
                value = input(f"{plant_id} Tag ID [{expected}]: ").strip()
                if value.lower() == "q": return 2
                if not value:
                    value = str(expected) if expected is not None else ""
                try:
                    tag_id = int(value)
                    if not 1 <= tag_id <= 32:
                        raise ValueError("身份 Tag 必须是 1-32；0 是标定板")
                    photo = _interactive_photo(setup_camera, setup_root / "tags" / f"{plant_id}.jpg", f"{plant_id} Tag")
                    session.record_tag(plant_id, TagObservation(tag_id, family=height.tag_family), "side", photo)
                    break
                except (ValueError, OSError, RuntimeError) as exc:
                    print(f"登记失败，请重试 {plant_id}: {exc}")
            if plant_id in height.active_plant_ids:
                while True:
                    offset = input(f"{plant_id} 卡槽顶到水面高度(m): ").strip()
                    if offset.lower() == "q": return 2
                    try:
                        session.record_water_offset(plant_id, float(offset))
                        break
                    except (TypeError, ValueError) as exc:
                        print(f"水面补偿无效，请重新输入: {exc}")

        station_status = None
        if not args.no_agv:
            agv_cfg = config["agv"]
            station_status = AGVStatusClient(str(agv_cfg["ip"]), int(agv_cfg.get("status_port", 19204)), interval_ms=int(agv_cfg.get("status_interval_ms", 200)), response_timeout_s=float(agv_cfg.get("status_response_timeout_s", .8)))
            if not station_status.connect() or not station_status.wait_for_status(timeout=3.0, max_age=1.0):
                station_status.disconnect()
                station_status = None
                print("警告：AGV 状态不可用；每个停车点改为手工输入 x y angle。")
        try:
            print("逐个登记 16 个真实水稻停车组。LM1~LM4 只是地图转弯点，不在这里登记。")
            for group_id in height.groups:
                while True:
                    command = input(f"{group_id}：将 AGV 驶到水稻旁停稳后回车，输入 m 手工坐标，q 中止: ").strip().lower()
                    if command == "q": return 2
                    if command in {"m", "manual"} or station_status is None:
                        raw = input(f"{group_id} x y angle(rad): ").strip().split()
                        if len(raw) != 3:
                            print("需要三个数字 x y angle")
                            continue
                        try:
                            pose = {"x": float(raw[0]), "y": float(raw[1]), "angle": float(raw[2])}
                        except ValueError:
                            print("坐标必须是数字")
                            continue
                    else:
                        try:
                            pose = _live_station_pose(station_status)
                        except RuntimeError as exc:
                            print(f"不能记录 {group_id}: {exc}")
                            continue
                    try:
                        photo = _interactive_photo(setup_camera, setup_root / "stations" / f"{group_id}.jpg", f"{group_id} 停车点")
                        note = input(f"{group_id} 备注(回车跳过): ").strip()
                        session.record_station(group_id, pose, photo=photo, note=note)
                        print(f"已登记 {group_id}: x={pose['x']:.3f} y={pose['y']:.3f} angle={pose['angle']:.3f}")
                        break
                    except (ValueError, OSError, RuntimeError) as exc:
                        print(f"停车点登记失败，请重试: {exc}")
        finally:
            if station_status is not None:
                station_status.disconnect()
        print("登记机械臂四个视角。left/center/right 使用当前 viewpoints.json，home_safe 读取 JAKA 当前关节并由操作员确认。")
        viewpoints = load_viewpoints(config)
        if input("确认 camera_left/camera/camera_right 已现场低速验证且无碰撞风险？输入 yes: ").strip().lower() != "yes":
            raise RuntimeError("操作员未确认三视角")
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
