"""Expert demonstration loop for Wenshi greenhouse hardware.

This module is intentionally not a monitoring or phenotyping path.  It sends
only the existing safe route/arm commands, publishes display-only ROS topics,
and saves a JPEG only when the operator explicitly enters ``photo``.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable
import urllib.request

import cv2
import numpy as np

from .agv import AGVMotionClient, AGVStatusClient
from .config import load_config, load_viewpoints, require_joint_pose, resolve_config_path
from .control.route_math import compute_segment_velocity, endpoint_reached, make_segments, segment_progress
from .control.route_math import Segment
from .control.route_policy import validate_route
from .jaka import JakaClient
from .map_utils import load_station_poses


OBSERVATION_GROUPS = tuple(
    [f"left-{index:02d}" for index in range(1, 9)]
    + [f"right-{index:02d}" for index in range(1, 9)]
)
MAP_ROUTE_ORDER = ("LM1", "LM4", "LM3", "LM2")


def _finite_pose(value: Any, label: str) -> tuple[float, float, float]:
    target = value.get("pose", value) if isinstance(value, dict) else value
    if not isinstance(target, dict):
        raise ValueError(f"{label} 缺少 pose")
    try:
        pose = tuple(float(target[name]) for name in ("x", "y", "angle"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须包含 x/y/angle") from exc
    if not all(math.isfinite(item) for item in pose):
        raise ValueError(f"{label} 包含非有限坐标")
    return pose


def load_demo_setup(path: str | Path) -> dict[str, Any]:
    """Load a published setup containing all 16 real plant parking points."""
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取现场 setup {source}: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("stations"), dict):
        raise ValueError(f"现场 setup 必须包含 16 个水稻观测停车点: {source}")
    stations = value["stations"]
    if set(stations) != set(OBSERVATION_GROUPS):
        missing = sorted(set(OBSERVATION_GROUPS) - set(stations))
        extra = sorted(set(stations) - set(OBSERVATION_GROUPS))
        detail = f"缺少 {', '.join(missing)}" if missing else f"多出 {', '.join(extra)}"
        raise ValueError(f"现场 setup 必须恰好包含 16 个水稻观测停车点（{detail}）")
    normalized_stations = {
        group_id: _finite_pose(stations[group_id], f"停车点 {group_id}")
        for group_id in OBSERVATION_GROUPS
    }
    viewpoints = value.get("viewpoints")
    if not isinstance(viewpoints, dict):
        raise ValueError("现场 setup 缺少 viewpoints；不能使用旧 LM 示教文件")
    aliases = {"left": "camera_left", "center": "camera", "right": "camera_right"}
    normalized_viewpoints = dict(viewpoints)
    for alias, name in aliases.items():
        if name not in normalized_viewpoints and alias in viewpoints:
            normalized_viewpoints[name] = viewpoints[alias]
    for name in ("home_safe", "camera_left", "camera", "camera_right"):
        _require_finite_joint_pose(normalized_viewpoints, name)
    return {"source": str(source), "stations": normalized_stations, "viewpoints": normalized_viewpoints, "raw": value}


def choose_demo_groups(
    groups: Any,
    min_count: int = 3,
    max_count: int = 5,
    rng: Any | None = None,
) -> list[str]:
    """Choose 3-5 unique real observation groups for one demonstration lap."""
    candidates = [str(group) for group in groups]
    if len(candidates) != len(set(candidates)) or not set(candidates).issubset(set(OBSERVATION_GROUPS)):
        raise ValueError("演示随机点只能来自 16 个真实水稻观测组，且不能包含 LM 角点")
    lower, upper = int(min_count), int(max_count)
    if lower < 1 or upper < lower or upper > len(candidates):
        raise ValueError("演示随机点数量范围无效")
    chooser = rng if rng is not None else __import__("random").SystemRandom()
    count = chooser.randint(lower, upper)
    return list(chooser.sample(candidates, count))


def _project_to_segment(point: tuple[float, float, float], segment: Segment) -> tuple[float, float]:
    sx, sy, _ = segment.start
    ex, ey, _ = segment.end
    dx, dy = ex - sx, ey - sy
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        raise ValueError(f"路线段长度过短: {segment.start_name}->{segment.end_name}")
    along_ratio = max(0.0, min(1.0, ((point[0] - sx) * dx + (point[1] - sy) * dy) / length_sq))
    px, py = sx + along_ratio * dx, sy + along_ratio * dy
    return math.hypot(point[0] - px, point[1] - py), along_ratio


def build_demo_lap_segments(
    map_stations: dict[str, tuple[float, float, float]],
    order: list[str] | tuple[str, ...],
    observation_stations: dict[str, tuple[float, float, float]],
    selected_groups: list[str] | tuple[str, ...],
    max_distance_m: float = 0.25,
) -> list[Segment]:
    """Insert selected plant stops into the verified LM closed-loop geometry."""
    base = make_segments(map_stations, list(order), loop=True)
    selected = list(selected_groups)
    if len(selected) != len(set(selected)) or not set(selected).issubset(observation_stations):
        raise ValueError("演示选择了不存在或重复的水稻观测组")
    by_segment: dict[int, list[tuple[float, str, tuple[float, float, float]]]] = {index: [] for index in range(len(base))}
    for group_id in selected:
        pose = observation_stations[group_id]
        candidates = [(_project_to_segment(pose, segment) + (index,)) for index, segment in enumerate(base)]
        distance, ratio, index = min(candidates, key=lambda item: item[0])
        if distance > float(max_distance_m):
            raise ValueError(f"水稻停车点 {group_id} 偏离验证路线 {distance:.3f}m，拒绝猜测路线")
        by_segment[index].append((ratio, group_id, pose))
    result: list[Segment] = []
    for index, base_segment in enumerate(base):
        previous_name = base_segment.start_name
        previous_pose = base_segment.start
        for _ratio, group_id, pose in sorted(by_segment[index], key=lambda item: item[0]):
            result.append(Segment(previous_name, group_id, previous_pose, pose))
            previous_name, previous_pose = group_id, pose
        result.append(Segment(previous_name, base_segment.end_name, previous_pose, base_segment.end))
    return result


class DemoCamera:
    def __init__(self, url: str, timeout_s: float = 1.5):
        self.url = str(url).rstrip("/")
        self.timeout_s = max(float(timeout_s), 0.1)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _json(self, suffix: str) -> dict[str, Any]:
        with self.opener.open(f"{self.url}/{suffix.lstrip('/')}", timeout=self.timeout_s) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("D435 response is not an object")
        return value

    def health(self) -> dict[str, Any]:
        return self._json("health")

    @staticmethod
    def _decode(value: str) -> np.ndarray:
        raw = base64.b64decode(str(value).encode("ascii"), validate=True)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("D435 color decode failed")
        return image

    def color(self) -> np.ndarray:
        packet = self._json("frame")
        if not packet.get("ok"):
            raise RuntimeError(str(packet.get("error", "D435 frame unavailable")))
        return self._decode(packet.get("color_jpeg_b64", ""))


def _load_demo_viewpoints(path: str | Path) -> dict[str, Any]:
    """Load either a direct viewpoints file or a published height-test setup."""
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取演示示教文件 {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"演示示教文件不是 JSON 对象: {source}")
    viewpoints = value.get("viewpoints", value)
    if not isinstance(viewpoints, dict):
        raise ValueError(f"演示示教文件缺少 viewpoints: {source}")
    aliases = {"left": "camera_left", "center": "camera", "right": "camera_right"}
    normalized = dict(viewpoints)
    for alias, name in aliases.items():
        if name not in normalized and alias in viewpoints:
            normalized[name] = viewpoints[alias]
    return normalized


def _require_finite_joint_pose(viewpoints: dict[str, Any], name: str) -> list[float]:
    pose = require_joint_pose(viewpoints, name)
    if not all(math.isfinite(value) for value in pose):
        raise ValueError(f"示教点 {name} 包含非有限关节角")
    return pose


class DemoArm:
    def __init__(
        self,
        config: dict[str, Any],
        log: Callable[[str], None] = print,
        viewpoints_file: str | Path | None = None,
    ):
        arm = config["jaka"]
        self.log = log
        self.client = JakaClient(
            str(arm["ip"]),
            int(arm.get("port", 10001)),
            joint_tolerance_deg=float(arm.get("joint_tolerance_deg", 0.5)),
            command_interval_s=float(arm.get("command_interval_s", 0.1)),
            motion_start_wait_s=float(arm.get("motion_start_wait_s", 0.5)),
            motion_stall_timeout_s=float(arm.get("motion_stall_timeout_s", 10.0)),
            log=log,
        )
        viewpoints = (
            _load_demo_viewpoints(viewpoints_file)
            if viewpoints_file is not None
            else load_viewpoints(config)
        )
        self.poses = {
            "left": _require_finite_joint_pose(viewpoints, str(arm.get("left_pose", "camera_left"))),
            "center": _require_finite_joint_pose(viewpoints, str(arm.get("center_pose", "camera"))),
            "right": _require_finite_joint_pose(viewpoints, str(arm.get("right_pose", "camera_right"))),
        }
        try:
            self.safe = _require_finite_joint_pose(viewpoints, "home_safe")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "专家演示拒绝启动：缺少经过现场确认的 home_safe；"
                "请先完成现场 setup，或通过 --viewpoints 指定已验证示教文件"
            ) from exc
        self._motion_lock = threading.Lock()
        self.cancel_requested: Callable[[], bool] | None = None
        self.speed = min(float(arm.get("fixed_transition_speed_deg_s", 60.0)), 30.0)
        self.accel = min(float(arm.get("accel_deg_s2", 80.0)), 50.0)
        self.timeout = float(arm.get("motion_timeout_s", 120.0))

    def connect(self) -> None:
        if not self.client.connect(timeout=3.0) or not self.client.wait_for_joint_state(timeout=3.0):
            raise RuntimeError(self.client.last_error or "JAKA connection failed")

    def move_to_view(self, view: str) -> bool:
        if view not in self.poses:
            raise ValueError(f"unknown demo view: {view}")
        with self._motion_lock:
            return bool(self.client.joint_move(self.poses[view], self.speed, self.accel, self.timeout))

    def observe(self) -> bool:
        for view in ("left", "center", "right", "center"):
            if self.cancel_requested is not None and self.cancel_requested():
                return False
            if not self.move_to_view(view):
                return False
            if self.cancel_requested is not None and self.cancel_requested():
                return False
        return True

    def move_to_safe(self) -> bool:
        with self._motion_lock:
            return bool(self.client.joint_move(self.safe, self.speed, self.accel, self.timeout))

    def stop(self) -> None:
        self.client.stop()

    def close(self) -> None:
        self.client.disconnect()


class DemoRosPublisher:
    """Publish only map/pose/state display topics; no data recorder."""

    def __init__(self, config: dict[str, Any], status: Any, log: Callable[[str], None] = print, observation_stations: dict[str, tuple[float, float, float]] | None = None):
        import rclpy
        from geometry_msgs.msg import PoseStamped
        from nav_msgs.msg import OccupancyGrid
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String
        from visualization_msgs.msg import Marker, MarkerArray

        self._rclpy = rclpy
        self._PoseStamped = PoseStamped
        self._String = String
        self._status = status
        self._log = log
        self._stopped = False
        initialized = False
        try:
            rclpy.init(args=[])
            initialized = True
            self._node = rclpy.create_node("wenshi_expert_demo_visualizer")
            topics = config["topics"]
            transient = QoSProfile(depth=1)
            transient.durability = DurabilityPolicy.TRANSIENT_LOCAL
            transient.reliability = ReliabilityPolicy.RELIABLE
            self._map_pub = self._node.create_publisher(OccupancyGrid, topics["map"], transient)
            self._marker_pub = self._node.create_publisher(MarkerArray, topics["markers"], transient)
            self._pose_pub = self._node.create_publisher(PoseStamped, topics["agv_pose"], 10)
            self._state_pub = self._node.create_publisher(String, topics["state"], transient)
            map_path = resolve_config_path(config, str(config["map"]["smap_file"]))
            from .map_utils import make_occupancy_grid, make_station_markers
            stamp = self._node.get_clock().now().to_msg()
            self._map_message = make_occupancy_grid(map_path, stamp)
            self._marker_message = make_station_markers(map_path, stamp)
            self._append_observation_markers(self._marker_message, observation_stations or {}, stamp)
            self._node.create_timer(0.2, self._publish)
            self._thread = threading.Thread(target=self._spin, name="wenshi-demo-rviz", daemon=True)
            self._thread.start()
            self._log("专家演示 RViz 已启动：地图、AGV位姿、路线标记和D435图像")
        except Exception:
            if initialized and rclpy.ok():
                rclpy.shutdown()
            raise

    @staticmethod
    def _append_observation_markers(marker_message: Any, stations: dict[str, tuple[float, float, float]], stamp: Any) -> None:
        """Add real plant parking groups so RViz distinguishes them from LM corners."""
        from visualization_msgs.msg import Marker

        for index, (name, (x, y, _angle)) in enumerate(stations.items(), start=10000):
            marker = Marker()
            marker.header.frame_id = "map"
            marker.header.stamp = stamp
            marker.ns = "observation_stations"
            marker.id = index
            marker.type = Marker.SPHERE
            marker.action = Marker.ADD
            marker.pose.position.x = float(x)
            marker.pose.position.y = float(y)
            marker.pose.position.z = 0.12
            marker.pose.orientation.w = 1.0
            marker.scale.x = marker.scale.y = marker.scale.z = 0.12
            marker.color.r = 1.0
            marker.color.g = 0.75
            marker.color.b = 0.05
            marker.color.a = 0.9
            marker_message.markers.append(marker)

            label = Marker()
            label.header = marker.header
            label.ns = "observation_station_labels"
            label.id = index + 100
            label.type = Marker.TEXT_VIEW_FACING
            label.action = Marker.ADD
            label.pose.position.x = float(x)
            label.pose.position.y = float(y)
            label.pose.position.z = 0.32
            label.pose.orientation.w = 1.0
            label.scale.z = 0.16
            label.color.r = label.color.g = label.color.b = label.color.a = 1.0
            label.text = str(name)
            marker_message.markers.append(label)

    def _spin(self) -> None:
        try:
            self._rclpy.spin(self._node)
        except Exception as exc:
            if not self._stopped:
                self._log(f"demo_rviz_error {exc}")

    def _publish(self) -> None:
        stamp = self._node.get_clock().now().to_msg()
        self._map_message.header.stamp = stamp
        self._marker_pub.publish(self._marker_message)
        self._map_pub.publish(self._map_message)
        current = self._status.get_status()
        if current.get("x") is not None and current.get("y") is not None:
            pose = self._PoseStamped()
            pose.header.stamp = stamp
            pose.header.frame_id = "map"
            pose.pose.position.x = float(current["x"])
            pose.pose.position.y = float(current["y"])
            yaw = float(current.get("angle") or 0.0)
            pose.pose.orientation.z = math.sin(yaw * 0.5)
            pose.pose.orientation.w = math.cos(yaw * 0.5)
            self._pose_pub.publish(pose)
        state = self._String()
        state.data = "WENSHI_DEMO: display only; no monitoring or phenotyping"
        self._state_pub.publish(state)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()
        thread = getattr(self, "_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=2.0)


class DemoController:
    def __init__(
        self,
        config: dict[str, Any],
        photo_dir: Path,
        *,
        viewpoints_file: str | Path | None = None,
        camera_enabled: bool = True,
        status: Any | None = None,
        motion: Any | None = None,
        arm: Any | None = None,
        camera: Any | None = None,
        ros: Any | None = None,
        log: Callable[[str], None] = print,
    ):
        self.config = config
        self.log = log
        self._stop_event = threading.Event()
        self.status = status or AGVStatusClient(str(config["agv"]["ip"]), int(config["agv"].get("status_port", 19204)), interval_ms=int(config["agv"].get("status_interval_ms", 200)), response_timeout_s=float(config["agv"].get("status_response_timeout_s", 0.8)), log=log)
        self.motion = motion or AGVMotionClient(str(config["agv"]["ip"]), int(config["agv"].get("motion_port", 19205)), send_rate_hz=float(config["control"].get("rate_hz", 20.0)), watchdog_s=float(config["safety"].get("command_watchdog_s", 0.3)), log=log)
        if viewpoints_file is None:
            raise ValueError("专家演示必须指定包含 16 个水稻观测停车点的 field_height_setup.json")
        self.demo_setup = load_demo_setup(viewpoints_file)
        self.arm = arm or DemoArm(config, log, viewpoints_file=viewpoints_file)
        if hasattr(self.arm, "cancel_requested"):
            self.arm.cancel_requested = self._stop_event.is_set
        self.camera = camera or DemoCamera(str(config["camera"]["server_url"]), float(config["camera"].get("timeout_s", 1.5)))
        self.camera_enabled = bool(camera_enabled)
        self.ros = ros
        map_path = resolve_config_path(config, str(config["map"]["smap_file"]))
        self.map_stations = load_station_poses(map_path)
        self.order = list(validate_route(config.get("route", {}).get("station_order", MAP_ROUTE_ORDER)))
        self.stations = self.map_stations  # compatibility for read-only route helpers
        self.observation_stations = self.demo_setup["stations"]
        self.observation_order = list(OBSERVATION_GROUPS)
        self.observation_route_max_offset_m = float(config.get("field_test", {}).get("observation_route_max_offset_m", config.get("safety", {}).get("hard_cross_track_m", 0.25)))
        # Validate every recorded plant stop at startup.  A bad setup must fail
        # before any AGV/JAKA connection or command is opened.
        build_demo_lap_segments(self.map_stations, self.order, self.observation_stations, self.observation_order, self.observation_route_max_offset_m)
        self.segments = make_segments(self.map_stations, self.order, loop=True)
        self.photo_dir = Path(photo_dir).expanduser().resolve()
        self._photo_index = 0
        self._stopped = False
        self._running = False
        self._route_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.current_station = ""
        self.station_snap_m = float(config.get("field_test", {}).get("station_snap_m", 0.25))

    def _stop_requested(self) -> bool:
        event = getattr(self, "_stop_event", None)
        return bool(getattr(self, "_stopped", False) or (event is not None and event.is_set()))

    def connect(self) -> None:
        if not self.status.connect() or not self.status.wait_for_status(timeout=3.0, max_age=1.0):
            raise RuntimeError("AGV status connection or fresh status failed")
        if not self.motion.connect():
            raise RuntimeError(self.motion.last_error or "AGV motion connection failed")
        self.arm.connect()
        if self.camera_enabled:
            health = self.camera.health()
            if not health.get("ok"):
                raise RuntimeError(str(health.get("error", "D435 health check failed")))
            deadline = time.monotonic() + max(float(self.config.get("camera", {}).get("startup_wait_s", 3.0)), 0.0)
            last_error: Exception | None = None
            while True:
                try:
                    frame = self.camera.color()
                    if not isinstance(frame, np.ndarray) or frame.size == 0:
                        raise RuntimeError("D435 RGB frame is empty")
                    break
                except Exception as exc:
                    last_error = exc
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"D435 首帧不可用: {last_error}") from exc
                    time.sleep(0.2)
        else:
            self.log("D435 健康检查已跳过；photo 命令不可用")

    def start(self) -> None:
        with self._lock:
            if self._stopped or self._stop_event.is_set():
                raise RuntimeError("演示已停止，不能重新 start")
            if self._route_thread and self._route_thread.is_alive():
                self._running = True
                return
        if not self.arm.move_to_safe():
            raise RuntimeError("路线启动前 JAKA 无法回到 home_safe")
        with self._lock:
            if self._stopped or self._stop_event.is_set():
                raise RuntimeError("演示在启动准备期间已停止")
            self._running = True
            self._route_thread = threading.Thread(target=self._route_loop, name="wenshi-demo-route", daemon=True)
            self._route_thread.start()

    def pause(self) -> None:
        with self._lock:
            self._running = False
        self.motion.stop()
        self.log("演示路线已暂停，AGV保持停止")

    def _fresh_status(self) -> dict[str, Any]:
        if not self.status.wait_for_status(timeout=0.8, max_age=0.8):
            raise RuntimeError("AGV定位状态过期")
        value = self.status.get_status()
        if value.get("emergency"):
            raise RuntimeError("AGV急停状态")
        if value.get("blocked"):
            self.motion.stop()
            raise RuntimeError("AGV阻挡，已停止等待人工处理")
        if value.get("x") is None or value.get("y") is None or value.get("angle") is None:
            raise RuntimeError("AGV没有有效位姿")
        return value

    def _run_segment(self, segment: Any) -> bool:
        control = self.config["control"]
        safety = self.config["safety"]
        speed = min(abs(float(self.config.get("field_test", {}).get("route_speed_mps", 0.10))), 0.10)
        while not self._stop_requested():
            with self._lock:
                active = self._running
            if not active:
                self.motion.stop()
                time.sleep(0.1)
                continue
            status = self._fresh_status()
            if endpoint_reached(status, segment, float(control.get("endpoint_tolerance_m", 0.10))):
                self.motion.stop()
                self.current_station = segment.end_name
                return True
            velocity, angular, progress = compute_segment_velocity(
                status, segment, speed,
                float(control.get("cross_track_gain", 0.8)),
                float(control.get("heading_gain", 1.6)),
                float(control.get("max_angular_speed_rad_s", 0.35)),
                float(control.get("correction_threshold_m", 0.04)),
                math.radians(float(control.get("rotate_in_place_threshold_deg", 30.0))),
                math.radians(float(control.get("heading_slowdown_threshold_deg", 10.0))),
                float(control.get("min_heading_scale", 0.20)),
            )
            if abs(progress.cross_track) > float(safety.get("hard_cross_track_m", 0.25)):
                self.motion.stop()
                raise RuntimeError(f"路线横向偏差过大: {progress.cross_track:.3f}m")
            self.motion.set_velocity(velocity, angular)
            time.sleep(0.05)
        self.motion.stop()
        return False

    def _route_attachment_index(self, status: dict[str, Any]) -> int:
        """Attach to the nearest forward route segment before starting motion."""
        nearest_station: tuple[float, str] | None = None
        x = float(status["x"])
        y = float(status["y"])
        for name in self.order:
            sx, sy, _ = self.stations[name]
            distance = math.hypot(x - sx, y - sy)
            if nearest_station is None or distance < nearest_station[0]:
                nearest_station = (distance, name)
        if nearest_station is not None and nearest_station[0] <= self.station_snap_m:
            for index, segment in enumerate(self.segments):
                if segment.start_name == nearest_station[1]:
                    self.log(f"演示路线接入站点 {nearest_station[1]}: {segment.start_name}->{segment.end_name}")
                    return index

        best: tuple[float, int, float] | None = None
        for index, segment in enumerate(self.segments):
            progress = segment_progress(status, segment, cross_track_gain=0.0)
            along = max(0.0, min(progress.length, progress.along_track))
            projection_x = segment.start[0] + along * math.cos(progress.segment_yaw)
            projection_y = segment.start[1] + along * math.sin(progress.segment_yaw)
            distance = math.hypot(x - projection_x, y - projection_y)
            candidate = (distance, index, progress.cross_track)
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is None:
            raise RuntimeError("地图没有可用演示路线")
        if best[0] > float(self.config["safety"].get("hard_cross_track_m", 0.25)):
            raise RuntimeError(f"当前位置离演示路线过远: {best[0]:.3f}m")
        segment = self.segments[best[1]]
        self.log(f"演示路线接入最近路段 {segment.start_name}->{segment.end_name}")
        return best[1]

    def _route_attachment_index_for(self, status: dict[str, Any], segments: list[Segment]) -> int:
        """Attach to a lap's forward segment without jumping across the greenhouse."""
        best: tuple[float, int] | None = None
        for index, segment in enumerate(segments):
            progress = segment_progress(status, segment, cross_track_gain=0.0)
            along = max(0.0, min(progress.length, progress.along_track))
            projection_x = segment.start[0] + along * math.cos(progress.segment_yaw)
            projection_y = segment.start[1] + along * math.sin(progress.segment_yaw)
            distance = math.hypot(float(status["x"]) - projection_x, float(status["y"]) - projection_y)
            candidate = (distance, index)
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is None:
            raise RuntimeError("本圈没有可用演示路线")
        if best[0] > float(self.config["safety"].get("hard_cross_track_m", 0.25)):
            raise RuntimeError(f"当前位置离本圈路线过远: {best[0]:.3f}m")
        return best[1]

    def _observe_station(self) -> None:
        first_error: RuntimeError | None = None
        try:
            try:
                self._stop_agv_and_wait()
                if not self.arm.move_to_safe():
                    first_error = RuntimeError("JAKA无法回到安全姿态")
                elif not self.arm.observe():
                    first_error = RuntimeError("JAKA展示观察动作失败")
            except RuntimeError as exc:
                first_error = exc
        finally:
            if not self.arm.move_to_safe():
                if first_error is None:
                    first_error = RuntimeError("JAKA观察后无法回到安全姿态")
                self.log("警告：JAKA观察后回安全姿态失败，请实体急停并人工处理")
        if first_error is not None:
            raise first_error
        self.log(f"演示观察完成 station={self.current_station}")

    def _stop_agv_and_wait(self) -> None:
        self.motion.stop()
        wait_for_status = getattr(self.status, "wait_for_status", None)
        if not callable(wait_for_status):
            return
        timeout = max(float(self.config.get("safety", {}).get("station_stop_timeout_s", 2.0)), 0.2)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stop_requested():
            if not wait_for_status(timeout=min(0.25, max(0.0, deadline - time.monotonic())), max_age=0.8):
                continue
            current = self.status.get_status()
            if current.get("emergency"):
                raise RuntimeError("AGV到站后进入急停")
            if current.get("blocked"):
                raise RuntimeError("AGV到站后仍处于阻挡状态")
            if bool(current.get("is_stop")):
                return
            time.sleep(0.05)
        if self._stop_requested():
            raise RuntimeError("演示已停止")
        raise RuntimeError("AGV停止命令未获得 is_stop 确认")

    def _route_loop(self) -> None:
        try:
            while not self._stop_requested():
                minimum = int(self.config.get("field_test", {}).get("demo_min_observations", 3))
                maximum = int(self.config.get("field_test", {}).get("demo_max_observations", 5))
                selected = choose_demo_groups(self.observation_order, minimum, maximum)
                self.log(f"本圈随机观察 {len(selected)} 个水稻停车组: {', '.join(selected)}")
                lap_segments = build_demo_lap_segments(
                    self.map_stations,
                    self.order,
                    self.observation_stations,
                    selected,
                    self.observation_route_max_offset_m,
                )
                index = self._route_attachment_index_for(self._fresh_status(), lap_segments)
                for _ in range(len(lap_segments)):
                    if self._stop_requested():
                        return
                    if not self._run_segment(lap_segments[index]):
                        return
                    endpoint = lap_segments[index].end_name
                    if endpoint in selected:
                        self.current_station = endpoint
                        self._observe_station()
                    index = (index + 1) % len(lap_segments)
        except Exception as exc:
            self.log(f"演示自动停止: {exc}")
            self.stop(f"route failure: {exc}")

    def save_photo(self) -> Path:
        if not getattr(self, "camera_enabled", True):
            raise RuntimeError("D435相机已通过 --no-camera 禁用，不能执行 photo")
        image = self.camera() if callable(self.camera) else self.camera.color()
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise RuntimeError("D435没有可保存的彩色画面")
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self._photo_index += 1
        output = self.photo_dir / f"demo_{time.strftime('%Y%m%d_%H%M%S')}_{self._photo_index:03d}.jpg"
        if not cv2.imwrite(str(output), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"演示照片写入失败: {output}")
        self.log(f"演示照片已保存: {output}")
        return output

    def status_text(self) -> str:
        value = self.status.get_status()
        arm = self.arm.client.snapshot() if hasattr(self.arm, "client") else {}
        return f"station={self.current_station or '-'} agv_stop={value.get('is_stop')} blocked={value.get('blocked')} emergency={value.get('emergency')} status_age={value.get('status_age')} arm_connected={arm.get('connected', '?')} running={self._running}"

    def stop(self, reason: str = "operator") -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._running = False
            stop_event = getattr(self, "_stop_event", None)
            if stop_event is not None:
                stop_event.set()
        try:
            self.motion.stop()
        finally:
            try:
                self.arm.stop()
            finally:
                try:
                    if not self.arm.move_to_safe():
                        self.log("警告：停止后 JAKA 未确认回到 home_safe，请实体急停并人工处理")
                except Exception as exc:
                    self.log(f"警告：停止后 JAKA 回 home_safe 失败: {exc}；请实体急停并人工处理")
        self.log(f"演示已停止 reason={reason}")

    def close(self) -> None:
        self.stop("close")
        thread = self._route_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        if self.ros is not None:
            try:
                self.ros.stop()
            except Exception as exc:
                self.log(f"RViz 发布器停止异常: {exc}")
        for target in (self.arm, self.motion, self.status):
            close = getattr(target, "close", None) or getattr(target, "disconnect", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    self.log(f"设备连接关闭异常: {exc}")


def console(session: DemoController, input_stream=None, output_stream=None) -> int:
    import sys
    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    session.start()
    output_stream.write("WENSHI专家展示已开始（不做监测、不运行YOLO）。命令: pause | start | photo | status | stop | q\n")
    output_stream.flush()
    for line in input_stream:
        command = line.strip().lower()
        if command in {"q", "quit", "stop"}:
            session.stop("operator input")
            return 0
        if command == "pause":
            session.pause()
        elif command == "start":
            session.start()
        elif command == "photo":
            try:
                session.save_photo()
            except Exception as exc:
                output_stream.write(f"照片失败: {exc}\n")
        elif command == "status":
            output_stream.write(session.status_text() + "\n")
        elif command:
            output_stream.write("命令: pause | start | photo | status | stop | q\n")
        output_stream.flush()
    session.stop("input closed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wenshi expert greenhouse demonstration")
    parser.add_argument("--config", default=str(Path(__file__).parents[2] / "config" / "wenshi.yaml"))
    parser.add_argument("--photos", default=str(Path(__file__).parents[2] / "runtime" / "demo"))
    parser.add_argument("--viewpoints", default=None, help="已验证示教文件或 field_height_setup.json")
    parser.add_argument("--no-rviz", action="store_true")
    parser.add_argument("--no-camera", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    viewpoints_file = args.viewpoints or os.environ.get("WENSHI_DEMO_VIEWPOINTS")
    if viewpoints_file is None:
        print("专家演示拒绝启动：缺少16个水稻观测停车点的 field_height_setup.json", file=__import__("sys").stderr)
        return 2
    if args.dry_run:
        DemoController(
            config,
            Path(args.photos),
            viewpoints_file=viewpoints_file,
            camera_enabled=not args.no_camera,
        )
        print("dry-run: local config/map/route/home_safe validated; no AGV/JAKA/D435 connection, no monitoring, no photos")
        return 0
    session: DemoController | None = None
    try:
        session = DemoController(
            config,
            Path(args.photos),
            viewpoints_file=viewpoints_file,
            camera_enabled=not args.no_camera,
        )
        session.connect()
        if not args.no_rviz:
            try:
                session.ros = DemoRosPublisher(config, session.status, observation_stations=session.observation_stations)
            except Exception as exc:
                raise RuntimeError(f"默认专家展示无法启动 ROS 展示发布器: {exc}") from exc
        return console(session)
    except KeyboardInterrupt:
        return 130
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
