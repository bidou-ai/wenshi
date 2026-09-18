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
from .control.route_math import (
    compute_segment_velocity,
    endpoint_approach_speed,
    endpoint_reached,
    make_segments,
    normalize_angle,
    segment_progress,
)
from .control.route_math import Segment
from .control.route_policy import validate_route
from .demo_setup import (
    A_STATIONS,
    B_LEFT_STATIONS,
    B_RIGHT_STATIONS,
    DEMO_PLANTS,
    OBSERVATION_GROUPS,
    RECORDED_STATIONS,
    VIEWPOINT_NAMES,
    DemoSetupSession,
    load_demo_setup,
)
from .jaka import JakaClient
from .map_utils import load_station_poses


MAP_ROUTE_ORDER = ("LM1", "LM4", "LM3", "LM2")


def _demo_route_rows(config: dict[str, Any]) -> tuple[float, float]:
    """Return the two horizontal route rows used as the B-R/B-L mirror."""
    map_config = config.get("map")
    if not isinstance(map_config, dict) or not str(map_config.get("smap_file", "")).strip():
        raise ValueError("Demo 停车点镜像缺少 map.smap_file 配置")
    validate_route(list(config.get("route", {}).get("station_order", MAP_ROUTE_ORDER)))
    map_path = resolve_config_path(config, str(map_config["smap_file"]))
    stations = load_station_poses(map_path)
    missing = [name for name in MAP_ROUTE_ORDER if name not in stations]
    if missing:
        raise ValueError("Demo 地图缺少镜像所需路线点: " + ", ".join(missing))
    top_values = (float(stations["LM1"][1]), float(stations["LM4"][1]))
    bottom_values = (float(stations["LM2"][1]), float(stations["LM3"][1]))
    if abs(top_values[0] - top_values[1]) > 0.05 or abs(bottom_values[0] - bottom_values[1]) > 0.05:
        raise ValueError("Demo 地图的 LM1-LM4 或 LM2-LM3 不是水平路线，不能自动镜像 B-L")
    top_y = sum(top_values) / 2.0
    bottom_y = sum(bottom_values) / 2.0
    if not math.isfinite(top_y) or not math.isfinite(bottom_y) or math.isclose(top_y, bottom_y):
        raise ValueError("Demo 地图的上下路线坐标无效，不能自动镜像 B-L")
    return top_y, bottom_y


def choose_demo_groups(
    groups: Any,
    min_count: int = 3,
    max_count: int = 5,
    rng: Any | None = None,
) -> list[str]:
    """Choose 3-5 unique real observation groups for one demonstration lap."""
    candidates = [str(group) for group in groups]
    if len(candidates) != len(set(candidates)) or not set(candidates).issubset(set(OBSERVATION_GROUPS)):
        raise ValueError("演示随机点只能来自 24 个水稻观测目标，且不能包含 LM 角点")
    lower, upper = int(min_count), int(max_count)
    if lower < 1 or upper < lower or upper > len(candidates):
        raise ValueError("演示随机点数量范围无效")
    chooser = rng if rng is not None else __import__("random").SystemRandom()
    count = chooser.randint(lower, upper)
    return list(chooser.sample(candidates, count))


def demo_view_for_station(station: str) -> str:
    """Select the single taught camera side for one plant target."""
    name = str(station)
    if name in B_LEFT_STATIONS:
        return "right"
    if name in A_STATIONS or name in B_RIGHT_STATIONS:
        return "left"
    raise ValueError(f"未知 Demo 水稻目标: {name}")


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
    """Load arm poses from the independent demonstration setup."""
    return load_demo_setup(path)["viewpoints"]


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
        setup_file: str | Path | None = None,
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
            _load_demo_viewpoints(setup_file)
            if setup_file is not None
            else load_viewpoints(config)
        )
        self.poses = {
            "left": _require_finite_joint_pose(viewpoints, str(arm.get("left_pose", "camera_left"))),
            "right": _require_finite_joint_pose(viewpoints, str(arm.get("right_pose", "camera_right"))),
        }
        try:
            self.safe = _require_finite_joint_pose(viewpoints, "home_safe")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "专家演示拒绝启动：缺少经过现场确认的 home_safe；"
                "请先运行 ./wenshi.sh --setup"
            ) from exc
        self._motion_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self.cancel_requested: Callable[[], bool] | None = None
        demo = config.get("demo", {})
        self.observe_speed = min(max(float(demo.get("arm_observe_speed_deg_s", 50.0)), 1.0), 60.0)
        self.retract_speed = min(max(float(demo.get("arm_retract_speed_deg_s", 60.0)), 1.0), 60.0)
        self.accel = min(max(float(demo.get("arm_accel_deg_s2", 80.0)), 1.0), 80.0)
        self.timeout = float(arm.get("motion_timeout_s", 120.0))
        self.observation_hold_s = max(float(config.get("demo", {}).get("observation_hold_s", 2.0)), 0.0)

    def connect(self) -> None:
        if not self.client.connect(timeout=3.0) or not self.client.wait_for_joint_state(timeout=3.0):
            raise RuntimeError(self.client.last_error or "JAKA connection failed")

    def move_to_view(self, view: str) -> bool:
        if view not in self.poses:
            raise ValueError(f"unknown demo view: {view}")
        with self._motion_lock:
            if self._view_cancelled():
                return False
            return bool(self.client.joint_move(self.poses[view], self.observe_speed, self.accel, self.timeout, cancel_requested=self._view_cancelled))

    def _view_cancelled(self) -> bool:
        return self._cancel_event.is_set() or (self.cancel_requested is not None and self.cancel_requested())

    def observe(self, view: str) -> bool:
        if self._view_cancelled() or not self.move_to_view(view):
            return False
        deadline = time.monotonic() + self.observation_hold_s
        while time.monotonic() < deadline:
            if self._view_cancelled():
                return False
            time.sleep(min(0.1, max(deadline - time.monotonic(), 0.0)))
        return True

    def move_to_safe(self) -> bool:
        with self._motion_lock:
            return bool(self.client.joint_move(self.safe, self.retract_speed, self.accel, self.timeout))

    def stop(self) -> None:
        self._cancel_event.set()
        self.client.stop()

    def resume(self) -> None:
        self._cancel_event.clear()

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
        setup_file: str | Path | None = None,
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
        self._pause_event = threading.Event()
        self.status = status or AGVStatusClient(str(config["agv"]["ip"]), int(config["agv"].get("status_port", 19204)), interval_ms=int(config["agv"].get("status_interval_ms", 200)), response_timeout_s=float(config["agv"].get("status_response_timeout_s", 0.8)), log=log)
        self.motion = motion or AGVMotionClient(str(config["agv"]["ip"]), int(config["agv"].get("motion_port", 19205)), send_rate_hz=float(config["control"].get("rate_hz", 20.0)), watchdog_s=float(config["safety"].get("command_watchdog_s", 0.3)), log=log)
        if setup_file is None:
            raise ValueError("专家演示缺少独立 demo_setup.json；请运行 ./wenshi.sh --setup")
        self.demo_setup = load_demo_setup(setup_file)
        self.arm = arm or DemoArm(config, log, setup_file=setup_file)
        if hasattr(self.arm, "cancel_requested"):
            self.arm.cancel_requested = lambda: self._stop_event.is_set() or self._pause_event.is_set()
        self.camera = camera or DemoCamera(str(config["camera"]["server_url"]), float(config["camera"].get("timeout_s", 1.5)))
        self.camera_enabled = bool(camera_enabled)
        self.ros = ros
        map_path = resolve_config_path(config, str(config["map"]["smap_file"]))
        self.map_stations = load_station_poses(map_path)
        self.order = list(validate_route(config.get("route", {}).get("station_order", MAP_ROUTE_ORDER)))
        self.stations = self.map_stations  # compatibility for read-only route helpers
        self.observation_stations = self.demo_setup["stations"]
        self.observation_order = list(OBSERVATION_GROUPS)
        self.observation_route_max_offset_m = float(config.get("demo", {}).get("observation_route_max_offset_m", 0.25))
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
        self.station_snap_m = float(config.get("demo", {}).get("station_snap_m", 0.25))

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
            route_thread = getattr(self, "_route_thread", None)
            resume = bool(route_thread and route_thread.is_alive())
        self._require_agv_ready_for_arm()
        if not self.arm.move_to_safe():
            raise RuntimeError("路线启动前 JAKA 无法回到 home_safe")
        arm_resume = getattr(self.arm, "resume", None)
        if callable(arm_resume):
            arm_resume()
        with self._lock:
            if self._stopped or self._stop_event.is_set():
                raise RuntimeError("演示在启动准备期间已停止")
            self._pause_event.clear()
            self._running = True
            if resume:
                return
            self._route_thread = threading.Thread(target=self._route_loop, name="wenshi-demo-route", daemon=True)
            self._route_thread.start()

    def pause(self) -> None:
        with self._lock:
            self._running = False
            self._pause_event.set()
        try:
            self._stop_agv_and_wait()
        finally:
            self.arm.stop()
        if self.arm.move_to_safe() is False:
            raise RuntimeError("暂停后 JAKA 无法回到 home_safe")
        self.log("演示已暂停，AGV停止且JAKA回到home_safe")

    def _fresh_status(self) -> dict[str, Any]:
        wait_for_status = getattr(self.status, "wait_for_status", None)
        if callable(wait_for_status) and not wait_for_status(timeout=0.8, max_age=0.8):
            raise RuntimeError("AGV定位状态过期")
        value = self.status.get_status()
        if value.get("emergency"):
            raise RuntimeError("AGV急停状态")
        if value.get("blocked"):
            self.motion.stop()
            raise RuntimeError("AGV阻挡，已停止等待人工处理")
        if value.get("fatals") or value.get("errors") or value.get("brake"):
            self.motion.stop()
            raise RuntimeError("AGV报警或刹车状态，已停止等待人工处理")
        if value.get("x") is None or value.get("y") is None or value.get("angle") is None:
            raise RuntimeError("AGV没有有效位姿")
        try:
            pose_values = [float(value[name]) for name in ("x", "y", "angle")]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("AGV没有有效位姿") from exc
        if not all(math.isfinite(item) for item in pose_values):
            self.motion.stop()
            raise RuntimeError("AGV位姿包含非有限数值，已停止等待人工处理")
        return value

    def _require_agv_ready_for_arm(self) -> None:
        value = self._fresh_status()
        if value.get("is_stop") is not True:
            raise RuntimeError("AGV尚未停稳，禁止 JAKA 动作")

    def _run_segment(self, segment: Any) -> bool:
        control = self.config["control"]
        safety = self.config["safety"]
        demo = self.config.get("demo", {})
        cruise_speed = min(abs(float(demo.get("route_speed_mps", 0.18))), 0.18)
        slowdown_distance = float(demo.get("endpoint_slowdown_distance_m", control.get("endpoint_slowdown_distance_m", 0.60)))
        minimum_speed = float(demo.get("endpoint_min_speed_mps", control.get("endpoint_min_speed_mps", 0.04)))
        endpoint_tolerance = float(control.get("endpoint_tolerance_m", 0.10))
        while not self._stop_requested():
            with self._lock:
                active = self._running
            if not active:
                self.motion.stop()
                time.sleep(0.1)
                continue
            status = self._fresh_status()
            if endpoint_reached(status, segment, endpoint_tolerance):
                self.motion.stop()
                self.current_station = segment.end_name
                return True
            approach = segment_progress(
                status,
                segment,
                cross_track_gain=float(control.get("cross_track_gain", 0.8)),
                correction_threshold_m=float(control.get("correction_threshold_m", 0.04)),
            )
            speed = endpoint_approach_speed(
                distance_m=approach.remaining_along,
                cruise_speed_mps=cruise_speed,
                slowdown_distance_m=slowdown_distance,
                stop_distance_m=endpoint_tolerance,
                minimum_speed_mps=minimum_speed,
            )
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
            with self._lock:
                if not self._running or self._pause_event.is_set() or self._stop_event.is_set():
                    continue
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
        base_stop_confirmed = False
        arm_motion_allowed = False
        try:
            try:
                self._stop_agv_and_wait()
                base_stop_confirmed = True
                self._align_station_heading()
                self._require_recorded_station_pose()
                arm_motion_allowed = True
                if not self.arm.move_to_safe():
                    first_error = RuntimeError("JAKA无法回到安全姿态")
                elif not self.arm.observe(demo_view_for_station(self.current_station)):
                    pause_event = getattr(self, "_pause_event", None)
                    if pause_event is None or not pause_event.is_set():
                        first_error = RuntimeError("JAKA展示观察动作失败")
            except RuntimeError as exc:
                first_error = exc
        finally:
            if arm_motion_allowed and not self.arm.move_to_safe():
                if first_error is None:
                    first_error = RuntimeError("JAKA观察后无法回到安全姿态")
                self.log("警告：JAKA观察后回安全姿态失败，请实体急停并人工处理")
            elif not arm_motion_allowed:
                if base_stop_confirmed:
                    self.log("警告：AGV停车姿态校正或校验未通过，机械臂未伸出")
                else:
                    self.log("警告：AGV未确认安全停稳，机械臂未伸出；请实体急停并人工处理")
        if first_error is not None:
            raise first_error
        self.log(f"演示观察完成 station={self.current_station}")

    def _align_station_heading(self) -> None:
        target = self.observation_stations.get(self.current_station)
        if target is None:
            raise RuntimeError(f"当前点不是已登记的水稻停车点: {self.current_station or '<missing>'}")
        demo = self.config.get("demo", {})
        position_limit = float(demo.get("station_position_tolerance_m", 0.15))
        tolerance = math.radians(float(demo.get("heading_alignment_tolerance_deg", 3.0)))
        timeout = max(float(demo.get("heading_alignment_timeout_s", 8.0)), 0.5)
        gain = max(float(demo.get("heading_alignment_gain", 2.0)), 0.1)
        maximum = min(max(float(demo.get("heading_alignment_max_rad_s", 0.45)), 0.05), 0.45)
        minimum = min(max(float(demo.get("heading_alignment_min_rad_s", 0.08)), 0.0), maximum)
        deadline = time.monotonic() + timeout
        try:
            while not self._stop_requested() and time.monotonic() < deadline:
                current = self._fresh_status()
                position_error = math.hypot(float(current["x"]) - target[0], float(current["y"]) - target[1])
                if position_error > position_limit:
                    raise RuntimeError(
                        f"AGV 在停车点 {self.current_station} 原地校正时位置偏差 {position_error:.3f}m，"
                        f"超过 {position_limit:.3f}m"
                    )
                heading_error = normalize_angle(target[2] - float(current["angle"]))
                if abs(heading_error) <= tolerance:
                    self.motion.stop()
                    self._stop_agv_and_wait()
                    confirmed = self._fresh_status()
                    confirmed_position = math.hypot(float(confirmed["x"]) - target[0], float(confirmed["y"]) - target[1])
                    confirmed_heading = abs(normalize_angle(target[2] - float(confirmed["angle"])))
                    if confirmed_position > position_limit:
                        raise RuntimeError(
                            f"AGV 在停车点 {self.current_station} 校正停止后位置偏差 {confirmed_position:.3f}m，"
                            f"超过 {position_limit:.3f}m"
                        )
                    if confirmed_heading <= tolerance:
                        self.log(
                            f"AGV停车朝向已校正 station={self.current_station} "
                            f"error={math.degrees(confirmed_heading):.1f}deg"
                        )
                        return
                    heading_error = normalize_angle(target[2] - float(confirmed["angle"]))
                angular = max(-maximum, min(maximum, gain * heading_error))
                if 0.0 < abs(angular) < minimum:
                    angular = math.copysign(minimum, angular)
                # Share the state lock with pause()/stop() so no rotation
                # command can be issued after either state transition.
                with self._lock:
                    if not self._running or self._pause_event.is_set() or self._stop_event.is_set():
                        raise RuntimeError("演示已暂停或停止")
                    self.motion.set_velocity(0.0, angular)
                time.sleep(0.05)
        finally:
            self.motion.stop()
        if self._stop_requested():
            raise RuntimeError("演示已停止")
        raise RuntimeError(
            f"AGV 在停车点 {self.current_station} 朝向校正超时，"
            f"未进入 {math.degrees(tolerance):.1f}deg 容差"
        )

    def _require_recorded_station_pose(self) -> None:
        target = self.observation_stations.get(self.current_station)
        if target is None:
            raise RuntimeError(f"当前点不是已登记的水稻停车点: {self.current_station or '<missing>'}")
        current = self._fresh_status()
        if current.get("is_stop") is not True:
            raise RuntimeError("AGV尚未停稳，禁止伸臂")
        position_error = math.hypot(float(current["x"]) - target[0], float(current["y"]) - target[1])
        heading_error = abs(normalize_angle(float(current["angle"]) - target[2]))
        demo = self.config.get("demo", {})
        position_tolerance = float(demo.get("station_position_tolerance_m", 0.15))
        heading_tolerance = math.radians(float(demo.get("heading_alignment_tolerance_deg", 3.0)))
        if position_error > position_tolerance:
            raise RuntimeError(
                f"AGV 距离停车点 {self.current_station} 偏差 {position_error:.3f}m，"
                f"超过 {position_tolerance:.3f}m，禁止伸臂"
            )
        if heading_error > heading_tolerance:
            raise RuntimeError(
                f"AGV 在停车点 {self.current_station} 的朝向偏差 {math.degrees(heading_error):.1f}deg，"
                f"超过 {math.degrees(heading_tolerance):.1f}deg，禁止伸臂"
            )

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
            if current.get("fatals") or current.get("errors") or current.get("brake"):
                raise RuntimeError("AGV到站后处于报警或刹车状态")
            if bool(current.get("is_stop")):
                return
            time.sleep(0.05)
        if self._stop_requested():
            raise RuntimeError("演示已停止")
        raise RuntimeError("AGV停止命令未获得 is_stop 确认")

    def _route_loop(self) -> None:
        try:
            while not self._stop_requested():
                minimum = int(self.config.get("demo", {}).get("min_observations", 3))
                maximum = int(self.config.get("demo", {}).get("max_observations", 5))
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

    def _agv_safe_for_retract(self) -> bool:
        timeout = max(float(self.config.get("safety", {}).get("station_stop_timeout_s", 2.0)), 0.2)
        deadline = time.monotonic() + timeout
        wait_for_status = getattr(self.status, "wait_for_status", None)
        while True:
            if callable(wait_for_status) and not wait_for_status(
                timeout=min(0.25, max(0.0, deadline - time.monotonic())),
                max_age=0.8,
            ):
                if time.monotonic() < deadline:
                    continue
                return False
            current = self.status.get_status()
            if current.get("emergency") or current.get("blocked") or current.get("fatals") or current.get("errors") or current.get("brake"):
                return False
            age = current.get("status_age")
            if age is not None and float(age) > 0.8:
                return False
            if current.get("is_stop") is True:
                return True
            if not callable(wait_for_status) or time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

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
                if not self._agv_safe_for_retract():
                    self.log("警告：AGV未确认安全停稳，停止后跳过JAKA回撤；请实体急停并人工处理")
                else:
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


class _SetupCancelled(RuntimeError):
    pass


class DemoSetupPreview:
    """Continuously display D435 RGB and expose the exact latest visible frame."""

    WINDOW_TITLE = "Wenshi Demo Setup - D435 RGB"

    def __init__(self, camera: DemoCamera, startup_timeout_s: float = 4.0):
        self.camera = camera
        self.startup_timeout_s = max(float(startup_timeout_s), 0.5)
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._latest_at = 0.0
        self._thread: threading.Thread | None = None
        self._window_created = False
        self.last_error = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise RuntimeError("当前没有图形桌面，无法显示 D435 预览；请在 Ubuntu 桌面终端运行 setup")
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run, name="demo-setup-camera-preview", daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=self.startup_timeout_s):
            error = self.last_error or "等待首帧超时"
            self.stop()
            raise RuntimeError(f"D435 实时预览启动失败: {error}")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame = self.camera.color()
                if not isinstance(frame, np.ndarray) or frame.size == 0:
                    raise RuntimeError("D435 返回空 RGB 画面")
                with self._lock:
                    self._latest = frame.copy()
                    self._latest_at = time.monotonic()
                display = frame.copy()
                cv2.putText(
                    display,
                    "D435 RGB LIVE",
                    (12, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.65,
                    (0, 220, 80),
                    2,
                )
                cv2.imshow(self.WINDOW_TITLE, display)
                self._window_created = True
                self.last_error = ""
                self._ready_event.set()
                cv2.waitKey(1)
            except Exception as exc:
                self.last_error = str(exc)
                time.sleep(0.2)
        self._close_window()

    def color(self) -> np.ndarray:
        with self._lock:
            image = None if self._latest is None else self._latest.copy()
            age = time.monotonic() - self._latest_at if self._latest_at else float("inf")
        if image is None or age > 2.0 or self._stop_event.is_set():
            detail = self.last_error or f"最新画面已过期 {age:.1f}s"
            raise RuntimeError(f"D435 实时预览不可用: {detail}")
        return image

    def _close_window(self) -> None:
        if not self._window_created:
            return
        try:
            cv2.destroyWindow(self.WINDOW_TITLE)
        except cv2.error:
            pass
        self._window_created = False

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        self._close_window()


def _setup_photo(camera: Any | None, label: str) -> np.ndarray:
    while True:
        answer = input(f"{label}：回车从 D435 拍照，或输入已有图片路径，q 中止: ").strip()
        if answer.lower() == "q":
            raise _SetupCancelled("操作员中止 Demo setup")
        if answer:
            image = cv2.imread(str(Path(answer).expanduser()), cv2.IMREAD_COLOR)
            if image is None:
                print(f"无法读取图片: {answer}，请重试")
                continue
            return image
        if camera is None:
            print("当前使用 --no-camera，必须输入已有图片路径")
            continue
        image = camera.color()
        if isinstance(image, np.ndarray) and image.size:
            return image
        print("D435 返回空画面，请重试")


def _setup_station_pose(status: AGVStatusClient) -> dict[str, float]:
    if not status.wait_for_status(timeout=1.5, max_age=0.8):
        raise RuntimeError("AGV 定位状态过期")
    value = status.get_status()
    if value.get("emergency") or value.get("blocked") or value.get("fatals") or value.get("errors") or value.get("brake"):
        raise RuntimeError("AGV 处于急停、阻挡、报警或刹车状态")
    if value.get("is_stop") is not True:
        raise RuntimeError("AGV 尚未停稳")
    try:
        pose = {name: float(value[name]) for name in ("x", "y", "angle")}
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("AGV 没有有效 x/y/angle 位姿") from exc
    if not all(math.isfinite(item) for item in pose.values()):
        raise RuntimeError("AGV 位姿包含非有限数值")
    return pose


def _require_setup_pose_stability(
    before: dict[str, float],
    after: dict[str, float],
    config: dict[str, Any],
) -> None:
    demo = config.get("demo", {})
    position_limit = float(demo.get("setup_position_stability_m", 0.02))
    heading_limit = math.radians(float(demo.get("setup_heading_stability_deg", 2.0)))
    position_error = math.hypot(float(after["x"]) - float(before["x"]), float(after["y"]) - float(before["y"]))
    heading_error = abs(normalize_angle(float(after["angle"]) - float(before["angle"])))
    if position_error > position_limit or heading_error > heading_limit:
        raise RuntimeError(
            "AGV 在停车点拍照期间发生移动："
            f"位置变化 {position_error:.3f}m，朝向变化 {math.degrees(heading_error):.1f}deg"
        )


def _setup_arm_pose(client: JakaClient, name: str) -> tuple[list[float], list[float] | None]:
    client.disconnect()
    try:
        if not client.connect(timeout=3.0):
            detail = str(getattr(client, "last_error", "")).strip()
            raise RuntimeError(f"无法连接 JAKA 读取 {name}" + (f": {detail}" if detail else ""))
        if not client.wait_for_joint_state(timeout=3.0):
            detail = str(getattr(client, "last_error", "")).strip()
            raise RuntimeError(f"无法读取 JAKA 的 {name} 关节角" + (f": {detail}" if detail else ""))
        snapshot = client.snapshot()
        joint = snapshot.get("joint")
        tcp = snapshot.get("tcp")
        if not isinstance(joint, list) or len(joint) != 6 or not all(math.isfinite(float(item)) for item in joint):
            raise RuntimeError(f"JAKA 的 {name} 关节角无效")
        if not isinstance(tcp, list) or len(tcp) != 6 or not all(math.isfinite(float(item)) for item in tcp):
            tcp = None
        return [float(item) for item in joint], None if tcp is None else [float(item) for item in tcp]
    finally:
        client.disconnect()


def run_interactive_setup(
    config: dict[str, Any],
    destination: Path,
    *,
    camera_enabled: bool = True,
    resume_root: Path | None = None,
    skip_archived_tags: bool = False,
) -> int:
    """Record a complete Demo-only setup without sending any motion command."""
    destination = Path(destination).expanduser().resolve()
    if resume_root is None:
        evidence_root = destination.parent / time.strftime("setup_%Y%m%d_%H%M%S")
        setup = DemoSetupSession(evidence_root)
    else:
        evidence_root = Path(resume_root).expanduser().resolve()
        try:
            setup = DemoSetupSession.resume(evidence_root)
        except (OSError, ValueError) as exc:
            print(f"Demo setup 恢复失败: {exc}")
            return 1
    camera: DemoCamera | None = None
    preview: DemoSetupPreview | None = None
    photo_source: Any | None = None
    status: AGVStatusClient | None = None
    arm: JakaClient | None = None
    route_rows: tuple[float, float] | None = None
    print(f"Demo setup 证据目录: {evidence_root}")
    print("此流程只读取相机、AGV 位姿和 JAKA 关节角，不会发送 AGV/JAKA 运动命令。")
    try:
        if setup.stations:
            route_rows = _demo_route_rows(config)
            setup.migrate_and_mirror_stations(top_route_y=route_rows[0], bottom_route_y=route_rows[1])
        if resume_root is not None:
            recorded_count = sum(group_id in setup.stations for group_id in RECORDED_STATIONS)
            mirrored_count = sum(group_id in setup.stations for group_id in B_LEFT_STATIONS)
            viewpoint_count = sum(name in setup.viewpoints for name in VIEWPOINT_NAMES)
            print(
                "恢复进度: "
                f"Tag 0={'已完成' if setup.calibration_board.get('photo') else '未完成'}, "
                f"植株 Tag={len(setup.tags)}/32, 实录停车点={recorded_count}/16, "
                f"B-L镜像点={mirrored_count}/8, Demo目标={len(setup.stations)}/24, "
                f"机械臂姿态={viewpoint_count}/3"
            )
        if setup.operator:
            print(f"沿用操作员: {setup.operator}")
        else:
            setup.set_operator(input("操作员姓名/编号: "))
        if camera_enabled:
            camera = DemoCamera(str(config["camera"]["server_url"]), float(config["camera"].get("timeout_s", 1.5)))
            health = camera.health()
            if not health.get("ok"):
                raise RuntimeError(f"D435 健康检查失败: {health.get('error', 'unknown')}")
            preview = DemoSetupPreview(camera)
            preview.start()
            photo_source = preview
            print("D435 已连接，实时预览窗口已打开；每次在终端回车只保存当前显示的一张 JPEG。")
        else:
            print("D435 已禁用；每次拍照提示都要输入已有 JPEG/PNG 路径。")

        print("先登记 Tag 0 标定板和展示所需的 1-24 号植株 Tag。C 排 25-32 只留档，可延期登记。")
        if skip_archived_tags:
            print("本次已选择延期登记 C 排 Tag 25-32；不影响专家展示。")
        if setup.calibration_board.get("photo"):
            print("已复用 Tag 0 标定板照片")
        else:
            setup.record_calibration_board(_setup_photo(photo_source, "Tag 0 标定板"))
        for plant_id, expected_tag, observed in DEMO_PLANTS:
            if plant_id in setup.tags:
                continue
            if skip_archived_tags and not observed:
                continue
            while True:
                answer = input(f"{plant_id} Tag ID [{expected_tag}]: ").strip()
                if answer.lower() == "q":
                    raise _SetupCancelled("操作员中止 Demo setup")
                try:
                    tag_id = expected_tag if not answer else int(answer)
                    if tag_id != expected_tag:
                        raise ValueError(f"应为 {expected_tag}")
                    photo = _setup_photo(photo_source, f"{plant_id} / Tag {tag_id}")
                    setup.record_tag(plant_id, tag_id, photo=photo, observed=observed)
                    break
                except (OSError, RuntimeError, ValueError) as exc:
                    print(f"{plant_id} 登记失败: {exc}，请重试")

        if route_rows is None:
            route_rows = _demo_route_rows(config)
        setup.migrate_and_mirror_stations(top_route_y=route_rows[0], bottom_route_y=route_rows[1])
        missing_stations = [group_id for group_id in RECORDED_STATIONS if group_id not in setup.stations]
        if missing_stations:
            agv = config["agv"]
            status = AGVStatusClient(
                str(agv["ip"]),
                int(agv.get("status_port", 19204)),
                interval_ms=int(agv.get("status_interval_ms", 200)),
                response_timeout_s=float(agv.get("status_response_timeout_s", 0.8)),
            )
            if not status.connect() or not status.wait_for_status(timeout=3.0, max_age=1.0):
                raise RuntimeError("AGV 状态不可用，不能记录 Demo 停车点")
            print("逐个登记缺少的 A 排和 B-R 实录停车点；B-L 将按地图路线自动镜像，不需要重录。")
            for group_id in missing_stations:
                while True:
                    answer = input(f"{group_id}：人工将 AGV 驶到水稻旁并停稳，回车读取实时位姿，q 中止: ").strip().lower()
                    if answer == "q":
                        raise _SetupCancelled("操作员中止 Demo setup")
                    if answer:
                        print("这里只接受回车读取 AGV 实时位姿，或 q 中止")
                        continue
                    try:
                        pose = _setup_station_pose(status)
                        photo = _setup_photo(photo_source, f"{group_id} 停车点")
                        confirmed_pose = _setup_station_pose(status)
                        _require_setup_pose_stability(pose, confirmed_pose, config)
                        note = input(f"{group_id} 备注（回车跳过）: ").strip()
                        setup.record_station(group_id, confirmed_pose, photo=photo, source="agv_status", note=note)
                        print(
                            f"已登记 {group_id}: x={confirmed_pose['x']:.3f} "
                            f"y={confirmed_pose['y']:.3f} angle={confirmed_pose['angle']:.3f}"
                        )
                        break
                    except (OSError, RuntimeError, ValueError) as exc:
                        print(f"{group_id} 登记失败: {exc}，请重试")
            status.disconnect()
            status = None
        setup.migrate_and_mirror_stations(top_route_y=route_rows[0], bottom_route_y=route_rows[1])
        print(
            "停车点已就绪: "
            f"实录={sum(group_id in setup.stations for group_id in RECORDED_STATIONS)}/16, "
            f"B-L镜像={sum(group_id in setup.stations for group_id in B_LEFT_STATIONS)}/8, "
            f"Demo目标={len(setup.stations)}/24"
        )

        missing_viewpoints = [name for name in VIEWPOINT_NAMES if name not in setup.viewpoints]
        if missing_viewpoints:
            jaka = config["jaka"]
            arm = JakaClient(str(jaka["ip"]), int(jaka.get("port", 10001)))
            print("使用 JAKA 示教器手动摆好三个姿态；每次保存会重新短连接读取，不会主动移动机械臂。")
            labels = {
                "home_safe": "安全收回姿态 home_safe",
                "left": "向左观察姿态 left",
                "right": "向右观察姿态 right",
            }
            for name in missing_viewpoints:
                while True:
                    answer = input(f"摆好并确认 {labels[name]} 无碰撞风险后输入 yes，q 中止: ").strip().lower()
                    if answer == "q":
                        raise _SetupCancelled("操作员中止 Demo setup")
                    if answer != "yes":
                        print("请输入 yes 确认，或 q 中止")
                        continue
                    try:
                        joint, tcp = _setup_arm_pose(arm, name)
                        setup.record_viewpoint(name, joint, tcp)
                        print(f"已登记 {name}")
                        break
                    except (OSError, RuntimeError, ValueError) as exc:
                        print(f"{name} 登记失败: {exc}，请保持当前姿态并再次输入 yes 重试，或输入 q 中止")
            arm.disconnect()
            arm = None

        setup.publish(destination)
        print(f"Demo setup 已发布: {destination}")
        print("下一步先运行 ./wenshi.sh --dry-run，再运行 ./wenshi.sh")
        return 0
    except _SetupCancelled as exc:
        print(str(exc))
        print(f"当前进度已保存: {setup.draft_path}")
        return 2
    except KeyboardInterrupt:
        print(f"\nDemo setup 已取消；当前进度已保存: {setup.draft_path}")
        return 130
    except EOFError:
        print(f"\nDemo setup 输入已关闭；当前进度已保存: {setup.draft_path}")
        return 2
    except Exception as exc:
        print(f"\nDemo setup 失败: {exc}；当前进度保存在 {setup.draft_path}。")
        return 1
    finally:
        if preview is not None:
            preview.stop()
        if status is not None:
            status.disconnect()
        if arm is not None:
            arm.disconnect()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wenshi expert greenhouse demonstration")
    parser.add_argument("--config", default=str(Path(__file__).parents[2] / "config" / "wenshi.yaml"))
    parser.add_argument("--photos", default=str(Path(__file__).parents[2] / "runtime" / "demo" / "photos"))
    parser.add_argument("--setup-file", default=str(Path(__file__).parents[2] / "runtime" / "demo" / "demo_setup.json"))
    parser.add_argument("--setup", action="store_true", help="交互登记独立 Demo 现场配置")
    parser.add_argument("--resume", help="从指定 Demo setup 证据目录继续登记")
    parser.add_argument("--skip-c-tags", action="store_true", help="本次延期登记不参与展示的 C 排 Tag 25-32")
    parser.add_argument("--no-rviz", action="store_true")
    parser.add_argument("--no-camera", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if args.resume and not args.setup:
        parser.error("--resume 只能与 --setup 一起使用")
    if args.skip_c_tags and not args.setup:
        parser.error("--skip-c-tags 只能与 --setup 一起使用")
    if args.setup:
        if args.dry_run:
            parser.error("--setup 与 --dry-run 不能同时使用")
        return run_interactive_setup(
            config,
            Path(args.setup_file),
            camera_enabled=not args.no_camera,
            resume_root=None if args.resume is None else Path(args.resume),
            skip_archived_tags=args.skip_c_tags,
        )
    if args.dry_run:
        try:
            DemoController(
                config,
                Path(args.photos),
                setup_file=args.setup_file,
                camera_enabled=not args.no_camera,
            )
        except ValueError as exc:
            print(f"Demo dry-run 失败: {exc}", file=__import__("sys").stderr)
            return 2
        print("dry-run: local config/map/route/home_safe validated; no AGV/JAKA/D435 connection, no monitoring, no photos")
        return 0
    session: DemoController | None = None
    try:
        session = DemoController(
            config,
            Path(args.photos),
            setup_file=args.setup_file,
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
