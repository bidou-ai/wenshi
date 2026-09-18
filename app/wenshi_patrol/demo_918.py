"""Tag-free expert demonstration for the independent 9.18 field site."""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
from pathlib import Path
import random
import threading
import time
from typing import Any, Callable, Mapping, Sequence
import urllib.request

import cv2
import numpy as np

from .agv import AGVMotionClient, AGVStatusClient
from .config import load_config
from .control.route_math import (
    Segment,
    compute_segment_velocity,
    endpoint_approach_speed,
    endpoint_reached,
    make_segments,
    normalize_angle,
    segment_progress,
)
from .demo_918_setup import Setup918Session, VIEWPOINT_NAMES, load_918_setup
from .jaka import JakaClient


class Demo918Paused(RuntimeError):
    """Internal control-flow signal for an operator pause during station alignment."""


class _SetupCancelled(RuntimeError):
    pass


def choose_918_observations(
    names: Sequence[str],
    *,
    rng: Any = random,
) -> list[str]:
    candidates = [str(name) for name in names]
    if not candidates or len(candidates) != len(set(candidates)):
        raise ValueError("9.18 观察点列表必须非空且不能重复")
    if len(candidates) <= 3:
        return list(candidates)
    count = rng.randint(3, min(5, len(candidates)))
    return list(rng.sample(candidates, count))


def _observation_pose(record: Any, name: str) -> tuple[float, float, float]:
    value = record.get("pose") if isinstance(record, dict) else record
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"9.18 观察点 {name} 位姿无效")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"9.18 观察点 {name} 包含非有限位姿")
    return result


def _project_to_segment(point: tuple[float, float, float], segment: Segment) -> tuple[float, float]:
    dx = segment.end[0] - segment.start[0]
    dy = segment.end[1] - segment.start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        raise ValueError(f"9.18 路线段长度过短: {segment.start_name}->{segment.end_name}")
    ratio = (
        (point[0] - segment.start[0]) * dx + (point[1] - segment.start[1]) * dy
    ) / length_sq
    ratio = max(0.0, min(1.0, ratio))
    projected_x = segment.start[0] + ratio * dx
    projected_y = segment.start[1] + ratio * dy
    return math.hypot(point[0] - projected_x, point[1] - projected_y), ratio


def _insert_observations(
    base: list[Segment],
    observations: Mapping[str, Any],
    selected: Sequence[str],
    max_distance_m: float,
) -> list[Segment]:
    by_segment: dict[int, list[tuple[float, str, tuple[float, float, float]]]] = {
        index: [] for index in range(len(base))
    }
    for name in selected:
        pose = _observation_pose(observations[name], name)
        candidates = [(_project_to_segment(pose, segment) + (index,)) for index, segment in enumerate(base)]
        distance, ratio, index = min(candidates, key=lambda item: item[0])
        if distance > float(max_distance_m):
            raise ValueError(
                f"9.18 观察点 {name} 偏离示教路线 {distance:.3f}m，超过 {float(max_distance_m):.3f}m"
            )
        by_segment[index].append((ratio, name, pose))
    result: list[Segment] = []
    for index, segment in enumerate(base):
        previous_name = segment.start_name
        previous_pose = segment.start
        for _ratio, name, pose in sorted(by_segment[index], key=lambda item: item[0]):
            result.append(Segment(previous_name, name, previous_pose, pose))
            previous_name, previous_pose = name, pose
        result.append(Segment(previous_name, segment.end_name, previous_pose, segment.end))
    return result


def build_918_lap_segments(
    anchors: Mapping[str, tuple[float, float, float]],
    order: Sequence[str],
    observations: Mapping[str, Any],
    selected: Sequence[str],
    mode: str,
    max_distance_m: float,
) -> list[Segment]:
    names = [str(name) for name in selected]
    if len(names) != len(set(names)) or not set(names).issubset(observations):
        raise ValueError("9.18 本轮选择了不存在或重复的观察点")
    route_order = [str(name) for name in order]
    if mode == "loop":
        base = make_segments(dict(anchors), route_order, loop=True)
        return _insert_observations(base, observations, names, max_distance_m)
    if mode != "shuttle":
        raise ValueError("9.18 路线模式必须是 shuttle 或 loop")
    outward = make_segments(dict(anchors), route_order, loop=False)
    result = _insert_observations(outward, observations, names, max_distance_m)
    result.extend(make_segments(dict(anchors), list(reversed(route_order)), loop=False))
    return result


class Camera918:
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

    def color(self) -> np.ndarray:
        packet = self._json("frame")
        if not packet.get("ok"):
            raise RuntimeError(str(packet.get("error", "D435 frame unavailable")))
        raw = base64.b64decode(str(packet.get("color_jpeg_b64", "")).encode("ascii"), validate=True)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError("D435 color decode failed")
        return image


class Arm918:
    def __init__(
        self,
        config: dict[str, Any],
        setup: dict[str, Any],
        log: Callable[[str], None] = print,
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
        viewpoints = setup["viewpoints"]
        self.safe = [float(item) for item in viewpoints["home_safe"]["joint"]]
        self.poses = {
            name: [float(item) for item in viewpoints[name]["joint"]]
            for name in ("left", "right")
        }
        demo = config.get("demo_918", {})
        self.observe_speed = min(max(float(demo.get("arm_observe_speed_deg_s", 50.0)), 1.0), 60.0)
        self.retract_speed = min(max(float(demo.get("arm_retract_speed_deg_s", 60.0)), 1.0), 60.0)
        self.accel = min(max(float(demo.get("arm_accel_deg_s2", 80.0)), 1.0), 80.0)
        self.timeout = float(arm.get("motion_timeout_s", 120.0))
        self.observation_hold_s = max(float(demo.get("observation_hold_s", 2.0)), 0.0)
        self._motion_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self.cancel_requested: Callable[[], bool] | None = None

    def connect(self) -> None:
        if not self.client.connect(timeout=3.0) or not self.client.wait_for_joint_state(timeout=3.0):
            raise RuntimeError(self.client.last_error or "JAKA connection failed")

    def _cancelled(self) -> bool:
        return self._cancel_event.is_set() or (
            self.cancel_requested is not None and self.cancel_requested()
        )

    def move_to_view(self, view: str) -> bool:
        if view not in self.poses:
            raise ValueError(f"9.18 未知机械臂观察方向: {view}")
        with self._motion_lock:
            if self._cancelled():
                return False
            return bool(
                self.client.joint_move(
                    self.poses[view],
                    self.observe_speed,
                    self.accel,
                    self.timeout,
                    cancel_requested=self._cancelled,
                )
            )

    def observe(self, view: str) -> bool:
        if self._cancelled() or not self.move_to_view(view):
            return False
        deadline = time.monotonic() + self.observation_hold_s
        while time.monotonic() < deadline:
            if self._cancelled():
                return False
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
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


class RosPublisher918:
    """Publish the independent site map and live Demo state for RViz."""

    def __init__(
        self,
        config: dict[str, Any],
        status: Any,
        setup: dict[str, Any],
        log: Callable[[str], None] = print,
    ):
        import rclpy
        from geometry_msgs.msg import PoseStamped
        from nav_msgs.msg import OccupancyGrid
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import String
        from visualization_msgs.msg import MarkerArray

        self._rclpy = rclpy
        self._PoseStamped = PoseStamped
        self._String = String
        self._MarkerArray = MarkerArray
        self._status = status
        self._setup = setup
        self._log = log
        self._stopped = False
        self._selection_lock = threading.Lock()
        self._observation_names = set(setup["observations"])
        self._selected: set[str] = set()
        initialized = False
        try:
            rclpy.init(args=[])
            initialized = True
            self._node = rclpy.create_node("wenshi_918_expert_demo_visualizer")
            topics = config["topics"]
            transient = QoSProfile(depth=1)
            transient.durability = DurabilityPolicy.TRANSIENT_LOCAL
            transient.reliability = ReliabilityPolicy.RELIABLE
            self._map_pub = self._node.create_publisher(OccupancyGrid, topics["map"], transient)
            self._marker_pub = self._node.create_publisher(MarkerArray, topics["markers"], transient)
            self._pose_pub = self._node.create_publisher(PoseStamped, topics["agv_pose"], 10)
            self._state_pub = self._node.create_publisher(String, topics["state"], transient)
            from .map_utils import make_occupancy_grid

            stamp = self._node.get_clock().now().to_msg()
            self._map_message = make_occupancy_grid(setup["map_path"], stamp)
            self._node.create_timer(0.2, self._publish)
            self._thread = threading.Thread(
                target=self._spin, name="wenshi-918-demo-rviz", daemon=True
            )
            self._thread.start()
            self._log("9.18 专家演示 RViz 已启动：新地图、AGV位姿、动态路线、观察点和D435图像")
        except Exception:
            if initialized and rclpy.ok():
                rclpy.shutdown()
            raise

    def update_selection(self, names: Sequence[str]) -> None:
        selected = {str(name) for name in names}
        unknown = selected - self._observation_names
        if unknown:
            raise ValueError(f"9.18 RViz 选择了不存在的观察点: {', '.join(sorted(unknown))}")
        with self._selection_lock:
            self._selected = selected

    def _site_markers(self, stamp: Any) -> Any:
        from geometry_msgs.msg import Point
        from visualization_msgs.msg import Marker

        markers = self._MarkerArray()
        marker_id = 0
        anchors = self._setup["anchors"]
        order = self._setup["route_order"]

        route = Marker()
        route.header.frame_id = "map"
        route.header.stamp = stamp
        route.ns = "demo918_route"
        route.id = marker_id
        marker_id += 1
        route.type = Marker.LINE_STRIP
        route.action = Marker.ADD
        route.pose.orientation.w = 1.0
        route.scale.x = 0.05
        route.color.r = 0.12
        route.color.g = 0.65
        route.color.b = 0.95
        route.color.a = 0.9
        route_names = list(order)
        if self._setup["route_mode"] == "loop":
            route_names.append(order[0])
        for name in route_names:
            point = Point()
            point.x = float(anchors[name][0])
            point.y = float(anchors[name][1])
            point.z = 0.03
            route.points.append(point)
        markers.markers.append(route)

        for name in order:
            x, y, _angle = anchors[name]
            marker_id = self._append_point_markers(
                markers,
                stamp,
                marker_id,
                name,
                float(x),
                float(y),
                namespace="demo918_anchors",
                color=(0.10, 0.78, 0.30),
                size=0.14,
            )

        with self._selection_lock:
            selected = set(self._selected)
        for name, record in self._setup["observations"].items():
            x, y, _angle = record["pose"]
            active = name in selected
            marker_id = self._append_point_markers(
                markers,
                stamp,
                marker_id,
                name,
                float(x),
                float(y),
                namespace="demo918_observations",
                color=(1.0, 0.22, 0.12) if active else (1.0, 0.72, 0.05),
                size=0.20 if active else 0.13,
            )
        return markers

    @staticmethod
    def _append_point_markers(
        message: Any,
        stamp: Any,
        marker_id: int,
        name: str,
        x: float,
        y: float,
        *,
        namespace: str,
        color: tuple[float, float, float],
        size: float,
    ) -> int:
        from visualization_msgs.msg import Marker

        point = Marker()
        point.header.frame_id = "map"
        point.header.stamp = stamp
        point.ns = namespace
        point.id = marker_id
        marker_id += 1
        point.type = Marker.SPHERE
        point.action = Marker.ADD
        point.pose.position.x = x
        point.pose.position.y = y
        point.pose.position.z = 0.10
        point.pose.orientation.w = 1.0
        point.scale.x = point.scale.y = point.scale.z = size
        point.color.r, point.color.g, point.color.b = color
        point.color.a = 1.0
        message.markers.append(point)

        label = Marker()
        label.header = point.header
        label.ns = f"{namespace}_labels"
        label.id = marker_id
        marker_id += 1
        label.type = Marker.TEXT_VIEW_FACING
        label.action = Marker.ADD
        label.pose.position.x = x
        label.pose.position.y = y
        label.pose.position.z = 0.32
        label.pose.orientation.w = 1.0
        label.scale.z = 0.16
        label.color.r = label.color.g = label.color.b = label.color.a = 1.0
        label.text = name
        message.markers.append(label)
        return marker_id

    def _spin(self) -> None:
        try:
            self._rclpy.spin(self._node)
        except Exception as exc:
            if not self._stopped:
                self._log(f"demo918_rviz_error {exc}")

    def _publish(self) -> None:
        stamp = self._node.get_clock().now().to_msg()
        self._map_message.header.stamp = stamp
        self._map_pub.publish(self._map_message)
        self._marker_pub.publish(self._site_markers(stamp))
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
        with self._selection_lock:
            selected = ",".join(sorted(self._selected)) or "none"
        state.data = (
            f"WENSHI_918_DEMO: display only; route={self._setup['route_mode']}; "
            f"selected={selected}; no monitoring or phenotyping"
        )
        self._state_pub.publish(state)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._node.destroy_node()
        if self._rclpy.ok():
            self._rclpy.shutdown()
        thread = getattr(self, "_thread", None)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)


class Controller918:
    def __init__(
        self,
        config: dict[str, Any],
        photo_dir: Path,
        *,
        setup_file: str | Path,
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
        self.setup = load_918_setup(setup_file)
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        agv = config["agv"]
        self.status = status or AGVStatusClient(
            str(agv["ip"]),
            int(agv.get("status_port", 19204)),
            interval_ms=int(agv.get("status_interval_ms", 200)),
            response_timeout_s=float(agv.get("status_response_timeout_s", 0.8)),
            log=log,
        )
        self.motion = motion or AGVMotionClient(
            str(agv["ip"]),
            int(agv.get("motion_port", 19205)),
            send_rate_hz=float(config["control"].get("rate_hz", 20.0)),
            watchdog_s=float(config["safety"].get("command_watchdog_s", 0.3)),
            log=log,
        )
        self.arm = arm or Arm918(config, self.setup, log)
        if hasattr(self.arm, "cancel_requested"):
            self.arm.cancel_requested = lambda: self._stop_event.is_set() or self._pause_event.is_set()
        camera_config = config["camera"]
        self.camera = camera or Camera918(
            str(camera_config["server_url"]), float(camera_config.get("timeout_s", 1.5))
        )
        self.camera_enabled = bool(camera_enabled)
        self.ros = ros
        self.anchors = self.setup["anchors"]
        self.route_order = self.setup["route_order"]
        self.route_mode = self.setup["route_mode"]
        self.observations = self.setup["observations"]
        self.observation_order = list(self.observations)
        self.max_observation_offset_m = float(self.setup["max_observation_offset_m"])
        self.base_segments = build_918_lap_segments(
            self.anchors,
            self.route_order,
            self.observations,
            [],
            self.route_mode,
            self.max_observation_offset_m,
        )
        self.photo_dir = Path(photo_dir).expanduser().resolve()
        self._photo_index = 0
        self._stopped = False
        self._running = False
        self._route_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.current_station = ""

    def _stop_requested(self) -> bool:
        return bool(self._stopped or self._stop_event.is_set())

    def connect(self) -> None:
        if not self.status.connect() or not self.status.wait_for_status(timeout=3.0, max_age=1.0):
            raise RuntimeError("AGV status connection or fresh status failed")
        if not self.motion.connect():
            raise RuntimeError(self.motion.last_error or "AGV motion connection failed")
        self.arm.connect()
        if not self.camera_enabled:
            self.log("9.18 D435 健康检查已跳过；photo 命令不可用")
            return
        health = self.camera.health()
        if not health.get("ok"):
            raise RuntimeError(str(health.get("error", "D435 health check failed")))
        deadline = time.monotonic() + max(float(self.config["camera"].get("startup_wait_s", 3.0)), 0.0)
        while True:
            try:
                frame = self.camera.color()
                if not isinstance(frame, np.ndarray) or frame.size == 0:
                    raise RuntimeError("D435 RGB frame is empty")
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)

    def start(self) -> None:
        with self._lock:
            if self._stopped or self._stop_event.is_set():
                raise RuntimeError("9.18 演示已停止，不能重新 start")
            route_thread = self._route_thread
            resume = bool(route_thread and route_thread.is_alive())
        self._require_agv_ready_for_arm()
        if not self.arm.move_to_safe():
            raise RuntimeError("9.18 路线启动前 JAKA 无法回到 home_safe")
        resume_arm = getattr(self.arm, "resume", None)
        if callable(resume_arm):
            resume_arm()
        with self._lock:
            if self._stopped or self._stop_event.is_set():
                raise RuntimeError("9.18 演示在启动准备期间已停止")
            self._pause_event.clear()
            self._running = True
            if resume:
                return
            self._route_thread = threading.Thread(
                target=self._route_loop, name="wenshi-918-demo-route", daemon=True
            )
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
            raise RuntimeError("9.18 暂停后 JAKA 无法回到 home_safe")
        self.log("9.18 演示已暂停，AGV停止且JAKA回到home_safe")

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
        try:
            pose = [float(value[name]) for name in ("x", "y", "angle")]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("AGV没有有效位姿") from exc
        if not all(math.isfinite(item) for item in pose):
            self.motion.stop()
            raise RuntimeError("AGV位姿包含非有限数值，已停止等待人工处理")
        return value

    def _require_agv_ready_for_arm(self) -> None:
        if self._fresh_status().get("is_stop") is not True:
            raise RuntimeError("AGV尚未停稳，禁止 JAKA 动作")

    def _run_segment(self, segment: Segment) -> bool:
        control = self.config["control"]
        safety = self.config["safety"]
        demo = self.config.get("demo_918", {})
        cruise_speed = min(abs(float(demo.get("route_speed_mps", 0.18))), 0.18)
        slowdown_distance = float(demo.get("endpoint_slowdown_distance_m", 0.60))
        minimum_speed = float(demo.get("endpoint_min_speed_mps", 0.04))
        endpoint_tolerance = float(control.get("endpoint_tolerance_m", 0.10))
        while not self._stop_requested():
            with self._lock:
                active = self._running and not self._pause_event.is_set()
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
                status,
                segment,
                speed,
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
                raise RuntimeError(f"9.18 路线横向偏差过大: {progress.cross_track:.3f}m")
            with self._lock:
                if not self._running or self._pause_event.is_set() or self._stop_event.is_set():
                    continue
                self.motion.set_velocity(velocity, angular)
            time.sleep(0.05)
        self.motion.stop()
        return False

    def _route_attachment_index(self, status: dict[str, Any], segments: Sequence[Segment]) -> int:
        best: tuple[float, int] | None = None
        for index, segment in enumerate(segments):
            progress = segment_progress(status, segment, cross_track_gain=0.0)
            along = max(0.0, min(progress.length, progress.along_track))
            projected_x = segment.start[0] + along * math.cos(progress.segment_yaw)
            projected_y = segment.start[1] + along * math.sin(progress.segment_yaw)
            distance = math.hypot(
                float(status["x"]) - projected_x, float(status["y"]) - projected_y
            )
            candidate = (distance, index)
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is None:
            raise RuntimeError("9.18 本轮没有可用路线")
        limit = float(self.config["safety"].get("hard_cross_track_m", 0.25))
        if best[0] > limit:
            raise RuntimeError(f"当前位置离 9.18 路线过远: {best[0]:.3f}m")
        return best[1]

    def _observation_target(self) -> tuple[float, float, float]:
        record = self.observations.get(self.current_station)
        if record is None:
            raise RuntimeError(f"当前点不是 9.18 观察点: {self.current_station or '<missing>'}")
        return _observation_pose(record, self.current_station)

    def _observe_current(self) -> None:
        first_error: RuntimeError | None = None
        arm_motion_allowed = False
        try:
            try:
                self._stop_agv_and_wait()
                self._align_observation_heading()
                self._require_observation_pose()
                arm_motion_allowed = True
                if not self.arm.move_to_safe():
                    first_error = RuntimeError("JAKA无法回到安全姿态")
                else:
                    view = str(self.observations[self.current_station]["arm_view"])
                    if not self.arm.observe(view) and not self._pause_event.is_set():
                        first_error = RuntimeError("JAKA展示观察动作失败")
            except Demo918Paused:
                return
            except RuntimeError as exc:
                first_error = exc
        finally:
            if arm_motion_allowed and not self.arm.move_to_safe():
                if first_error is None:
                    first_error = RuntimeError("JAKA观察后无法回到安全姿态")
                self.log("警告：JAKA观察后回安全姿态失败，请实体急停并人工处理")
            elif not arm_motion_allowed:
                self.log("9.18 AGV停车姿态校正或校验未通过，机械臂未伸出")
        if first_error is not None:
            raise first_error
        self.log(f"9.18 演示观察完成 station={self.current_station}")

    def _align_observation_heading(self) -> None:
        target = self._observation_target()
        demo = self.config.get("demo_918", {})
        position_limit = float(demo.get("station_position_tolerance_m", 0.15))
        tolerance = math.radians(float(demo.get("heading_alignment_tolerance_deg", 3.0)))
        timeout = max(float(demo.get("heading_alignment_timeout_s", 8.0)), 0.5)
        gain = max(float(demo.get("heading_alignment_gain", 2.0)), 0.1)
        maximum = min(max(float(demo.get("heading_alignment_max_rad_s", 0.45)), 0.05), 0.45)
        minimum = min(max(float(demo.get("heading_alignment_min_rad_s", 0.08)), 0.0), maximum)
        deadline = time.monotonic() + timeout
        try:
            while not self._stop_requested() and time.monotonic() < deadline:
                with self._lock:
                    if not self._running or self._pause_event.is_set():
                        raise Demo918Paused("9.18 演示已暂停")
                current = self._fresh_status()
                position_error = math.hypot(float(current["x"]) - target[0], float(current["y"]) - target[1])
                if position_error > position_limit:
                    raise RuntimeError(
                        f"AGV 在观察点 {self.current_station} 对中时位置偏差 {position_error:.3f}m，"
                        f"超过 {position_limit:.3f}m"
                    )
                heading_error = normalize_angle(target[2] - float(current["angle"]))
                if abs(heading_error) <= tolerance:
                    self.motion.stop()
                    self._stop_agv_and_wait()
                    confirmed = self._fresh_status()
                    confirmed_position = math.hypot(
                        float(confirmed["x"]) - target[0], float(confirmed["y"]) - target[1]
                    )
                    confirmed_heading = abs(normalize_angle(target[2] - float(confirmed["angle"])))
                    if confirmed_position > position_limit:
                        raise RuntimeError(
                            f"AGV 在观察点 {self.current_station} 停止后位置偏差 "
                            f"{confirmed_position:.3f}m，超过 {position_limit:.3f}m"
                        )
                    if confirmed_heading <= tolerance:
                        self.log(
                            f"9.18 AGV停车朝向已校正 station={self.current_station} "
                            f"error={math.degrees(confirmed_heading):.1f}deg"
                        )
                        return
                    heading_error = normalize_angle(target[2] - float(confirmed["angle"]))
                angular = max(-maximum, min(maximum, gain * heading_error))
                if 0.0 < abs(angular) < minimum:
                    angular = math.copysign(minimum, angular)
                with self._lock:
                    if not self._running or self._pause_event.is_set():
                        raise Demo918Paused("9.18 演示已暂停")
                    if self._stop_event.is_set():
                        raise RuntimeError("9.18 演示已停止")
                    self.motion.set_velocity(0.0, angular)
                time.sleep(0.05)
        finally:
            self.motion.stop()
        if self._stop_requested():
            raise RuntimeError("9.18 演示已停止")
        raise RuntimeError(
            f"AGV 在观察点 {self.current_station} 朝向校正超时，"
            f"未进入 {math.degrees(tolerance):.1f}deg 容差"
        )

    def _require_observation_pose(self) -> None:
        target = self._observation_target()
        current = self._fresh_status()
        if current.get("is_stop") is not True:
            raise RuntimeError("AGV尚未停稳，禁止伸臂")
        demo = self.config.get("demo_918", {})
        position_tolerance = float(demo.get("station_position_tolerance_m", 0.15))
        heading_tolerance = math.radians(float(demo.get("heading_alignment_tolerance_deg", 3.0)))
        position_error = math.hypot(float(current["x"]) - target[0], float(current["y"]) - target[1])
        heading_error = abs(normalize_angle(float(current["angle"]) - target[2]))
        if position_error > position_tolerance:
            raise RuntimeError(
                f"AGV 距离观察点 {self.current_station} 偏差 {position_error:.3f}m，"
                f"超过 {position_tolerance:.3f}m，禁止伸臂"
            )
        if heading_error > heading_tolerance:
            raise RuntimeError(
                f"AGV 在观察点 {self.current_station} 的朝向偏差 {math.degrees(heading_error):.1f}deg，"
                f"超过 {math.degrees(heading_tolerance):.1f}deg，禁止伸臂"
            )

    def _stop_agv_and_wait(self) -> None:
        self.motion.stop()
        wait_for_status = getattr(self.status, "wait_for_status", None)
        if not callable(wait_for_status):
            return
        timeout = max(float(self.config["safety"].get("station_stop_timeout_s", 2.0)), 0.2)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self._stop_requested():
            if not wait_for_status(
                timeout=min(0.25, max(0.0, deadline - time.monotonic())), max_age=0.8
            ):
                continue
            current = self.status.get_status()
            if current.get("emergency") or current.get("blocked"):
                raise RuntimeError("AGV到站后处于急停或阻挡状态")
            if current.get("fatals") or current.get("errors") or current.get("brake"):
                raise RuntimeError("AGV到站后处于报警或刹车状态")
            if current.get("is_stop") is True:
                return
            time.sleep(0.05)
        if self._stop_requested():
            raise RuntimeError("9.18 演示已停止")
        raise RuntimeError("AGV停止命令未获得 is_stop 确认")

    def _route_loop(self) -> None:
        try:
            while not self._stop_requested():
                selected = choose_918_observations(self.observation_order)
                self.log(f"9.18 本轮随机观察 {len(selected)} 个水稻点: {', '.join(selected)}")
                update_selection = getattr(self.ros, "update_selection", None)
                if callable(update_selection):
                    update_selection(selected)
                segments = build_918_lap_segments(
                    self.anchors,
                    self.route_order,
                    self.observations,
                    selected,
                    self.route_mode,
                    self.max_observation_offset_m,
                )
                index = self._route_attachment_index(self._fresh_status(), segments)
                observed: set[str] = set()
                for _ in range(len(segments)):
                    if self._stop_requested():
                        return
                    if not self._run_segment(segments[index]):
                        return
                    endpoint = segments[index].end_name
                    if endpoint in selected and endpoint not in observed:
                        observed.add(endpoint)
                        self.current_station = endpoint
                        self._observe_current()
                    index = (index + 1) % len(segments)
        except Exception as exc:
            self.log(f"9.18 演示自动停止: {exc}")
            self.stop(f"route failure: {exc}")

    def save_photo(self) -> Path:
        if not self.camera_enabled:
            raise RuntimeError("D435相机已通过 --no-camera 禁用，不能执行 photo")
        image = self.camera() if callable(self.camera) else self.camera.color()
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise RuntimeError("D435没有可保存的彩色画面")
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self._photo_index += 1
        output = self.photo_dir / f"demo918_{time.strftime('%Y%m%d_%H%M%S')}_{self._photo_index:03d}.jpg"
        if not cv2.imwrite(str(output), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"9.18 演示照片写入失败: {output}")
        self.log(f"9.18 演示照片已保存: {output}")
        return output

    def status_text(self) -> str:
        value = self.status.get_status()
        arm = self.arm.client.snapshot() if hasattr(self.arm, "client") else {}
        return (
            f"station={self.current_station or '-'} agv_stop={value.get('is_stop')} "
            f"blocked={value.get('blocked')} emergency={value.get('emergency')} "
            f"status_age={value.get('status_age')} arm_connected={arm.get('connected', '?')} "
            f"running={self._running}"
        )

    def _agv_safe_for_retract(self) -> bool:
        wait_for_status = getattr(self.status, "wait_for_status", None)
        timeout = max(float(self.config["safety"].get("station_stop_timeout_s", 2.0)), 0.2)
        deadline = time.monotonic() + timeout
        while True:
            if callable(wait_for_status) and not wait_for_status(
                timeout=min(0.25, max(0.0, deadline - time.monotonic())), max_age=0.8
            ):
                if time.monotonic() < deadline:
                    continue
                return False
            current = self.status.get_status()
            if (
                current.get("emergency")
                or current.get("blocked")
                or current.get("fatals")
                or current.get("errors")
                or current.get("brake")
            ):
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
            self._stop_event.set()
        try:
            self.motion.stop()
        finally:
            try:
                self.arm.stop()
            finally:
                if not self._agv_safe_for_retract():
                    self.log("警告：AGV未确认安全停稳，9.18 停止后跳过JAKA回撤；请实体急停")
                else:
                    try:
                        if not self.arm.move_to_safe():
                            self.log("警告：9.18 停止后 JAKA 未确认回到 home_safe")
                    except Exception as exc:
                        self.log(f"警告：9.18 停止后 JAKA 回 home_safe 失败: {exc}")
        self.log(f"9.18 演示已停止 reason={reason}")

    def close(self) -> None:
        self.stop("close")
        thread = self._route_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        if self.ros is not None:
            try:
                self.ros.stop()
            except Exception as exc:
                self.log(f"9.18 RViz 发布器停止异常: {exc}")
        for target in (self.arm, self.motion, self.status):
            close = getattr(target, "close", None) or getattr(target, "disconnect", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    self.log(f"9.18 设备连接关闭异常: {exc}")


def console(session: Controller918, input_stream=None, output_stream=None) -> int:
    import sys

    input_stream = input_stream or sys.stdin
    output_stream = output_stream or sys.stdout
    session.start()
    output_stream.write(
        "9.18专家展示已开始（无Tag、不做监测、不运行YOLO）。命令: "
        "pause | start | photo | status | stop | q\n"
    )
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


class SetupPreview918:
    WINDOW_TITLE = "9.18 Demo Setup - D435 RGB"

    def __init__(self, camera: Camera918, startup_timeout_s: float = 4.0):
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
            raise RuntimeError("当前没有图形桌面，无法显示 D435 预览")
        self._stop_event.clear()
        self._ready_event.clear()
        self._thread = threading.Thread(target=self._run, name="demo-918-setup-preview", daemon=True)
        self._thread.start()
        if not self._ready_event.wait(timeout=self.startup_timeout_s):
            error = self.last_error or "等待首帧超时"
            self.stop()
            raise RuntimeError(f"9.18 D435 实时预览启动失败: {error}")

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
                    "9.18 D435 RGB LIVE",
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
            raise RuntimeError(f"9.18 D435 实时预览不可用: {detail}")
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
        answer = input(f"{label}：回车保存当前 D435 画面，或输入已有图片路径，q 中止: ").strip()
        if answer.lower() == "q":
            raise _SetupCancelled("操作员中止 9.18 setup")
        if answer:
            image = cv2.imread(str(Path(answer).expanduser()), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                print(f"无法读取图片: {answer}，请重试")
                continue
            return image
        if camera is None:
            print("当前使用 --no-camera，必须输入已有 JPEG/PNG 路径")
            continue
        image = camera.color()
        if isinstance(image, np.ndarray) and image.size:
            return image
        print("D435 返回空画面，请重试")


def _setup_station_pose(status: AGVStatusClient) -> dict[str, float]:
    if not status.wait_for_status(timeout=1.5, max_age=0.8):
        raise RuntimeError("AGV 定位状态过期")
    value = status.get_status()
    if (
        value.get("emergency")
        or value.get("blocked")
        or value.get("fatals")
        or value.get("errors")
        or value.get("brake")
    ):
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
    before: Mapping[str, float],
    after: Mapping[str, float],
    config: dict[str, Any],
) -> None:
    demo = config.get("demo_918", {})
    position_limit = float(demo.get("setup_position_stability_m", 0.02))
    heading_limit = math.radians(float(demo.get("setup_heading_stability_deg", 2.0)))
    position_error = math.hypot(
        float(after["x"]) - float(before["x"]), float(after["y"]) - float(before["y"])
    )
    heading_error = abs(normalize_angle(float(after["angle"]) - float(before["angle"])))
    if position_error > position_limit or heading_error > heading_limit:
        raise RuntimeError(
            "AGV 在拍照期间发生移动："
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
        if not isinstance(joint, list) or len(joint) != 6:
            raise RuntimeError(f"JAKA 的 {name} 关节角无效")
        joint_values = [float(item) for item in joint]
        if not all(math.isfinite(item) for item in joint_values):
            raise RuntimeError(f"JAKA 的 {name} 关节角无效")
        if not isinstance(tcp, list) or len(tcp) != 6:
            tcp_values = None
        else:
            tcp_values = [float(item) for item in tcp]
            if not all(math.isfinite(item) for item in tcp_values):
                tcp_values = None
        return joint_values, tcp_values
    finally:
        client.disconnect()


def _prompt_integer(label: str, lower: int, upper: int) -> int:
    while True:
        answer = input(label).strip().lower()
        if answer == "q":
            raise _SetupCancelled("操作员中止 9.18 setup")
        try:
            value = int(answer)
        except ValueError:
            print(f"请输入 {lower}-{upper} 的整数，或 q 中止")
            continue
        if lower <= value <= upper:
            return value
        print(f"请输入 {lower}-{upper} 的整数，或 q 中止")


def run_interactive_setup(
    config: dict[str, Any],
    destination: Path,
    runtime_root: Path,
    *,
    map_source: Path | None,
    resume_root: Path | None,
    camera_enabled: bool = True,
    reuse_viewpoints_file: Path | None = None,
) -> int:
    destination = Path(destination).expanduser().resolve()
    runtime_root = Path(runtime_root).expanduser().resolve()
    if resume_root is None:
        if map_source is None:
            print("9.18 setup 失败: 新建 setup 必须通过 --map 指定扫描后的 .smap")
            return 2
        evidence_root = runtime_root / time.strftime("setup_%Y%m%d_%H%M%S")
        setup = Setup918Session(evidence_root, runtime_root)
    else:
        if map_source is not None:
            print("9.18 setup 失败: --resume 使用草稿地图，不能同时传 --map")
            return 2
        evidence_root = Path(resume_root).expanduser().resolve()
        try:
            setup = Setup918Session.resume(evidence_root, runtime_root)
        except (OSError, ValueError) as exc:
            print(f"9.18 setup 恢复失败: {exc}")
            return 1
    camera: Camera918 | None = None
    preview: SetupPreview918 | None = None
    photo_source: Any | None = None
    status: AGVStatusClient | None = None
    arm: JakaClient | None = None
    print(f"9.18 setup 证据目录: {evidence_root}")
    print("此流程只读取 D435、AGV 位姿和 JAKA 关节角，不发送任何运动命令，也不登记 Tag。")
    try:
        if not setup.operator:
            setup.set_operator(input("操作员姓名/编号: "))
        else:
            print(f"沿用操作员: {setup.operator}")
        if not setup.map_record:
            setup.import_map(Path(map_source))
            print(f"新场地地图已固定到本次证据目录: {setup.root / 'map.smap'}")

        if camera_enabled:
            camera_config = config["camera"]
            camera = Camera918(
                str(camera_config["server_url"]), float(camera_config.get("timeout_s", 1.5))
            )
            health = camera.health()
            if not health.get("ok"):
                raise RuntimeError(f"D435 健康检查失败: {health.get('error', 'unknown')}")
            preview = SetupPreview918(camera)
            preview.start()
            photo_source = preview
            print("D435 已连接，9.18 实时预览窗口已打开。")
        else:
            print("D435 已禁用；登记点位时需要输入已有 JPEG/PNG 路径。")

        if not setup.route_mode:
            while True:
                mode = input("路线模式 shuttle(单排往返) / loop(闭环)，q 中止: ").strip().lower()
                if mode == "q":
                    raise _SetupCancelled("操作员中止 9.18 setup")
                if mode in {"shuttle", "loop"}:
                    break
                print("请输入 shuttle、loop 或 q")
            setup.set_route(mode, _prompt_integer("路线锚点数量 [2-99]: ", 2, 99))
        print(
            f"路线配置: mode={setup.route_mode}, "
            f"锚点={len(setup.anchors)}/{setup.anchor_count}"
        )

        missing_anchor = len(setup.anchors) < setup.anchor_count
        missing_observation = setup.observation_count == 0 or len(setup.observations) < setup.observation_count
        if missing_anchor or missing_observation:
            agv = config["agv"]
            status = AGVStatusClient(
                str(agv["ip"]),
                int(agv.get("status_port", 19204)),
                interval_ms=int(agv.get("status_interval_ms", 200)),
                response_timeout_s=float(agv.get("status_response_timeout_s", 0.8)),
            )
            if not status.connect() or not status.wait_for_status(timeout=3.0, max_age=1.0):
                raise RuntimeError("AGV 状态不可用，不能登记 9.18 点位")

        for index in range(len(setup.anchors) + 1, setup.anchor_count + 1):
            name = f"R-{index:02d}"
            while True:
                answer = input(f"{name}：人工将 AGV 驶到安全路线锚点并停稳，回车读取，q 中止: ").strip().lower()
                if answer == "q":
                    raise _SetupCancelled("操作员中止 9.18 setup")
                if answer:
                    print("这里只接受回车读取 AGV 实时位姿，或 q 中止")
                    continue
                try:
                    before = _setup_station_pose(status)
                    photo = _setup_photo(photo_source, f"{name} 路线锚点")
                    after = _setup_station_pose(status)
                    _require_setup_pose_stability(before, after, config)
                    setup.record_anchor(name, after, photo=photo, source="agv_status")
                    print(f"已登记 {name}: x={after['x']:.3f} y={after['y']:.3f} angle={after['angle']:.3f}")
                    break
                except (OSError, RuntimeError, ValueError) as exc:
                    print(f"{name} 登记失败: {exc}，请重试")

        if setup.observation_count == 0:
            setup.set_observation_count(_prompt_integer("水稻观察点数量 [1-99]: ", 1, 99))
        print(f"观察点进度: {len(setup.observations)}/{setup.observation_count}")
        for index in range(len(setup.observations) + 1, setup.observation_count + 1):
            name = f"P-{index:02d}"
            while True:
                answer = input(f"{name}：人工将 AGV 驶到水稻观察位置并停稳，回车读取，q 中止: ").strip().lower()
                if answer == "q":
                    raise _SetupCancelled("操作员中止 9.18 setup")
                if answer:
                    print("这里只接受回车读取 AGV 实时位姿，或 q 中止")
                    continue
                try:
                    before = _setup_station_pose(status)
                    photo = _setup_photo(photo_source, f"{name} 水稻观察点")
                    after = _setup_station_pose(status)
                    _require_setup_pose_stability(before, after, config)
                    while True:
                        arm_view = input(f"{name} 机械臂观察方向 left/right: ").strip().lower()
                        if arm_view in {"left", "right"}:
                            break
                        if arm_view == "q":
                            raise _SetupCancelled("操作员中止 9.18 setup")
                        print("请输入 left、right 或 q")
                    note = input(f"{name} 备注（回车跳过）: ").strip()
                    setup.record_observation(
                        name, after, arm_view=arm_view, photo=photo, note=note
                    )
                    print(
                        f"已登记 {name}: x={after['x']:.3f} y={after['y']:.3f} "
                        f"angle={after['angle']:.3f} arm={arm_view}"
                    )
                    break
                except _SetupCancelled:
                    raise
                except (OSError, RuntimeError, ValueError) as exc:
                    print(f"{name} 登记失败: {exc}，请重试")
        if status is not None:
            status.disconnect()
            status = None

        missing_viewpoints = [name for name in VIEWPOINT_NAMES if name not in setup.viewpoints]
        if missing_viewpoints and reuse_viewpoints_file is not None and Path(reuse_viewpoints_file).is_file():
            try:
                setup.copy_viewpoints(Path(reuse_viewpoints_file))
                missing_viewpoints = []
                print("已将现有 home_safe/left/right 复制到 9.18 独立 setup；运行时不再依赖旧文件。")
            except (OSError, ValueError) as exc:
                print(f"现有机械臂姿态不能复用: {exc}；改为现场读取。")
        if missing_viewpoints:
            jaka = config["jaka"]
            arm = JakaClient(str(jaka["ip"]), int(jaka.get("port", 10001)))
            labels = {
                "home_safe": "安全收回姿态 home_safe",
                "left": "向左观察姿态 left",
                "right": "向右观察姿态 right",
            }
            for name in missing_viewpoints:
                while True:
                    answer = input(f"摆好并确认 {labels[name]} 无碰撞风险后输入 yes，q 中止: ").strip().lower()
                    if answer == "q":
                        raise _SetupCancelled("操作员中止 9.18 setup")
                    if answer != "yes":
                        print("请输入 yes 确认，或 q 中止")
                        continue
                    try:
                        joint, tcp = _setup_arm_pose(arm, name)
                        setup.record_viewpoint(name, joint, tcp)
                        print(f"已登记 {name}")
                        break
                    except (OSError, RuntimeError, ValueError) as exc:
                        print(f"{name} 登记失败: {exc}，请保持姿态并重试")
            arm.disconnect()
            arm = None

        setup.publish(destination)
        print(f"9.18 setup 已发布: {destination}")
        print("下一步运行 ./9.18.sh --dry-run，再运行 ./9.18.sh")
        return 0
    except _SetupCancelled as exc:
        print(str(exc))
        print(f"当前进度已保存: {setup.draft_path}")
        return 2
    except KeyboardInterrupt:
        print(f"\n9.18 setup 已取消；当前进度已保存: {setup.draft_path}")
        return 130
    except EOFError:
        print(f"\n9.18 setup 输入已关闭；当前进度已保存: {setup.draft_path}")
        return 2
    except Exception as exc:
        print(f"\n9.18 setup 失败: {exc}；当前进度保存在 {setup.draft_path}。")
        return 1
    finally:
        if preview is not None:
            preview.stop()
        if status is not None:
            status.disconnect()
        if arm is not None:
            arm.disconnect()


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).parents[2]
    parser = argparse.ArgumentParser(description="9.18 independent Tag-free expert demonstration")
    parser.add_argument("--config", default=str(root / "config" / "9.18.yaml"))
    parser.add_argument("--runtime-root", default=str(root / "runtime" / "9.18"))
    parser.add_argument("--photos", default=str(root / "runtime" / "9.18" / "photos"))
    parser.add_argument("--setup-file", default=str(root / "runtime" / "9.18" / "demo_setup.json"))
    parser.add_argument("--setup", action="store_true", help="交互登记新场地地图、路线和观察点")
    parser.add_argument("--map", dest="map_source", help="新建 setup 使用的扫描 .smap")
    parser.add_argument("--resume", help="从指定 9.18 setup 证据目录继续登记")
    parser.add_argument("--reuse-viewpoints", help="一次性复制已有 home_safe/left/right 的 setup JSON")
    parser.add_argument("--no-rviz", action="store_true")
    parser.add_argument("--no-camera", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.resume and not args.setup:
        parser.error("--resume 只能与 --setup 一起使用")
    if args.map_source and not args.setup:
        parser.error("--map 只能与 --setup 一起使用")
    if args.reuse_viewpoints and not args.setup:
        parser.error("--reuse-viewpoints 只能与 --setup 一起使用")
    if args.setup and args.dry_run:
        parser.error("--setup 与 --dry-run 不能同时使用")
    if args.setup and args.resume and args.map_source:
        parser.error("--resume 与 --map 不能同时使用")
    if args.setup and not args.resume and not args.map_source:
        parser.error("新建 9.18 setup 必须通过 --map 指定扫描后的 .smap")

    config = load_config(args.config)
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    if args.setup:
        return run_interactive_setup(
            config,
            Path(args.setup_file),
            runtime_root,
            map_source=None if args.map_source is None else Path(args.map_source),
            resume_root=None if args.resume is None else Path(args.resume),
            camera_enabled=not args.no_camera,
            reuse_viewpoints_file=(
                None if args.reuse_viewpoints is None else Path(args.reuse_viewpoints)
            ),
        )

    if args.dry_run:
        try:
            session = Controller918(
                config,
                Path(args.photos),
                setup_file=args.setup_file,
                camera_enabled=not args.no_camera,
            )
        except (OSError, ValueError) as exc:
            print(f"9.18 dry-run 失败: {exc}", file=__import__("sys").stderr)
            return 2
        print(
            "dry-run: local config/map/dynamic route/home_safe validated; "
            "no hardware connection, no monitoring, no photos"
        )
        print(f"route_mode={session.route_mode}")
        print(f"observations={len(session.observations)}")
        return 0

    session: Controller918 | None = None
    try:
        session = Controller918(
            config,
            Path(args.photos),
            setup_file=args.setup_file,
            camera_enabled=not args.no_camera,
        )
        session.connect()
        if not args.no_rviz:
            try:
                session.ros = RosPublisher918(config, session.status, session.setup)
            except Exception as exc:
                raise RuntimeError(f"9.18 专家展示无法启动 ROS 展示发布器: {exc}") from exc
        return console(session)
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"9.18 演示启动失败: {exc}", file=__import__("sys").stderr)
        return 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())
