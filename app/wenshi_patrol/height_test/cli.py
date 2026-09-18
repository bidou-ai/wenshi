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
from ..control.route_math import compute_segment_velocity, endpoint_reached, segment_progress
from ..control.route_policy import validate_route
from ..demo import build_demo_lap_segments
from ..jaka import JakaClient
from ..agv import AGVMotionClient, AGVStatusClient
from ..map_utils import load_station_poses
from .capture import HttpRgbdSource, ModelSuite
from .models import HeightTestConfig
from .report import build_report, write_report
from .runner import HeightTestRunner
from .setup import SetupSession
from .setup import expected_field_tag_id
from .storage import HeightTestStore, load_store


class _ArmAdapter:
    def __init__(self, config: dict[str, Any], setup: dict[str, Any] | None = None):
        arm = config["jaka"]
        self.config = config
        self.client = JakaClient(str(arm["ip"]), int(arm.get("port", 10001)), joint_tolerance_deg=float(arm.get("joint_tolerance_deg", .5)), command_interval_s=float(arm.get("command_interval_s", .1)), motion_start_wait_s=float(arm.get("motion_start_wait_s", .5)), motion_stall_timeout_s=float(arm.get("motion_stall_timeout_s", 10.0)))
        self._cancel_requested = threading.Event()
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
        return bool(self.client.joint_move(self.poses[name], self.speed, self.accel, self.timeout, cancel_requested=self._cancel_requested.is_set))

    def move_to_safe(self) -> bool:
        return bool(self.client.joint_move(self.safe, self.speed, self.accel, self.timeout))

    def stop(self) -> None:
        self._cancel_requested.set()
        self.client.stop()

    def close(self) -> None:
        self.client.disconnect()


class _StationRouteAdapter:
    """Follow real plant stops only through the verified LM closed-loop route."""
    def __init__(self, config: dict[str, Any], setup: dict[str, Any]):
        agv = config["agv"]
        self.config = config
        self.status = AGVStatusClient(str(agv["ip"]), int(agv.get("status_port", 19204)), interval_ms=int(agv.get("status_interval_ms", 200)), response_timeout_s=float(agv.get("status_response_timeout_s", .8)))
        self.motion = AGVMotionClient(str(agv["ip"]), int(agv.get("motion_port", 19205)), send_rate_hz=float(config.get("control", {}).get("rate_hz", 20.0)), watchdog_s=float(config.get("safety", {}).get("command_watchdog_s", .3)))
        control = config.get("control", {})
        self.tolerance = float(control.get("endpoint_tolerance_m", .10))
        self.speed = min(abs(float(config.get("field_test", {}).get("route_speed_mps", .10))), .10)
        self.hard_cross_track_m = float(config.get("safety", {}).get("hard_cross_track_m", .25))
        self.station_snap_m = float(config.get("field_test", {}).get("station_snap_m", .25))
        map_path = resolve_config_path(config, str(config["map"]["smap_file"]))
        map_stations = load_station_poses(map_path)
        route_order = list(validate_route(config.get("route", {}).get("station_order", ("LM1", "LM4", "LM3", "LM2"))))
        observations = {
            group_id: tuple(float(station.get("pose", station)[name]) for name in ("x", "y", "angle"))
            for group_id, station in setup["stations"].items()
        }
        max_offset = float(config.get("field_test", {}).get("observation_route_max_offset_m", self.hard_cross_track_m))
        self.segments = build_demo_lap_segments(map_stations, route_order, observations, list(observations), max_offset)
        self._next_segment_index: int | None = None

    def connect_status(self) -> None:
        if not self.status.connect() or not self.status.wait_for_status(timeout=3.0, max_age=1.0):
            raise RuntimeError("AGV status connection or fresh status failed")
        value = self._fresh_status()
        if value.get("is_stop") is not True:
            raise RuntimeError("AGV is not safely stopped")

    def connect_motion(self) -> None:
        if not self.motion.connect():
            raise RuntimeError(self.motion.last_error or "AGV motion connection failed")

    def get_status(self) -> dict[str, Any]:
        return self.status.get_status()

    @staticmethod
    def _has_alarm(value: dict[str, Any]) -> bool:
        return bool(value.get("emergency") or value.get("blocked") or value.get("fatals") or value.get("errors") or value.get("brake"))

    def _fresh_status(self) -> dict[str, Any]:
        if not self.status.wait_for_status(timeout=.8, max_age=.8):
            raise RuntimeError("AGV status is stale")
        value = self.status.get_status()
        if self._has_alarm(value):
            raise RuntimeError("AGV alarm, obstacle, emergency or brake is active")
        if value.get("x") is None or value.get("y") is None or value.get("angle") is None:
            raise RuntimeError("AGV pose is unavailable")
        return value

    def _attachment_index(self, value: dict[str, Any]) -> int:
        x, y = float(value["x"]), float(value["y"])
        for index, segment in enumerate(self.segments):
            if math.hypot(x - segment.start[0], y - segment.start[1]) <= self.station_snap_m:
                return index
        best: tuple[float, int] | None = None
        for index, segment in enumerate(self.segments):
            progress = segment_progress(value, segment, cross_track_gain=0.0)
            along = max(0.0, min(progress.length, progress.along_track))
            px = segment.start[0] + along * math.cos(progress.segment_yaw)
            py = segment.start[1] + along * math.sin(progress.segment_yaw)
            candidate = (math.hypot(x - px, y - py), index)
            if best is None or candidate < best:
                best = candidate
        if best is None or best[0] > self.hard_cross_track_m:
            distance = best[0] if best is not None else float("inf")
            raise RuntimeError(f"AGV is too far from the verified route: {distance:.3f}m")
        return best[1]

    def _run_segment(self, segment: Any) -> None:
        control = self.config.get("control", {})
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            current = self._fresh_status()
            if endpoint_reached(current, segment, self.tolerance):
                self.motion.stop()
                return
            velocity, angular, progress = compute_segment_velocity(
                current,
                segment,
                self.speed,
                float(control.get("cross_track_gain", .8)),
                float(control.get("heading_gain", 1.6)),
                float(control.get("max_angular_speed_rad_s", .35)),
                float(control.get("correction_threshold_m", .04)),
                math.radians(float(control.get("rotate_in_place_threshold_deg", 30.0))),
                math.radians(float(control.get("heading_slowdown_threshold_deg", 10.0))),
                float(control.get("min_heading_scale", .20)),
            )
            if abs(progress.cross_track) > self.hard_cross_track_m:
                raise RuntimeError(f"AGV cross-track limit exceeded: {progress.cross_track:.3f}m")
            self.motion.set_velocity(velocity, angular)
            time.sleep(.05)
        raise RuntimeError(f"AGV route segment timed out: {segment.start_name}->{segment.end_name}")

    def _stop_and_confirm(self) -> bool:
        self.motion.stop()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                value = self._fresh_status()
            except RuntimeError:
                return False
            if value.get("is_stop") is True:
                return True
            time.sleep(.05)
        return False

    def move_to_station(self, station: dict[str, Any]) -> bool:
        group_id = str(station.get("group_id", ""))
        if not group_id or not any(segment.end_name == group_id for segment in self.segments):
            raise RuntimeError(f"unknown route observation station: {group_id or '<missing>'}")
        value = self._fresh_status()
        target = station.get("pose", station)
        if math.hypot(float(value["x"]) - float(target["x"]), float(value["y"]) - float(target["y"])) <= self.tolerance and value.get("is_stop") is True:
            return True
        if self._next_segment_index is None:
            self._next_segment_index = self._attachment_index(value)
        for _ in range(len(self.segments)):
            segment = self.segments[self._next_segment_index]
            self._run_segment(segment)
            self._next_segment_index = (self._next_segment_index + 1) % len(self.segments)
            if segment.end_name == group_id:
                return self._stop_and_confirm()
        raise RuntimeError(f"station is unreachable on verified route: {group_id}")

    def stop(self) -> None:
        self.motion.stop()

    def close(self) -> None:
        self.stop(); self.motion.disconnect(); self.status.disconnect()


def _load_setup(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("stations"), dict):
        raise ValueError("setup snapshot is invalid or unpublished")
    stations = value["stations"]
    expected = {f"left-{index:02d}" for index in range(1, 9)} | {f"right-{index:02d}" for index in range(1, 9)}
    if set(stations) != expected:
        raise ValueError("setup snapshot must contain exactly 16 observation stations")
    manual = [group_id for group_id, station in stations.items() if station.get("pose_source") != "agv_status"]
    if manual:
        raise ValueError("setup snapshot contains stations without AGV realtime poses: " + ", ".join(manual))
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
    if value.get("fatals") or value.get("errors") or value.get("brake"):
        raise RuntimeError("AGV处于报警或刹车状态，不能记录停车点")
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


def _connect_live_hardware(arm: Any, agv: Any, require_motion: bool) -> None:
    """Verify the stationary base before allowing any JAKA or AGV motion."""
    agv.connect_status()
    arm.connect()
    if require_motion:
        if not arm.move_to_safe():
            raise RuntimeError("JAKA 无法回到 home_safe，拒绝启动 AGV")
        agv.connect_motion()


def _warn_if_manual_arm_recovery_required(events: tuple[str, ...] | list[str]) -> bool:
    if not {"safe_retract_skipped", "safe_retract_failed"}.intersection(events):
        return False
    print("警告：机械臂未确认回到 home_safe。必须按下实体急停并人工处理，禁止继续运行。", file=sys.stderr)
    return True


def _stop_runner_and_warn(runner: Any, *, already_warned: bool = False) -> bool:
    """Stop a run and expose late safe-retract failures to the operator."""
    runner.stop("completed")
    events = tuple(getattr(runner, "_events", ()))
    if already_warned:
        return bool({"safe_retract_skipped", "safe_retract_failed"}.intersection(events))
    return _warn_if_manual_arm_recovery_required(events)


def _common_config(args: argparse.Namespace) -> tuple[dict[str, Any], HeightTestConfig]:
    config = load_config(args.config)
    return config, HeightTestConfig.from_project(config)


def _collect_manual_heights(run_dir: Path) -> int:
    """Interactively attach ruler measurements to an existing run."""
    store = load_store(Path(run_dir))
    plant_dirs = sorted(path for path in (store.path / "plants").glob("*") if (path / "results.json").is_file())
    if not plant_dirs:
        raise RuntimeError(f"运行目录没有逐株结果: {store.path}")
    operator = input("人工尺量操作员姓名/编号: ").strip()
    paired = 0
    for plant_dir in plant_dirs:
        current: dict[str, Any] = {}
        manual_path = plant_dir / "manual.json"
        if manual_path.is_file():
            try:
                value = json.loads(manual_path.read_text(encoding="utf-8"))
                current = value if isinstance(value, dict) else {}
            except (OSError, json.JSONDecodeError):
                current = {}
        existing = current.get("measured_height_m")
        suffix = f" [{float(existing):.3f}]" if isinstance(existing, (int, float)) else ""
        while True:
            raw = input(f"{plant_dir.name} 人工株高(m){suffix}，回车保留/跳过，q结束: ").strip().lower()
            if raw == "q":
                print(f"已保存 {paired} 株人工尺量")
                return paired
            if not raw:
                if isinstance(existing, (int, float)):
                    paired += 1
                break
            try:
                measured = float(raw)
                if not math.isfinite(measured) or not 0.0 < measured <= 3.0:
                    raise ValueError
            except ValueError:
                print("株高必须是 0-3 m 之间的数字，请重新输入")
                continue
            store.write_manual_points(plant_dir.name, {
                "measured_height_m": measured,
                "operator": operator,
                "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })
            paired += 1
            break
    print(f"已保存 {paired} 株人工尺量")
    return paired


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
    parser.add_argument("--interactive", action="store_true", help="setup: collect field evidence; report: enter manual ruler heights")
    parser.add_argument("--no-camera", action="store_true", help="setup mode: do not connect D435; provide existing photo paths")
    args = parser.parse_args(argv)

    root = Path(__file__).parents[3]
    if args.command in {"replay", "report"}:
        if not args.path:
            parser.error("replay/report require a run directory")
        if args.command == "report" and args.interactive:
            _collect_manual_heights(Path(args.path))
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

        agv_cfg = config["agv"]
        station_status = AGVStatusClient(str(agv_cfg["ip"]), int(agv_cfg.get("status_port", 19204)), interval_ms=int(agv_cfg.get("status_interval_ms", 200)), response_timeout_s=float(agv_cfg.get("status_response_timeout_s", .8)))
        if not station_status.connect() or not station_status.wait_for_status(timeout=3.0, max_age=1.0):
            station_status.disconnect()
            raise RuntimeError("AGV 状态不可用；不能记录并发布真实停车点")
        try:
            print("逐个登记 16 个真实水稻停车组。LM1~LM4 只是地图转弯点，不在这里登记。")
            for group_id in height.groups:
                while True:
                    command = input(f"{group_id}：将 AGV 驶到水稻旁停稳后回车，q 中止: ").strip().lower()
                    if command == "q": return 2
                    if command:
                        print("只接受回车读取 AGV 实时位姿，或 q 中止")
                        continue
                    try:
                        pose = _live_station_pose(station_status)
                    except RuntimeError as exc:
                        print(f"不能记录 {group_id}: {exc}")
                        continue
                    try:
                        photo = _interactive_photo(setup_camera, setup_root / "stations" / f"{group_id}.jpg", f"{group_id} 停车点")
                        note = input(f"{group_id} 备注(回车跳过): ").strip()
                        session.record_station(group_id, pose, photo=photo, note=note, source="agv_status")
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
    arm = _ArmAdapter(config, setup)
    # Both modes require a fresh AGV stop status; full-route additionally uses
    # the motion port for station waypoints.
    agv = _StationRouteAdapter(config, setup)
    runner = None
    listener_done = threading.Event()
    manual_recovery_warned = False
    try:
        _connect_live_hardware(arm, agv, require_motion=args.command == "full-route")
        runner = HeightTestRunner(height, source, models, store, arm=arm, agv=agv, setup=setup)
        listener_done = _start_stop_listener(runner)
        outcome = runner.run_arm_only(args.group) if args.command == "arm-only" else runner.run_full_route()
        store.finish("finished" if outcome.ok else "needs_review")
        summary = write_report(store.path)
        print(f"run_dir: {store.path}")
        print(json.dumps({"ok": outcome.ok, "errors": outcome.errors, "events": outcome.events, "flags": summary.flags}, ensure_ascii=False, indent=2))
        manual_recovery_warned = _warn_if_manual_arm_recovery_required(outcome.events)
        return 0 if outcome.ok else 2
    finally:
        try:
            if runner is not None:
                _stop_runner_and_warn(runner, already_warned=manual_recovery_warned)
        except Exception: pass
        try: listener_done.set()
        except Exception: pass
        arm.close()
        agv.close()


if __name__ == "__main__":
    raise SystemExit(main())
