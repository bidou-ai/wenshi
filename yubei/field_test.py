"""Single-entry Wenshi field teaching and hardware test session.

This module is intentionally separate from the formal patrol node.  It owns one
AGV/JAKA client pair for an interactive field session and never changes JAKA
power or enable state.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import queue
import threading
import time
from typing import Any, TextIO

import cv2

try:
    from .http_camera import HttpCameraClient
    from .paths import save_json_atomic
    from .teach_protocol import TeachingClient
    from .teach_viewpoints import TeachingSession, VIEWPOINT_NAMES
except ImportError:  # direct module execution
    from http_camera import HttpCameraClient
    from paths import save_json_atomic
    from teach_protocol import TeachingClient
    from teach_viewpoints import TeachingSession, VIEWPOINT_NAMES

from wenshi_patrol.agv import AGVMotionClient, AGVStatusClient
from wenshi_patrol.config import load_config, resolve_config_path
from wenshi_patrol.control.route_math import (
    Segment,
    SegmentProgress,
    compute_segment_velocity,
    endpoint_reached,
    make_segments,
    segment_progress,
)
from wenshi_patrol.map_utils import load_station_poses
from wenshi_patrol.jaka import JakaClient


def resolve_console_input(input_stream: TextIO, tty_opener=open) -> tuple[TextIO, TextIO | None]:
    """Use the controlling terminal when a launcher redirected stdin."""
    if input_stream.isatty():
        return input_stream, None
    try:
        tty_stream = tty_opener("/dev/tty", "r", encoding="utf-8")
    except (OSError, TypeError):
        return input_stream, None
    return tty_stream, tty_stream


def build_closed_route(
    stations: dict[str, tuple[float, float, float]], order: list[str]
) -> list[Segment]:
    """Build the Wens1 route with the closing segment back to the first point."""
    return make_segments(stations, order, loop=True)


def nearest_route_segment(
    status: dict[str, Any], segments: list[Segment]
) -> tuple[int, float, float]:
    """Return nearest segment index, signed cross-track, and endpoint distance."""
    best: tuple[float, int, float, float] | None = None
    for index, segment in enumerate(segments):
        progress = segment_progress(status, segment, cross_track_gain=0.0)
        along = max(0.0, min(progress.length, progress.along_track))
        distance = math.hypot(
            float(status["x"]) - (segment.start[0] + along * math.cos(progress.segment_yaw)),
            float(status["y"]) - (segment.start[1] + along * math.sin(progress.segment_yaw)),
        )
        candidate = (distance, index, progress.cross_track, progress.distance_to_goal)
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None:
        raise ValueError("地图没有可用路线段")
    return best[1], best[2], best[0]


def route_attachment(
    status: dict[str, Any],
    stations: dict[str, tuple[float, float, float]],
    order: list[str],
    segments: list[Segment],
    station_snap_m: float,
) -> tuple[int, str | None]:
    """Choose a forward route segment, preferring a nearby station.

    At a corner both adjoining segments can be equally close.  Snapping to the
    station first prevents a test started just after LM4 from incorrectly
    re-running LM1 -> LM4.
    """
    x = float(status["x"])
    y = float(status["y"])
    nearest_station: tuple[float, str] | None = None
    for name in order:
        sx, sy, _ = stations[name]
        distance = math.hypot(x - sx, y - sy)
        if nearest_station is None or distance < nearest_station[0]:
            nearest_station = (distance, name)
    if nearest_station is not None and nearest_station[0] <= float(station_snap_m):
        station_name = nearest_station[1]
        for index, segment in enumerate(segments):
            if segment.start_name == station_name:
                return index, station_name
    index, _cross_track, _distance = nearest_route_segment(status, segments)
    return index, None


def blocked_recovery_state(
    status: dict[str, Any],
    clear_since: float | None,
    *,
    now: float,
    clear_s: float,
) -> tuple[str, float | None]:
    """Return the safe response to one AGV blocked/emergency status sample."""
    if bool(status.get("emergency")):
        return "abort", clear_since
    if bool(status.get("blocked")):
        return "pause", None
    if clear_since is None:
        return "waiting", now
    if now - clear_since >= max(float(clear_s), 0.0):
        return "resume", clear_since
    return "waiting", clear_since


def route_segments_until_station(
    segments: list[Segment], attach_index: int, station_name: str
) -> list[Segment]:
    """Return forward-only segments from the attachment segment to a station."""
    if not segments:
        raise ValueError("路线为空")
    selected: list[Segment] = []
    index = int(attach_index) % len(segments)
    for _ in range(len(segments)):
        segment = segments[index]
        selected.append(segment)
        if segment.end_name == station_name:
            return selected
        index = (index + 1) % len(segments)
    raise ValueError(f"闭环路线无法到达站点: {station_name}")


def validate_viewpoints(path: Path) -> list[str]:
    """Return human-readable errors for an eight-point joint file."""
    errors: list[str] = []
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取示教文件: {exc}"]
    if not isinstance(value, dict):
        return ["示教文件不是 JSON 对象"]
    for name in VIEWPOINT_NAMES:
        pose = value.get(name)
        joint = pose.get("joint") if isinstance(pose, dict) else None
        if not isinstance(joint, list) or len(joint) != 6:
            errors.append(f"缺少或无效示教点: {name}")
            continue
        try:
            [float(item) for item in joint]
        except (TypeError, ValueError):
            errors.append(f"示教点关节值无效: {name}")
    return errors


class CameraPreview:
    def __init__(self, url: str, timeout_s: float, enabled: bool, output_dir: Path):
        self.client = HttpCameraClient(url, timeout_s=timeout_s)
        self.enabled = bool(enabled)
        self.output_dir = Path(output_dir)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.last_frame = None
        self.last_error = ""
        self._window_created = False

    def start(self):
        if not self.enabled or (self.thread and self.thread.is_alive()):
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="field-camera-preview", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                frame = self.client.frame()
                self.last_frame = frame
                image = frame.color.copy()
                cv2.putText(image, f"seq={frame.seq}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 80), 2)
                cv2.imshow("Wenshi field test camera", image)
                self._window_created = True
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    self.stop_event.set()
                    break
            except Exception as exc:  # preview failure must not command motion
                self.last_error = str(exc)
                time.sleep(0.2)
        if self._window_created:
            try:
                cv2.destroyWindow("Wenshi field test camera")
            except cv2.error:
                pass
            self._window_created = False

    def snapshot(self, path: Path) -> bool:
        frame = self.last_frame
        if frame is None:
            try:
                frame = self.client.frame()
            except Exception as exc:
                self.last_error = str(exc)
                return False
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return bool(cv2.imwrite(str(path), frame.color))

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        self.thread = None
        if self._window_created:
            try:
                cv2.destroyWindow("Wenshi field test camera")
            except cv2.error:
                pass
            self._window_created = False


class QueueInput:
    """TextIO-shaped bridge from the main console to a worker prompt."""

    def __init__(self, commands: queue.Queue[str]):
        self.commands = commands

    def readline(self) -> str:
        return self.commands.get()


class ArmPointTester:
    def __init__(self, config: dict[str, Any], log):
        arm = config["jaka"]
        self.log = log
        self.client = JakaClient(
            ip=str(arm["ip"]),
            port=int(arm.get("port", 10001)),
            joint_tolerance_deg=float(arm.get("joint_tolerance_deg", 0.5)),
            command_interval_s=float(arm.get("command_interval_s", 0.1)),
            motion_start_wait_s=float(arm.get("motion_start_wait_s", 0.5)),
            motion_stall_timeout_s=float(arm.get("motion_stall_timeout_s", 10.0)),
            log=log,
        )
        self.speed = min(float(arm.get("fixed_transition_speed_deg_s", 60.0)), 20.0)
        self.accel = min(float(arm.get("accel_deg_s2", 80.0)), 40.0)
        self.timeout = float(arm.get("motion_timeout_s", 120.0))

    def run(self, path: Path, input_stream: TextIO, output_stream: TextIO) -> dict[str, Any]:
        errors = validate_viewpoints(path)
        if errors:
            return {"ok": False, "completed": [], "error": "；".join(errors)}
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        completed: list[str] = []
        if not self.client.connect(timeout=float(self.client.command_interval_s + 3.0)):
            return {"ok": False, "completed": completed, "error": self.client.last_error or "JAKA 连接失败"}
        try:
            if not self.client.wait_for_joint_state(timeout=2.0):
                return {"ok": False, "completed": completed, "error": "JAKA 无法读取当前关节角"}
            for name in VIEWPOINT_NAMES:
                output_stream.write(f"确认工作空间安全后按回车运动到 {name}，输入 q 停止：")
                output_stream.flush()
                command = input_stream.readline().strip().lower()
                if command in {"q", "stop"}:
                    self.client.stop()
                    return {"ok": False, "completed": completed, "error": "用户停止或输入结束"}
                target = [float(item) for item in value[name]["joint"]]
                if not self.client.joint_move(target, self.speed, self.accel, self.timeout):
                    self.client.stop()
                    return {"ok": False, "completed": completed, "error": self.client.last_error or f"运动到 {name} 失败"}
                completed.append(name)
                self.log(f"arm_point_completed name={name}")
            return {"ok": True, "completed": completed, "error": ""}
        finally:
            self.client.disconnect()


class RouteRunner:
    def __init__(self, config: dict[str, Any], log):
        self.config = config
        self.log = log
        agv = config["agv"]
        control = config["control"]
        safety = config["safety"]
        self.status = AGVStatusClient(str(agv["ip"]), int(agv.get("status_port", 19204)), log=log)
        self.motion = AGVMotionClient(str(agv["ip"]), int(agv.get("motion_port", 19205)), log=log)
        field_test = config.get("field_test", {})
        self.speed = float(field_test.get("route_speed_mps", control.get("test_speed_mps", 0.05)))
        if self.speed <= 0.0:
            raise ValueError("field_test.route_speed_mps 必须大于 0")
        self.max_cross = float(safety.get("hard_cross_track_m", 0.25))
        self.tolerance = float(control.get("endpoint_tolerance_m", 0.10))
        self.blocked_clear_s = float(field_test.get("blocked_clear_s", safety.get("blocked_clear_s", 2.0)))
        self.station_snap_m = float(field_test.get("station_snap_m", 0.25))
        self.stations = load_station_poses(resolve_config_path(config, str(config["map"]["smap_file"])))
        self.order = [str(item) for item in config["route"]["station_order"]]
        self.segments = build_closed_route(self.stations, self.order)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()
        self.motion.stop()

    def _precheck(self) -> tuple[bool, str, dict[str, Any] | None]:
        if not self.status.connected and not self.status.connect():
            return False, "AGV 状态端口连接失败", None
        if not self.motion.connected and not self.motion.connect():
            return False, self.motion.last_error or "AGV 运动端口连接失败", None
        if not self.status.wait_for_status(timeout=3.0, max_age=0.8):
            return False, "AGV 定位状态不新鲜", None
        current = self.status.get_status()
        if current.get("emergency") or current.get("blocked"):
            return False, "AGV 当前急停或阻挡状态未解除", current
        if current.get("x") is None or current.get("y") is None or current.get("angle") is None:
            return False, "AGV 没有有效 x/y/angle", current
        return True, "ok", current

    def _wait_until_unblocked(self) -> tuple[bool, str]:
        """Pause in place and resume the same segment after a stable clear state."""
        clear_since: float | None = None
        logged_pause = False
        while not self.stop_event.is_set():
            if not self.status.wait_for_status(timeout=0.8, max_age=0.8):
                self.stop()
                return False, "AGV 定位状态过期（避障等待期间）"
            status = self.status.get_status()
            action, clear_since = blocked_recovery_state(
                status,
                clear_since,
                now=time.monotonic(),
                clear_s=self.blocked_clear_s,
            )
            self.motion.stop()
            if action == "abort":
                self.stop()
                return False, "AGV 急停"
            if action == "pause":
                if not logged_pause:
                    self.log(f"route_blocked_pause reason={status.get('block_reason')}")
                    logged_pause = True
                clear_since = None
            elif action == "resume":
                self.log(f"route_blocked_resume clear_s={self.blocked_clear_s:.1f}")
                return True, "阻挡已解除，继续当前路线段"
            time.sleep(0.05)
        return False, "用户停止"

    def _run_segments(self, segments: list[Segment]) -> tuple[bool, str]:
        for segment in segments:
            while not self.stop_event.is_set():
                if not self.status.wait_for_status(timeout=0.8, max_age=0.8):
                    self.stop()
                    return False, "AGV 定位状态过期"
                status = self.status.get_status()
                if status.get("emergency"):
                    self.stop()
                    return False, "AGV 急停"
                if status.get("blocked"):
                    recovered, recovery_message = self._wait_until_unblocked()
                    if not recovered:
                        return False, recovery_message
                    continue
                progress = segment_progress(status, segment, float(self.config["control"].get("cross_track_gain", 0.8)))
                if abs(progress.cross_track) > self.max_cross:
                    self.stop()
                    return False, f"路线横向偏差超过限制: {progress.cross_track:.3f}m"
                if endpoint_reached(status, segment, self.tolerance):
                    self.motion.stop()
                    self.log(f"route_segment_completed {segment.start_name}->{segment.end_name}")
                    break
                speed, angular, _ = compute_segment_velocity(
                    status, segment, self.speed,
                    float(self.config["control"].get("cross_track_gain", 0.8)),
                    float(self.config["control"].get("heading_gain", 1.6)),
                    float(self.config["control"].get("max_angular_speed_rad_s", 0.35)),
                    float(self.config["control"].get("correction_threshold_m", 0.04)),
                    math.radians(float(self.config["control"].get("rotate_in_place_threshold_deg", 30.0))),
                    math.radians(float(self.config["control"].get("heading_slowdown_threshold_deg", 10.0))),
                    float(self.config["control"].get("min_heading_scale", 0.2)),
                )
                self.motion.set_velocity(speed, angular)
                time.sleep(0.05)
        self.motion.stop()
        return not self.stop_event.is_set(), "用户停止" if self.stop_event.is_set() else "路线完成"

    def run_one_loop(self) -> dict[str, Any]:
        ok, message, status = self._precheck()
        if not ok:
            self.stop()
            return {"ok": False, "attach_segment": None, "route": [], "error": message}
        index, snapped_station = route_attachment(
            status,
            self.stations,
            self.order,
            self.segments,
            self.station_snap_m,
        )
        _nearest_index, cross_track, _distance = nearest_route_segment(status, self.segments)
        if abs(cross_track) > self.max_cross:
            self.stop()
            return {"ok": False, "attach_segment": index, "route": [], "error": f"当前位置离地图路线过远: {cross_track:.3f}m"}
        station_x, station_y, _ = self.stations[self.order[0]]
        station_distance = math.hypot(float(status["x"]) - station_x, float(status["y"]) - station_y)
        if snapped_station:
            self.log(f"route_attachment_snapped station={snapped_station} segment={self.segments[index].start_name}->{self.segments[index].end_name}")
        attach = [] if station_distance <= self.tolerance else route_segments_until_station(self.segments, index, self.order[0])
        loop = list(self.segments)
        # If attach already reaches LM1, do not duplicate the closing segment before the loop.
        ok_attach, attach_message = self._run_segments(attach)
        if not ok_attach:
            return {"ok": False, "attach_segment": index, "route": [(s.start_name, s.end_name) for s in attach], "error": attach_message}
        ok_loop, loop_message = self._run_segments(loop)
        return {"ok": ok_loop, "attach_segment": index, "route": [(s.start_name, s.end_name) for s in attach + loop], "error": "" if ok_loop else loop_message}


class RosFieldPublisher:
    """Read-only RViz publisher used by the yubei hardware test session."""

    def __init__(self, config: dict[str, Any], route: RouteRunner, log):
        import rclpy
        from geometry_msgs.msg import PoseStamped
        from nav_msgs.msg import OccupancyGrid
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String
        from visualization_msgs.msg import MarkerArray

        self._rclpy = rclpy
        self._PoseStamped = PoseStamped
        self._OccupancyGrid = OccupancyGrid
        self._String = String
        self._MarkerArray = MarkerArray
        self._route = route
        self._log = log
        self._stopped = False
        self._thread: threading.Thread | None = None

        initialized = False
        try:
            rclpy.init(args=[])
            initialized = True
            self._node = Node("wenshi_field_test_visualizer")
            topics = config["topics"]
            transient = QoSProfile(depth=1)
            transient.durability = DurabilityPolicy.TRANSIENT_LOCAL
            transient.reliability = ReliabilityPolicy.RELIABLE
            self._map_pub = self._node.create_publisher(OccupancyGrid, topics["map"], transient)
            self._marker_pub = self._node.create_publisher(MarkerArray, topics["markers"], transient)
            self._pose_pub = self._node.create_publisher(PoseStamped, topics["agv_pose"], 10)
            self._state_pub = self._node.create_publisher(String, topics["state"], transient)
            map_path = resolve_config_path(config, str(config["map"]["smap_file"]))
            stamp = self._node.get_clock().now().to_msg()
            from wenshi_patrol.map_utils import make_occupancy_grid, make_station_markers

            self._map_message = make_occupancy_grid(map_path, stamp)
            self._marker_message = make_station_markers(map_path, stamp)
            self._node.create_timer(0.2, self._publish)
            self._log("field_test_rviz_ready topics=/map,/rice/patrol/map_markers,/rice/chassis/pose,/camera/color/image_raw")
        except Exception:
            if initialized and rclpy.ok():
                rclpy.shutdown()
            raise

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._spin, name="field-test-rviz", daemon=True)
        self._thread.start()

    def _spin(self):
        try:
            self._rclpy.spin(self._node)
        except Exception as exc:
            if not self._stopped:
                self._log(f"field_test_rviz_error {exc}")

    def _publish(self):
        stamp = self._node.get_clock().now().to_msg()
        self._map_message.header.stamp = stamp
        self._marker_pub.publish(self._marker_message)
        self._map_pub.publish(self._map_message)
        status = self._route.status.get_status()
        if status.get("x") is not None and status.get("y") is not None:
            pose = self._PoseStamped()
            pose.header.stamp = stamp
            pose.header.frame_id = "map"
            pose.pose.position.x = float(status["x"])
            pose.pose.position.y = float(status["y"])
            yaw = float(status.get("angle") or 0.0)
            pose.pose.orientation.z = math.sin(yaw * 0.5)
            pose.pose.orientation.w = math.cos(yaw * 0.5)
            self._pose_pub.publish(pose)
        state = self._String()
        state.data = "FIELD_TEST: route test / teaching console"
        self._state_pub.publish(state)

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        try:
            self._node.destroy_node()
        finally:
            if self._rclpy.ok():
                self._rclpy.shutdown()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)


class FieldTestSession:
    def __init__(self, config_path: Path, output_root: Path, preview: bool = True, ros_enabled: bool = False):
        self.config = load_config(config_path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(output_root).expanduser().resolve() / f"field_test_{stamp}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events = self.run_dir / "events.jsonl"
        self.log_path = self.run_dir / "field_test.log"
        self.staged_viewpoints = self.run_dir / "teach" / "viewpoints.json"
        self.teach_session = TeachingSession(self.staged_viewpoints)
        camera = self.config["camera"]
        self.preview = CameraPreview(str(camera["server_url"]), float(camera.get("timeout_s", 1.5)), preview, self.run_dir / "teach")
        self.route = RouteRunner(self.config, self.log)
        self.ros: RosFieldPublisher | None = None
        if ros_enabled:
            try:
                self.ros = RosFieldPublisher(self.config, self.route, self.log)
                self.ros.start()
            except Exception as exc:
                self.log(f"field_test_rviz_disabled error={exc}")
        self._route_thread: threading.Thread | None = None
        self._route_result: dict[str, Any] | None = None
        self._arm_tester: ArmPointTester | None = None
        self._arm_thread: threading.Thread | None = None
        self._arm_result: dict[str, Any] | None = None
        self._arm_commands: queue.Queue[str] = queue.Queue()

    def log(self, message: str):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{datetime.now(timezone.utc).isoformat()} {message}\n")

    def event(self, event_type: str, **values: Any):
        with self.events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "event": event_type, **values}, ensure_ascii=False) + "\n")

    def teach(self, input_stream: TextIO, output_stream: TextIO) -> int:
        self.preview.start()
        client = TeachingClient(str(self.config["jaka"]["ip"]), int(self.config["jaka"].get("port", 10001)))
        saved = 0
        output_stream.write("示教只读取 JAKA，不会上电、使能或发送运动命令。相机预览已启动。\n")
        for name in VIEWPOINT_NAMES:
            while True:
                output_stream.write(f"请人工移动到 {name}，确认安全后按回车保存；输入 q 结束：")
                output_stream.flush()
                command = input_stream.readline()
                if command == "":
                    output_stream.write("输入流已结束（不是普通回车），示教提前结束；请从可交互终端启动后重试。\n")
                    output_stream.flush()
                    return saved
                if command.strip().lower() == "q":
                    output_stream.write(f"已停止示教，已保存 {saved}/{len(VIEWPOINT_NAMES)} 个点。\n")
                    output_stream.flush()
                    return saved
                if command.strip():
                    output_stream.write("只接受回车或 q。\n")
                    continue
                try:
                    joint, tcp = client.read_snapshot()
                except Exception as exc:
                    output_stream.write(f"{name} 保存失败：{exc}，请保持当前位置后再次按回车重试。\n")
                    self.event("teach_failed", name=name, error=str(exc))
                    continue
                self.teach_session.save(name, joint, tcp)
                self.preview.snapshot(self.run_dir / "teach" / f"{name}.jpg")
                self.event("teach_saved", name=name)
                saved += 1
                output_stream.write(f"已保存 {name} ({saved}/{len(VIEWPOINT_NAMES)})\n")
                break
        output_stream.write(f"八个示教点已完成 ({saved}/{len(VIEWPOINT_NAMES)})。\n")
        return saved

    def start_route_test(self) -> bool:
        if self._route_thread and self._route_thread.is_alive():
            return False
        self.route.stop_event.clear()
        self._route_result = None

        def run():
            result = self.route.run_one_loop()
            self._route_result = result
            self.event("route_test_finished", **result)

        self._route_thread = threading.Thread(target=run, name="field-route-test", daemon=True)
        self._route_thread.start()
        self.event("route_test_started")
        return True

    def stop_all(self):
        self.route.stop()
        if self._arm_tester is not None:
            self._arm_tester.client.stop()

    def start_arm_test(self, output_stream: TextIO) -> bool:
        if self._arm_thread and self._arm_thread.is_alive():
            return False
        self._arm_commands = queue.Queue()
        self._arm_result = None
        self._arm_tester = ArmPointTester(self.config, self.log)

        def run():
            result = self._arm_tester.run(self.staged_viewpoints, QueueInput(self._arm_commands), output_stream)
            self._arm_result = result
            self.event("arm_test_finished", **result)

        self._arm_thread = threading.Thread(target=run, name="field-arm-test", daemon=True)
        self._arm_thread.start()
        self.event("arm_test_started")
        return True

    def run_console(self, input_stream: TextIO, output_stream: TextIO):
        output_stream.write("Wenshi 现场示教与硬件测试\n")
        output_stream.write("RViz地图/相机由启动脚本管理；命令: camera [stop] | teach | test route | test arm | status | stop | q\n")
        self.preview.start()
        while True:
            output_stream.write("field> ")
            output_stream.flush()
            command = input_stream.readline()
            if command == "":
                break
            parts = command.strip().lower().split()
            if not parts:
                if self._arm_thread and self._arm_thread.is_alive():
                    self._arm_commands.put("\n")
                continue
            if self._arm_thread and self._arm_thread.is_alive():
                if parts == ["status"]:
                    output_stream.write(json.dumps({"arm_test_active": True, "arm_test_result": self._arm_result}, ensure_ascii=False) + "\n")
                    continue
                if parts == ["stop"]:
                    self.stop_all()
                    self._arm_commands.put("stop\n")
                    output_stream.write("已发送 AGV/JAKA 停止。\n")
                    continue
                if parts in (["q"], ["quit"], ["exit"]):
                    self.stop_all()
                    self._arm_commands.put("q\n")
                    break
                self._arm_commands.put(command)
                continue
            if parts == ["camera"]:
                self.preview.start()
                output_stream.write("相机预览已启动。\n")
            elif parts == ["camera", "stop"]:
                self.preview.stop()
                output_stream.write("相机预览已停止。\n")
            elif parts == ["teach"]:
                output_stream.write(f"本次示教目录: {self.run_dir / 'teach'}\n")
                self.teach(input_stream, output_stream)
            elif parts == ["test", "route"]:
                if self.start_route_test():
                    output_stream.write("地图单圈测试已启动；可随时输入 stop。\n")
                else:
                    output_stream.write("地图测试已在运行。\n")
            elif parts == ["test", "arm"]:
                if self.start_arm_test(output_stream):
                    output_stream.write("机械臂逐点测试已启动；每个点按回车确认，运动中可输入 stop。\n")
                else:
                    output_stream.write("机械臂测试已在运行。\n")
            elif parts == ["status"]:
                output_stream.write(json.dumps({
                    "agv": self.route.status.get_status(),
                    "route_test_active": bool(self._route_thread and self._route_thread.is_alive()),
                    "route_test_result": self._route_result,
                    "jaka": "owned by arm test when active",
                    "camera_error": self.preview.last_error,
                }, ensure_ascii=False) + "\n")
            elif parts == ["stop"]:
                self.stop_all()
                output_stream.write("已发送 AGV/JAKA 停止。\n")
            elif parts in (["q"], ["quit"], ["exit"]):
                break
            else:
                output_stream.write("命令: camera [stop] | teach | test route | test arm | status | stop | q\n")
        self.stop_all()
        if self._route_thread and self._route_thread.is_alive():
            self._route_thread.join(timeout=2.0)
        if self._arm_thread and self._arm_thread.is_alive():
            self._arm_commands.put("q\n")
            self._arm_thread.join(timeout=2.0)
        self.preview.stop()
        if self.ros is not None:
            self.ros.stop()
        self.route.status.disconnect()
        self.route.motion.disconnect()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Wenshi 单入口现场示教与硬件测试")
    parser.add_argument("--config", type=Path, default=Path("config/wenshi.yaml"))
    parser.add_argument("--output", type=Path, default=Path("runtime/field_tests"))
    parser.add_argument("--no-preview", action="store_true", help="不打开 OpenCV 预览窗口")
    parser.add_argument("--ros", action="store_true", help="发布地图、位姿和状态供 RViz 显示")
    args = parser.parse_args(argv)
    input_stream, owned_input = resolve_console_input(__import__("sys").stdin)
    try:
        FieldTestSession(
            args.config,
            args.output,
            preview=not args.no_preview,
            ros_enabled=args.ros,
        ).run_console(input_stream, __import__("sys").stdout)
    finally:
        if owned_input is not None:
            owned_input.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
