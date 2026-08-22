"""Interactive Wens1 station-order patrol with AGV/JAKA interlocking."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import signal
import threading
import time
from typing import Any

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import String
from visualization_msgs.msg import MarkerArray

from .agv import AGVMotionClient, AGVStatusClient
from .config import load_config, resolve_config_path
from .controller_math import (
    reverse_distance_travelled,
    reverse_motion_allowed,
    route_camera_required,
    slew_rate,
)
from .logging_utils import RunLogger
from .map_utils import load_station_poses, make_occupancy_grid, make_station_markers
from .arm_controller import ArmSweepWorker
from .vision.frame_policy import FrameStaleError, require_current_color_frame
from .vision.storage import VisionRunStore
from .vision.run_store import PatrolRunStore, TargetStore
from .vision.targeting import side_from_bbox
from .patrol_target_runtime import PatrolTargetRuntime, RuntimeConfig, TargetEvent
from .target_task import TargetTask, TaskObservation
from .near_capture import accept_near_frame, choose_best_frame, near_burst_action

from .control.route_math import (
    Segment,
    compute_segment_velocity,
    endpoint_approach_speed,
    endpoint_reached,
    make_segments,
)


IDLE = "IDLE"
STOPPED = "STOPPED"
ERROR = "ERROR"
ROUTE_MOVE = "ROUTE_MOVE"
END_PAUSE = "END_PAUSE"
BLOCKED = "BLOCKED"
CAMERA_WAIT = "CAMERA_WAIT"
TEST_FORWARD = "TEST_FORWARD"
TEST_BACKWARD = "TEST_BACKWARD"
ARM_TEST = "ARM_TEST"
ARM_HOME = "ARM_HOME"
ARM_FIXED = "ARM_FIXED"
TARGET_ALIGN_REVERSE = "TARGET_ALIGN_REVERSE"
TARGET_RELOCALIZE = "TARGET_RELOCALIZE"
TARGET_FIXED = "TARGET_FIXED"
TARGET_RECOVER = "TARGET_RECOVER"

MOTION_STATES = {ROUTE_MOVE, TEST_FORWARD, TEST_BACKWARD}
ROUTE_STATES = {ROUTE_MOVE, END_PAUSE}
TARGET_STATES = {TARGET_ALIGN_REVERSE, TARGET_RELOCALIZE, TARGET_FIXED, TARGET_RECOVER}


class WenshiPatrolNode(Node):
    def __init__(self, config: dict[str, Any]):
        super().__init__("wenshi_patrol_manager")
        self.config = config
        self.route_config = config["route"]
        self.control = config["control"]
        self.safety = config["safety"]
        self.camera_config = config.get("camera", {})
        self.topics = config["topics"]
        self.vision = config.get("vision", {})
        self._lock = threading.RLock()
        self._image_lock = threading.Lock()
        self._cleanup_done = False
        self._exit_requested = threading.Event()
        self.state = IDLE
        self.state_detail = "等待 start"
        self._resume_state: str | None = None
        self._blocked_clear_since: float | None = None
        self._camera_last_ok = 0.0
        self._camera_status = "waiting"
        self._camera_resume_state: str | None = None
        self._camera_lost_since: float | None = None
        self._camera_recovered_since: float | None = None
        self._last_vx = 0.0
        self._last_w = 0.0
        self._last_velocity_update = time.monotonic()
        self._last_log_sample = 0.0
        self._last_static_publish = 0.0
        self._pause_until = 0.0
        self._test_start: tuple[float, float] | None = None
        self._test_distance = 0.0
        self._latest_color: dict[str, Any] | None = None
        self._latest_depth: dict[str, Any] | None = None
        self._last_image_error_log = 0.0
        self._detector: Any | None = None
        self._detector_checked = False
        self._detector_message = ""
        target_config = config.get("patrol_target", {})
        self.target_enabled = bool(target_config.get("enabled", False))
        self._target_runtime = PatrolTargetRuntime(RuntimeConfig(
            stability_window=int(self.vision.get("stability_window", 5)),
            stability_min_hits=int(self.vision.get("stability_min_hits", 3)),
            station_safety_band_m=float(self.vision.get("station_safety_band_m", 0.50)),
            dedupe_ttl_s=float(self.vision.get("dedupe_ttl_s", 7200.0)),
            neighbor_suppression_radius_m=float(self.vision.get("neighbor_suppression_radius_m", 0.30)),
        ))
        self._target_task: TargetTask | None = None
        self._target_store: TargetStore | None = None
        self._target_event: TargetEvent | None = None
        self._target_locked_detection: Any | None = None
        self._target_frames: list[tuple[np.ndarray, Any, Any]] = []
        self._target_near_saved = False
        self._target_near_rounds = 0
        self._target_last_frame_stamp_s: float | None = None
        self._target_reverse_start_along: float | None = None
        self._target_loop_id = 0
        self._target_recovery_started = False

        logs_root = resolve_config_path(config, str(config["logging"]["root_dir"]))
        self.run_log = RunLogger(logs_root)
        self.run_log.event("program_started", config=str(config["_config_path"]))
        self.vision_store = VisionRunStore(self.run_log.run_dir)
        self.vision_data_dir = self.vision_store.data_dir
        self.vision_image_dir = self.vision_store.image_dir
        self.vision_log_path = self.vision_store.record_path
        self.patrol_run_store = PatrolRunStore(self.run_log.run_dir)

        agv = config["agv"]
        self.agv_status = AGVStatusClient(
            ip=str(agv["ip"]),
            port=int(agv.get("status_port", 19204)),
            interval_ms=int(agv.get("status_interval_ms", 200)),
            response_timeout_s=float(agv.get("status_response_timeout_s", 0.8)),
            log=self._hardware_log,
        )
        self.agv_motion = AGVMotionClient(
            ip=str(agv["ip"]),
            port=int(agv.get("motion_port", 19205)),
            send_rate_hz=float(self.control.get("rate_hz", 20.0)),
            watchdog_s=float(self.safety.get("command_watchdog_s", 0.3)),
            log=self._hardware_log,
        )
        self.arm = ArmSweepWorker(config, self._hardware_log)

        map_path = resolve_config_path(config, str(config["map"]["smap_file"]))
        self.map_path = map_path
        self.stations = load_station_poses(map_path)
        self.station_order = [str(name) for name in self.route_config["station_order"]]
        self.default_loop = bool(self.route_config.get("loop", False))
        self._active_segments = make_segments(
            self.stations,
            self.station_order,
            loop=self.default_loop,
        )
        self._route_loop = self.default_loop
        self._route_index = 0

        transient = QoSProfile(depth=1)
        transient.durability = DurabilityPolicy.TRANSIENT_LOCAL
        transient.reliability = ReliabilityPolicy.RELIABLE
        self.map_pub = self.create_publisher(OccupancyGrid, self.topics["map"], transient)
        self.marker_pub = self.create_publisher(MarkerArray, self.topics["markers"], transient)
        self.pose_pub = self.create_publisher(PoseStamped, self.topics["agv_pose"], 10)
        self.state_pub = self.create_publisher(String, self.topics["state"], transient)
        self.create_subscription(String, self.topics["camera_status"], self._camera_callback, 10)
        self.create_subscription(Image, self.topics["color"], self._color_callback, 5)
        self.create_subscription(Image, self.topics["depth"], self._depth_callback, 5)

        stamp = self.get_clock().now().to_msg()
        self._map_message = make_occupancy_grid(map_path, stamp)
        self._marker_message = make_station_markers(map_path, stamp)
        self._publish_static(force=True)
        self._publish_state()

        rate = max(float(self.control.get("rate_hz", 20.0)), 2.0)
        self.create_timer(1.0 / rate, self._control_tick)
        self.run_log.event(
            "route_loaded",
            station_order=self.station_order,
            loop=self.default_loop,
            segments=[
                f"{segment.start_name}->{segment.end_name}"
                for segment in self._active_segments
            ],
        )
        if not bool(self.safety.get("rear_radar_verified", False)):
            warning = (
                "车尾雷达未验证；本路线正常行驶使用前进姿态，"
                "但 test back 仍依赖用户现场看护"
            )
            self.get_logger().warning(warning)
            self.run_log.event("rear_radar_warning", message=warning)

    def _hardware_log(self, message: str):
        self.get_logger().info(message)
        self.run_log.info(message)

    def _camera_callback(self, message: String):
        self._camera_status = message.data
        if message.data.startswith("ok:"):
            self._camera_last_ok = time.monotonic()

    @staticmethod
    def _image_message_to_array(message: Image) -> np.ndarray:
        encoding = str(message.encoding).strip().lower()
        if encoding in {"bgr8", "rgb8"}:
            dtype = np.uint8
            channels = 3
        elif encoding in {"mono8", "8uc1"}:
            dtype = np.uint8
            channels = 1
        elif encoding in {"16uc1", "mono16"}:
            dtype = np.uint16
            channels = 1
        else:
            raise ValueError(f"不支持的图像编码: {message.encoding}")

        height = int(message.height)
        width = int(message.width)
        itemsize = np.dtype(dtype).itemsize
        row_items = int(message.step // itemsize) if message.step else width * channels
        expected_items = row_items * height
        try:
            flat = np.frombuffer(message.data, dtype=dtype)
        except TypeError:
            flat = np.asarray(message.data, dtype=np.uint8).view(dtype)
        if flat.size < expected_items:
            raise ValueError(
                f"图像数据长度不足: got={flat.size} expected={expected_items}"
            )
        flat = flat[:expected_items]

        if channels == 1:
            image = flat.reshape((height, row_items))[:, :width]
        else:
            image = flat.reshape((height, row_items))[:, : width * channels]
            image = image.reshape((height, width, channels))
        if message.is_bigendian and image.dtype.itemsize > 1:
            image = image.byteswap()
        image = image.copy()
        if encoding == "rgb8":
            image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        return image

    @staticmethod
    def _stamp_seconds(message: Image) -> float:
        return float(message.header.stamp.sec) + float(message.header.stamp.nanosec) * 1e-9

    def _warn_image_parse(self, label: str, exc: Exception):
        now = time.monotonic()
        if now - self._last_image_error_log >= 2.0:
            self.get_logger().warning(f"{label}图像解析失败: {exc}")
            self._last_image_error_log = now

    def _color_callback(self, message: Image):
        try:
            image = self._image_message_to_array(message)
            if image.ndim == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            frame = {
                "image": image,
                "encoding": str(message.encoding),
                "frame_id": str(message.header.frame_id),
                "stamp_s": self._stamp_seconds(message),
                "received_s": time.monotonic(),
            }
            with self._image_lock:
                self._latest_color = frame
        except Exception as exc:
            self._warn_image_parse("彩色", exc)

    def _depth_callback(self, message: Image):
        try:
            image = self._image_message_to_array(message)
            frame = {
                "image": image,
                "encoding": str(message.encoding),
                "frame_id": str(message.header.frame_id),
                "stamp_s": self._stamp_seconds(message),
                "received_s": time.monotonic(),
            }
            with self._image_lock:
                self._latest_depth = frame
        except Exception as exc:
            self._warn_image_parse("深度", exc)

    def _camera_is_fresh(self) -> bool:
        if not self._camera_last_ok:
            return False
        return time.monotonic() - self._camera_last_ok <= float(
            self.safety.get("camera_timeout_s", 2.0)
        )

    def _resolve_vision_model_path(self) -> str:
        configured = str(self.vision.get("model_path", "") or "").strip()
        if not configured:
            return ""
        path = Path(configured).expanduser()
        if path.is_absolute():
            return str(path.resolve())

        config_path = resolve_config_path(self.config, configured)
        if config_path.exists():
            return str(config_path)
        return str(config_path)

    @staticmethod
    def _copy_frame(frame: dict[str, Any] | None) -> dict[str, Any] | None:
        if frame is None:
            return None
        copied = dict(frame)
        copied["image"] = frame["image"].copy()
        return copied

    def _latest_images(self) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        with self._image_lock:
            return self._copy_frame(self._latest_color), self._copy_frame(self._latest_depth)

    def _latest_frame_age(self, frame: dict[str, Any] | None) -> float | None:
        if frame is None:
            return None
        return time.monotonic() - float(frame["received_s"])

    def _load_detector(self) -> tuple[Any | None, str]:
        if self._detector_checked:
            return self._detector, self._detector_message
        self._detector_checked = True

        model_path = self._resolve_vision_model_path()
        if not model_path:
            self._detector_message = (
                f"识别模型未配置；请在 {self.config['_config_path']} 设置 vision.model_path"
            )
            return None, self._detector_message
        if not Path(model_path).exists():
            self._detector_message = f"识别模型文件不存在: {model_path}"
            return None, self._detector_message

        try:
            from .vision.detector import RiceMarkerDetector
        except Exception as exc:
            self._detector_message = f"识别模块不可用: {exc}"
            return None, self._detector_message

        target_names = self.vision.get("target_class_names", [])
        if not isinstance(target_names, list):
            target_names = []
        try:
            detector = RiceMarkerDetector(
                model_path=model_path,
                conf_threshold=float(self.vision.get("conf_threshold", 0.35)),
                target_class_names=[str(name) for name in target_names],
                allow_missing_model=False,
            )
            if not detector.load_model():
                self._detector_message = f"识别模型加载失败: {model_path}"
                return None, self._detector_message
        except Exception as exc:
            self._detector_message = f"识别模型初始化失败: {exc}"
            return None, self._detector_message

        self._detector = detector
        self._detector_message = f"识别模型: {model_path}"
        return self._detector, self._detector_message

    def _append_vision_record(self, record: dict[str, Any]):
        self.vision_store.ensure_directories()
        with self.vision_log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def collect_image(self, run_detection: bool = False) -> tuple[bool, str]:
        if run_detection and not bool(self.vision.get("enabled", False)):
            return False, "视觉识别未启用；请先完成模型、标定和现场验证"
        color, depth = self._latest_images()
        if color is None:
            return False, f"D435 暂无彩色图像: {self._camera_status}"
        color_age = self._latest_frame_age(color)
        try:
            require_current_color_frame(
                color_age,
                float(self.camera_config.get("frame_max_age_s", 1.0)),
            )
        except FrameStaleError as exc:
            return False, str(exc)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        action = "detect" if run_detection else "collect"
        color_path, suggested_depth_path, suggested_annotated_path = self.vision_store.image_paths(
            timestamp, action
        )
        if not cv2.imwrite(str(color_path), color["image"]):
            return False, f"彩色图片保存失败: {color_path}"

        depth_path: Path | None = None
        depth_note = ""
        depth_max_lag = float(self.vision.get("depth_max_lag_s", 1.0))
        if depth is not None:
            lag = abs(float(depth["received_s"]) - float(color["received_s"]))
            if lag <= depth_max_lag:
                depth_path = suggested_depth_path
                if not cv2.imwrite(str(depth_path), depth["image"]):
                    depth_note = f"深度图保存失败: {depth_path}"
                    depth_path = None
            else:
                depth_note = f"深度图不同步: lag={lag:.2f}s"
        else:
            depth_note = "暂无深度图"

        detections: list[dict[str, Any]] = []
        annotated_path: Path | None = None
        detector_message = ""
        detector_error = ""
        if run_detection:
            detector, detector_message = self._load_detector()
            annotated = color["image"].copy()
            if detector is not None:
                try:
                    found = detector.detect(color["image"].copy())
                    selected = found[0] if found else None
                    detector.draw(annotated, found, selected)
                    detections = [item.to_dict() for item in found]
                except Exception as exc:
                    detector_error = f"识别执行失败: {exc}"
                    self.get_logger().warning(detector_error)
            else:
                detector_error = detector_message
            annotated_path = suggested_annotated_path
            if not cv2.imwrite(str(annotated_path), annotated):
                return False, f"识别结果图保存失败: {annotated_path}"

        agv_status = self.agv_status.get_status()
        arm = self.arm.snapshot()
        record = {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "command": action,
            "camera_status": self._camera_status,
            "color_frame": {
                "encoding": color["encoding"],
                "frame_id": color["frame_id"],
                "stamp_s": color["stamp_s"],
                "age_s": self._latest_frame_age(color),
            },
            "depth_frame": {
                "encoding": depth["encoding"] if depth else "",
                "frame_id": depth["frame_id"] if depth else "",
                "stamp_s": depth["stamp_s"] if depth else None,
                "age_s": self._latest_frame_age(depth),
                "note": depth_note,
            },
            "images": {
                "color": str(color_path),
                "depth": str(depth_path) if depth_path else "",
                "annotated": str(annotated_path) if annotated_path else "",
            },
            "detections": detections,
            "detector": {
                "model_path": self._resolve_vision_model_path(),
                "message": detector_message,
                "error": detector_error,
            },
            "agv": {
                "x": agv_status.get("x"),
                "y": agv_status.get("y"),
                "angle": agv_status.get("angle"),
                "status_age": agv_status.get("status_age"),
            },
            "jaka": {
                "connected": arm["connected"],
                "state": arm["state"],
                "joint": arm["joint"],
                "error": arm["error"],
            },
        }
        self._append_vision_record(record)
        self.run_log.event(
            "vision_capture",
            command=action,
            color=str(color_path),
            depth=str(depth_path) if depth_path else "",
            annotated=str(annotated_path) if annotated_path else "",
            detections=len(detections),
            detector_error=detector_error,
        )

        age_text = f" age={color_age:.2f}s" if color_age is not None else ""
        if run_detection:
            if detector_error:
                result = f"采集完成，识别未执行: {detector_error}"
            elif detections:
                best = detections[0]
                result = (
                    f"识别完成: 目标={len(detections)} "
                    f"最高置信度={best['confidence']:.2f} 类别={best['class_name']}"
                )
            else:
                result = "识别完成: 目标=0"
            if depth_note:
                result += f"；{depth_note}"
            return (
                True,
                f"{result}；图片={annotated_path}；原图={color_path}；记录={self.vision_log_path}{age_text}",
            )

        result = f"采集完成: 彩色={color_path}"
        result += f"；深度={depth_path}" if depth_path else f"；{depth_note}"
        result += f"；记录={self.vision_log_path}{age_text}"
        return True, result

    def _transition(self, state: str, detail: str = ""):
        with self._lock:
            previous = self.state
            self.state = state
            self.state_detail = detail
        self._publish_state()
        self.run_log.event("state", previous=previous, state=state, detail=detail)
        self.get_logger().info(f"状态 {previous} -> {state}: {detail}")

    def _publish_state(self):
        message = String()
        message.data = f"{self.state}:{self.state_detail}"
        self.state_pub.publish(message)

    def _publish_static(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_static_publish < 2.0:
            return
        stamp = self.get_clock().now().to_msg()
        self._map_message.header.stamp = stamp
        self._marker_message = make_station_markers(self.map_path, stamp)
        self.map_pub.publish(self._map_message)
        self.marker_pub.publish(self._marker_message)
        self._last_static_publish = now

    def _publish_pose(self, status: dict[str, Any]):
        if status.get("x") is None or status.get("y") is None:
            return
        yaw = float(status.get("angle") or 0.0)
        message = PoseStamped()
        message.header.stamp = self.get_clock().now().to_msg()
        message.header.frame_id = "map"
        message.pose.position.x = float(status["x"])
        message.pose.position.y = float(status["y"])
        message.pose.orientation.z = math.sin(yaw * 0.5)
        message.pose.orientation.w = math.cos(yaw * 0.5)
        self.pose_pub.publish(message)

    def _ensure_agv_status(self) -> tuple[bool, str]:
        timeout = float(self.config["agv"].get("connect_timeout_s", 3.0))
        if not self.agv_status.connected:
            if not self.agv_status.connect(timeout=timeout):
                return False, "AGV 状态端口连接失败"
        max_age = float(self.safety.get("status_timeout_s", 0.6))
        if not self.agv_status.wait_for_status(timeout=timeout, max_age=max_age):
            response = self.agv_status.get_status().get("last_response")
            return False, f"AGV 没有返回有效定位状态，最后响应={response}"
        return True, "ok"

    def _ensure_agv(self) -> tuple[bool, str]:
        ok, message = self._ensure_agv_status()
        if not ok:
            return ok, message
        timeout = float(self.config["agv"].get("connect_timeout_s", 3.0))
        if not self.agv_motion.connected and not self.agv_motion.connect(timeout=timeout):
            return False, f"AGV 19205 连接失败: {self.agv_motion.last_error}"
        self.agv_motion.last_error = ""
        return True, "ok"

    def _agv_precheck(self, require_camera: bool) -> tuple[bool, str]:
        ok, message = self._ensure_agv()
        if not ok:
            return ok, message
        status = self.agv_status.get_status()
        age = status.get("status_age")
        if age is None or age > float(self.safety.get("status_timeout_s", 0.6)):
            return False, f"AGV 状态过期: {age}"
        if status.get("x") is None or status.get("y") is None or status.get("angle") is None:
            return False, "AGV 定位坐标不完整"
        if status.get("emergency"):
            return False, "AGV 急停已按下"
        if status.get("fatals") or status.get("errors"):
            return False, f"AGV 报警: {status.get('fatals')} {status.get('errors')}"
        if status.get("charging"):
            return False, "AGV 正在充电"
        if status.get("blocked"):
            return False, "AGV 当前被阻挡"
        if require_camera:
            wait_s = float(self.config["camera"].get("startup_wait_s", 3.0))
            deadline = time.monotonic() + max(0.0, wait_s)
            while time.monotonic() < deadline:
                if self._camera_is_fresh():
                    break
                time.sleep(0.05)
            if not self._camera_is_fresh():
                return False, f"D435 未就绪: {self._camera_status}"
        return True, "ok"

    def _runtime_safety(self, status: dict[str, Any], require_arm: bool) -> str | None:
        age = status.get("status_age")
        if age is None or age > float(self.safety["status_timeout_s"]):
            return f"AGV 状态超时: {age}"
        if status.get("emergency"):
            return "AGV 急停"
        if status.get("fatals") or status.get("errors"):
            return f"AGV 报警: {status.get('fatals')} {status.get('errors')}"
        if status.get("x") is None or status.get("y") is None or status.get("angle") is None:
            return "AGV 定位丢失"
        if not self.agv_motion.connected:
            return "AGV 19205 运动连接中断"
        if self.agv_motion.last_error:
            return f"19205 错误: {self.agv_motion.last_error}"
        if require_arm:
            arm = self.arm.snapshot()
            if arm["state"] == "ERROR":
                return f"机械臂错误: {arm['error']}"
        return None

    def _camera_required_for_state(self, state: str) -> bool:
        return route_camera_required(self.camera_config, self.target_enabled) and state in {
            ROUTE_MOVE,
            END_PAUSE,
        }

    def _current_segment(self) -> Segment:
        return self._active_segments[self._route_index]

    def _route_label(self) -> str:
        if not self._active_segments:
            return "-"
        segment = self._current_segment()
        return f"{segment.start_name}->{segment.end_name}"

    def _distance_to_station(self, status: dict[str, Any], station_name: str) -> float:
        x, y, _ = self.stations[station_name]
        return math.hypot(float(status["x"]) - x, float(status["y"]) - y)

    def start_route(self, loop: bool = False) -> tuple[bool, str]:
        with self._lock:
            if self.state not in {IDLE, STOPPED, ERROR}:
                return False, f"当前状态不能 start: {self.state}"
            ok, message = self._agv_precheck(
                require_camera=route_camera_required(self.camera_config, self.target_enabled)
            )
            if not ok:
                self._transition(ERROR, message)
                return False, message
            ok, message = self.arm.quick_start_check()
            if not ok:
                self._transition(ERROR, message)
                return False, message
            if self.target_enabled:
                model_path = self._resolve_vision_model_path()
                if not bool(self.vision.get("enabled", False)):
                    return False, "patrol_target 已启用，但 vision.enabled 未启用"
                if not model_path or not Path(model_path).exists():
                    return False, "patrol_target 已启用，但 rice 模型不存在"
                if not bool(self.config.get("fixed_approach", {}).get("enabled", False)):
                    return False, "patrol_target 已启用，但 fixed_approach.enabled 未启用"
                if not reverse_motion_allowed(self.safety):
                    return False, "patrol_target 已启用，但目标对齐倒车安全锁未放行"

            status = self.agv_status.get_status()
            first = self.station_order[0]
            start_error = self._distance_to_station(status, first)
            start_limit = float(self.route_config.get("start_station_tolerance_m", 0.35))
            if start_error > start_limit:
                message = (
                    f"当前位置离 {first} 为 {start_error:.3f}m，"
                    f"超过启动限制 {start_limit:.3f}m；请先把底盘放到 {first}"
                )
                self.agv_motion.stop()
                self._transition(ERROR, message)
                return False, message

            self.patrol_run_store.reopen()
            self._target_runtime.reset_target()
            self._target_loop_id = 0
            self._route_loop = bool(loop or self.default_loop)
            self._active_segments = make_segments(
                self.stations,
                self.station_order,
                loop=self._route_loop,
            )
            self._route_index = 0
            self._last_vx = self._last_w = 0.0
            self._last_velocity_update = time.monotonic()
            ok, message = self.arm.start()
            if not ok:
                self._transition(ERROR, message)
                return False, message
            self._transition(ROUTE_MOVE, f"开始路线 {self._route_label()}")
            return True, "wens1 路线已启动: " + " -> ".join(self.station_order)

    def start_test(self, direction: int, distance: float) -> tuple[bool, str]:
        with self._lock:
            if self.state not in {IDLE, STOPPED, ERROR}:
                return False, f"当前状态不能测试: {self.state}"
            if direction < 0 and not reverse_motion_allowed(self.safety):
                self.agv_motion.stop()
                self._transition(ERROR, "车尾雷达未验证，禁止 test back")
                return False, "车尾雷达未验证，禁止 test back"
            maximum = float(self.control.get("test_max_distance_m", 0.5))
            if distance <= 0 or distance > maximum:
                return False, f"测试距离必须在 0 至 {maximum:.2f}m 之间"
            ok, message = self._agv_precheck(require_camera=False)
            if not ok:
                self._transition(ERROR, message)
                return False, message
            status = self.agv_status.get_status()
            self._test_start = (float(status["x"]), float(status["y"]))
            self._test_distance = float(distance)
            self._last_vx = self._last_w = 0.0
            self._last_velocity_update = time.monotonic()
            state = TEST_FORWARD if direction > 0 else TEST_BACKWARD
            self._transition(state, f"低速测试 {distance:.2f}m")
            return True, "短距离测试已开始"

    def start_arm_test(self) -> tuple[bool, str]:
        with self._lock:
            if self.state not in {IDLE, STOPPED, ERROR}:
                return False, f"当前状态不能测试机械臂: {self.state}"
            ok, message = self.arm.quick_start_check()
            if not ok:
                self._transition(ERROR, message)
                return False, message
            ok, message = self.arm.start()
            if not ok:
                self._transition(ERROR, message)
                return False, message
            self._transition(ARM_TEST, "J5 从当前位置巡视，底盘保持停止")
            return True, "机械臂独立测试已启动；输入 stop 停止"

    def start_fixed_approach(self, side: str) -> tuple[bool, str]:
        with self._lock:
            if self.state not in {IDLE, STOPPED, ERROR}:
                return False, f"当前状态不能执行固定抵近: {self.state}"
            if not bool(self.config.get("fixed_approach", {}).get("enabled", False)):
                return False, "固定示教抵近未启用；请完成现场复核后再开启"
            ok, message = self._agv_precheck(require_camera=False)
            if not ok:
                self._transition(ERROR, message)
                return False, message
            self.agv_motion.stop()
            self._last_vx = self._last_w = 0.0
            ok, message = self.arm.connect_and_check()
            if not ok:
                self._transition(ERROR, message)
                return False, message
            ok, message = self.arm.start_fixed_sequence(side)
            if not ok:
                self._transition(ERROR, message)
                return False, message
            self._transition(ARM_FIXED, f"底盘保持停止；固定{side}侧示教抵近执行中")
            self.run_log.event("fixed_approach_started", side=side)
            return True, f"固定{side}侧示教抵近已启动；底盘保持停止"

    def goto_home(self) -> tuple[bool, str]:
        with self._lock:
            if self.state not in {IDLE, STOPPED, ERROR}:
                return False, f"当前状态不能 goto home: {self.state}"
            ok, message = self._agv_precheck(require_camera=False)
            if not ok:
                self._transition(ERROR, message)
                return False, message
            self.agv_motion.stop()
            self._last_vx = self._last_w = 0.0
            ok, message = self.arm.connect_and_check()
            if not ok:
                return False, message
            ok, message = self.arm.start_home()
            if not ok:
                return False, message
            self._transition(ARM_HOME, f"底盘保持停止；{message}")
            self.run_log.event("goto_home_started", plan=message)
            return True, "机械臂正在安全回到初始 camera 姿态；底盘保持停止"

    def stop_all(self, detail: str = "用户 stop"):
        with self._lock:
            self.agv_motion.stop()
            self._last_vx = self._last_w = 0.0
            self.arm.stop()
            self._abort_target(detail)
            self.patrol_run_store.finish("stopped")
            self._transition(STOPPED, detail)

    def _abort_target(self, reason: str) -> None:
        if self._target_task is not None:
            self._target_task.stop(reason)
        self.arm.stop_target_follow()
        self._target_task = None
        self._target_store = None
        self._target_event = None
        self._target_locked_detection = None
        self._target_frames = []
        self._target_near_saved = False
        self._target_near_rounds = 0
        self._target_last_frame_stamp_s = None
        self._target_reverse_start_along = None
        self._target_recovery_started = False
        self._target_runtime.reset_target()

    def _detector_detections(self, image: np.ndarray) -> list[Any]:
        detector, _message = self._load_detector()
        if detector is None:
            return []
        try:
            return detector.detect(image.copy())
        except Exception as exc:
            self.run_log.event("detector_error", error=str(exc))
            return []

    def _maybe_start_target(self, status: dict[str, Any], progress) -> bool:
        if not self.target_enabled or self.state != ROUTE_MOVE:
            return False
        reset_marker = self.run_log.run_dir / "dedupe_reset.json"
        if self._target_runtime.apply_reset_marker(reset_marker):
            self.run_log.event("dedupe_reset_applied", marker=str(reset_marker))
        color, depth = self._latest_images()
        if color is None or not self._camera_is_fresh():
            return False
        detections = self._detector_detections(color["image"])
        event = self._target_runtime.observe(
            color["image"], detections, int(color["image"].shape[1]), int(color["image"].shape[0]),
            self._route_label(), progress.along_track, self._target_loop_id, time.monotonic(), progress.length,
        )
        if event is None:
            return False
        depth_summary = None
        if depth is not None:
            from .vision.targeting import robust_bbox_depth
            depth_summary = robust_bbox_depth(
                depth["image"],
                event.detection,
                source_size=(color["image"].shape[1], color["image"].shape[0]),
            )
        from .vision.quality import score_frame
        far_quality = score_frame(color["image"], event.detection, depth_summary, expected_upper_body=True)
        if depth_summary is None or not depth_summary.valid:
            self._target_runtime.reject_current()
            self.run_log.event(
                "target_far_rejected",
                route_segment=event.route_segment,
                side=event.side,
                reasons=["目标框内没有有效深度"],
            )
            return False
        if not far_quality.ok:
            self._target_runtime.reject_current()
            self.run_log.event(
                "target_far_rejected",
                route_segment=event.route_segment,
                side=event.side,
                reasons=far_quality.reasons,
                quality=far_quality.score,
            )
            return False
        target = self.patrol_run_store.create_target()
        target.save_far(color["image"], {
            "route_segment": event.route_segment,
            "side": event.side,
            "along_track_m": event.along_track_m,
            "bbox": event.detection.to_dict(),
            "depth": depth_summary.__dict__ if depth_summary else None,
            "quality": far_quality.__dict__,
        }, int(self.vision.get("far_jpeg_quality", 95)))
        self._target_store = target
        self._target_event = event
        self._target_locked_detection = event.detection
        self._target_task = TargetTask(
            event.side,
            float(self.vision.get("target_reverse_speed_mps", 0.05)),
            float(self.vision.get("target_reverse_limit_m", 0.60)),
            float(self.safety.get("camera_timeout_s", 2.0)),
            reverse_permitted=reverse_motion_allowed(self.safety),
        )
        self._target_reverse_start_along = progress.along_track
        self._target_frames = []
        self._target_near_saved = False
        self._target_last_frame_stamp_s = None
        self.agv_motion.stop()
        self._last_vx = self._last_w = 0.0
        self.arm.stop()
        ok, message = self.arm.start_target_follow(int(color["image"].shape[1]))
        if not ok:
            self._abort_target(f"J5跟随启动失败: {message}")
            self._transition(ROUTE_MOVE, "目标任务放弃，继续路线")
            return False
        self.run_log.event("target_far_captured", target_id=target.target_id, side=event.side, route_segment=event.route_segment)
        self._transition(TARGET_ALIGN_REVERSE, f"目标 {target.target_id} {event.side} 侧：受控后退对位（车尾雷达未验证，请人工看护）")
        return True

    def _begin_target_recovery(self, message: str, fixed_side: str | None = None) -> None:
        """Return through a known arm path before resuming the original route."""
        self.agv_motion.stop()
        self._last_vx = self._last_w = 0.0
        self.arm.stop()
        if fixed_side in {"left", "right"}:
            ok, detail = self.arm.start_retract(fixed_side)
        else:
            ok, detail = self.arm.start_centering()
        if not ok:
            self._fail(f"目标任务回巡视失败: {detail}; 原因: {message}")
            return
        if self._target_store is not None:
            self._target_store.write_metadata({"status": "failed", "failure_reason": message})
        self._target_recovery_started = True
        self._transition(TARGET_RECOVER, f"目标任务失败，回巡视: {message}")

    def _locked_detection(self, image: np.ndarray) -> Any | None:
        detections = [item for item in self._detector_detections(image) if item.class_name.lower() == "rice"]
        if not detections or self._target_event is None:
            return None
        from .vision.targeting import match_locked_detection
        previous = self._target_locked_detection or self._target_event.detection
        matched = match_locked_detection(detections, previous)
        if matched is not None:
            self._target_locked_detection = matched
        return matched

    def _run_target_align(self, status: dict[str, Any]):
        if self._target_task is None or self._target_event is None:
            self._fail("目标任务状态缺少上下文")
            return
        failure = self._runtime_safety(status, require_arm=True)
        if failure:
            self._fail(f"目标抵近安全失败: {failure}")
            return
        color, depth = self._latest_images()
        detection = self._locked_detection(color["image"]) if color is not None else None
        if detection is None:
            self._begin_target_recovery("目标跟随失败: target_lost")
            return
        self.arm.update_target_follow(detection, int(color["image"].shape[1]))
        from .control.route_math import segment_progress
        progress = segment_progress(status, self._current_segment(), cross_track_gain=0.0)
        start = (
            float(progress.along_track)
            if self._target_reverse_start_along is None
            else float(self._target_reverse_start_along)
        )
        moved = reverse_distance_travelled(start, progress.along_track)
        distance_remaining = float(self.vision.get("target_reverse_limit_m", 0.60)) - moved
        from .vision.targeting import depth_valid_for_detection
        command = self._target_task.tick(TaskObservation(
            camera_age_s=self._latest_frame_age(color), target_visible=True,
            distance_remaining_m=distance_remaining, j5_speed_deg_s=0.0,
            depth_valid=bool(
                depth is not None
                and depth_valid_for_detection(
                    depth["image"],
                    detection,
                    source_size=(color["image"].shape[1], color["image"].shape[0]),
                )
            ),
            agv_blocked=bool(status.get("blocked")), emergency=bool(status.get("emergency")),
        ))
        if command.stop:
            self.agv_motion.stop()
            self._last_vx = self._last_w = 0.0
            if command.state == "RELOCALIZE":
                self.arm.stop_target_follow()
                self._transition(TARGET_RELOCALIZE, "后退对位完成，重新确认目标")
            else:
                self._fail(f"目标抵近停止: {command.reason}")
            return
        self.agv_motion.set_velocity(command.reverse_velocity_mps, 0.0)
        self._last_vx, self._last_w = command.reverse_velocity_mps, 0.0
        self.state_detail = f"目标后退对位 moved={moved:.3f}m remaining={distance_remaining:.3f}m J5跟随"

    def _run_target_relocalize(self, status: dict[str, Any]):
        self.agv_motion.stop()
        color, depth = self._latest_images()
        detection = self._locked_detection(color["image"]) if color is not None else None
        from .vision.targeting import depth_valid_for_detection
        if detection is None or depth is None or not depth_valid_for_detection(
            depth["image"],
            detection,
            source_size=(color["image"].shape[1], color["image"].shape[0]),
        ):
            self._begin_target_recovery("目标后退后重新定位失败")
            return
        side = self._target_event.side if self._target_event else "left"
        ok, message = self.arm.start_fixed_sequence(side)
        if not ok:
            self._fail(f"固定示教启动失败: {message}")
            return
        self._transition(TARGET_FIXED, f"固定{side}侧示教靠近")

    def _run_target_fixed(self, status: dict[str, Any]):
        self.agv_motion.stop()
        arm = self.arm.snapshot()
        if arm["state"] == "ERROR":
            side = self._target_event.side if self._target_event else None
            self._begin_target_recovery(f"固定抵近失败: {arm['error']}", side)
            return
        color, depth = self._latest_images()
        if arm.get("sequence_phase") == "PHOTO_HOLD" and color is not None and self._target_event is not None:
            frame_age = self._latest_frame_age(color)
            frame_stamp = float(color["stamp_s"])
            frame_is_usable = accept_near_frame(
                self._target_last_frame_stamp_s,
                frame_stamp,
                float(frame_age if frame_age is not None else math.inf),
                float(self.camera_config.get("frame_max_age_s", 1.0)),
            )
            detection = self._locked_detection(color["image"]) if frame_is_usable else None
            if detection is not None:
                from .vision.targeting import robust_bbox_depth
                summary = (
                    robust_bbox_depth(
                        depth["image"],
                        detection,
                        source_size=(color["image"].shape[1], color["image"].shape[0]),
                    )
                    if depth is not None
                    else None
                )
                self._target_frames.append((color["image"], detection, summary))
                self._target_last_frame_stamp_s = frame_stamp
            required = int(self.vision.get("near_burst_count", 5))
            if len(self._target_frames) >= required and not self._target_near_saved:
                best = choose_best_frame(self._target_frames, 1, required)
                max_rounds = max(1, int(self.vision.get("near_retry_rounds", 3)))
                action = near_burst_action(
                    best.quality.ok,
                    self._target_near_rounds,
                    max_rounds,
                )
                if action == "save":
                    self._target_store.save_near(best.image, {"bbox": best.detection.to_dict(), "quality": best.quality.__dict__}, int(self.vision.get("near_jpeg_quality", 95)))
                    self._target_near_saved = True
                    self.run_log.event("target_near_captured", target_id=self._target_store.target_id, quality=best.quality.score, round=self._target_near_rounds + 1)
                elif action == "retry_hold":
                    self._target_near_rounds += 1
                    self._target_frames = []
                    self.state_detail = (
                        "近拍质量不合格，保持当前姿态继续连拍 "
                        f"{self._target_near_rounds + 1}/{max_rounds} 轮"
                    )
                    return
                else:
                    self._target_store.write_metadata({"status": "near_failed", "failure_reason": "近拍质量不合格: " + ", ".join(best.quality.reasons)})
                    self._begin_target_recovery("近拍质量不合格，已达到重拍上限", self._target_event.side if self._target_event else None)
                    return
        if arm.get("sequence_completed"):
            if not self._target_near_saved and self._target_store is not None:
                self._target_store.write_metadata({"status": "near_failed", "failure_reason": "近拍姿态未获得合格帧"})
            self.run_log.event("target_task_completed", target_id=self._target_store.target_id if self._target_store else None)
            self._abort_target("target completed")
            self._transition(ROUTE_MOVE, "目标任务完成，恢复原方向巡检")

    def _fail(self, message: str):
        with self._lock:
            self.agv_motion.stop()
            self._last_vx = self._last_w = 0.0
            self.arm.stop()
            self.patrol_run_store.finish("error")
            self._transition(ERROR, message)

    def _run_target_recover(self, status: dict[str, Any]):
        self.agv_motion.stop()
        self._last_vx = self._last_w = 0.0
        failure = self._runtime_safety(status, require_arm=False)
        if failure:
            self._fail(f"目标回巡视期间安全失败: {failure}")
            return
        arm = self.arm.snapshot()
        if arm["state"] == "ERROR":
            self._fail(f"目标回巡视失败: {arm['error']}")
            return
        if not arm.get("sequence_completed"):
            self.state_detail = f"底盘停止，目标失败回巡视: {arm['state']}"
            return
        self.run_log.event(
            "target_task_failed_resume",
            target_id=self._target_store.target_id if self._target_store else None,
            reason=self._target_store.metadata().get("failure_reason") if self._target_store else None,
        )
        self._abort_target("target failed; resumed route")
        self._transition(ROUTE_MOVE, "目标任务失败已回巡视，恢复原方向巡检")

    def _enter_blocked(self, status: dict[str, Any]):
        self._resume_state = self.state
        self._blocked_clear_since = None
        self.agv_motion.stop()
        self._last_vx = self._last_w = 0.0
        if self.state in ROUTE_STATES:
            self.arm.stop()
        self._transition(BLOCKED, f"block_reason={status.get('block_reason')}")

    def _enter_camera_wait(self, resume_state: str):
        self.agv_motion.stop()
        self._last_vx = self._last_w = 0.0
        if resume_state in ROUTE_STATES | {ARM_HOME}:
            self.arm.stop()
        self._camera_resume_state = resume_state
        self._camera_lost_since = time.monotonic()
        self._camera_recovered_since = None
        self._transition(CAMERA_WAIT, f"D435短暂断流，已暂停: {self._camera_status}")

    def _handle_camera_wait(self, status: dict[str, Any]):
        failure = self._runtime_safety(status, require_arm=False)
        if failure:
            self._fail(failure)
            return
        now = time.monotonic()
        fail_s = float(self.safety.get("camera_fail_s", 5.0))
        camera_lost_for = (
            now - self._camera_last_ok
            if self._camera_last_ok
            else now - float(self._camera_lost_since or now)
        )
        if camera_lost_for > fail_s:
            self._fail(f"D435断流超过 {fail_s:.1f}s: {self._camera_status}")
            return
        if not self._camera_is_fresh():
            self._camera_recovered_since = None
            return
        if self._camera_recovered_since is None:
            self._camera_recovered_since = now
            return
        recovery_s = float(self.safety.get("camera_recovery_s", 2.0))
        if now - self._camera_recovered_since < recovery_s:
            self.state_detail = f"D435已恢复，稳定等待 {recovery_s:.1f}s"
            return

        resume = self._camera_resume_state or STOPPED
        if resume in ROUTE_STATES:
            ok, message = self.arm.start()
            if not ok:
                self._fail(f"D435恢复后J5恢复失败: {message}")
                return
        elif resume == ARM_HOME:
            ok, message = self.arm.start_home()
            if not ok:
                self._fail(f"D435恢复后 goto home 失败: {message}")
                return
        self._camera_resume_state = None
        self._camera_lost_since = None
        self._camera_recovered_since = None
        self._transition(resume, f"D435连续恢复 {recovery_s:.1f}s，自动继续")

    def _run_route_state(self, status: dict[str, Any]):
        failure = self._runtime_safety(status, require_arm=True)
        if failure:
            self._fail(failure)
            return
        if status.get("blocked"):
            self._enter_blocked(status)
            return

        segment = self._current_segment()
        if endpoint_reached(
            status,
            segment,
            float(self.control["endpoint_tolerance_m"]),
        ):
            self.agv_motion.stop()
            self._last_vx = self._last_w = 0.0
            self._pause_until = time.monotonic() + float(self.control["endpoint_pause_s"])
            self._transition(END_PAUSE, f"到达 {segment.end_name}")
            return

        _, _, progress = compute_segment_velocity(
            status=status,
            segment=segment,
            speed_mps=1.0,
            cross_track_gain=float(self.control["cross_track_gain"]),
            heading_gain=float(self.control["heading_gain"]),
            max_angular_speed=float(self.control["max_angular_speed_rad_s"]),
            correction_threshold_m=float(self.control.get("correction_threshold_m", 0.0)),
            rotate_in_place_threshold_rad=math.radians(
                float(self.control.get("rotate_in_place_threshold_deg", 30.0))
            ),
            heading_slowdown_threshold_rad=math.radians(
                float(self.control.get("heading_slowdown_threshold_deg", 10.0))
            ),
            min_heading_scale=float(self.control.get("min_heading_scale", 0.2)),
        )
        cross_limit = float(self.safety["hard_cross_track_m"])
        if abs(progress.cross_track) > cross_limit:
            self._fail(f"横向偏差超过硬限制: {progress.cross_track:.3f}m")
            return

        if self._maybe_start_target(status, progress):
            return

        speed = endpoint_approach_speed(
            distance_m=progress.remaining_along,
            cruise_speed_mps=float(self.control["patrol_speed_mps"]),
            slowdown_distance_m=float(self.control["endpoint_slowdown_distance_m"]),
            stop_distance_m=float(self.control["endpoint_tolerance_m"]),
            minimum_speed_mps=float(self.control["endpoint_min_speed_mps"]),
        )
        target_vx, target_w, progress = compute_segment_velocity(
            status=status,
            segment=segment,
            speed_mps=speed,
            cross_track_gain=float(self.control["cross_track_gain"]),
            heading_gain=float(self.control["heading_gain"]),
            max_angular_speed=float(self.control["max_angular_speed_rad_s"]),
            correction_threshold_m=float(self.control.get("correction_threshold_m", 0.0)),
            rotate_in_place_threshold_rad=math.radians(
                float(self.control.get("rotate_in_place_threshold_deg", 30.0))
            ),
            heading_slowdown_threshold_rad=math.radians(
                float(self.control.get("heading_slowdown_threshold_deg", 10.0))
            ),
            min_heading_scale=float(self.control.get("min_heading_scale", 0.2)),
        )
        now = time.monotonic()
        dt = min(max(now - self._last_velocity_update, 0.0), 0.2)
        self._last_velocity_update = now
        vx = slew_rate(
            self._last_vx,
            target_vx,
            float(self.control.get("linear_accel_limit_mps2", 0.08)),
            dt,
        )
        w = slew_rate(
            self._last_w,
            target_w,
            float(self.control.get("angular_accel_limit_rad_s2", 0.30)),
            dt,
        )
        self.agv_motion.set_velocity(vx, w)
        self._last_vx, self._last_w = vx, w
        self.state_detail = (
            f"{segment.start_name}->{segment.end_name} "
            f"vx={vx:.3f} w={w:.3f} 横向={progress.cross_track:.3f} "
            f"剩余={progress.remaining_along:.3f}m "
            f"航向误差={math.degrees(progress.heading_error):.1f}deg"
        )

    def _handle_end_pause(self, status: dict[str, Any]):
        failure = self._runtime_safety(status, require_arm=True)
        if failure:
            self._fail(failure)
            return
        if status.get("blocked"):
            self._enter_blocked(status)
            return
        if time.monotonic() < self._pause_until:
            return
        self._route_index += 1
        if self._route_index >= len(self._active_segments):
            if self._route_loop:
                self._route_index = 0
                self._target_loop_id += 1
            else:
                self.agv_motion.stop()
                self._last_vx = self._last_w = 0.0
                self.arm.stop()
                self.patrol_run_store.finish("finished")
                self._transition(
                    STOPPED,
                    f"wens1 路线完成，已到 {self.station_order[-1]}",
                )
                self.run_log.event("route_completed", station=self.station_order[-1])
                return
        self._transition(ROUTE_MOVE, f"继续路线 {self._route_label()}")

    def _run_test_state(self, status: dict[str, Any], state: str):
        failure = self._runtime_safety(status, require_arm=False)
        if failure:
            self._fail(failure)
            return
        if status.get("blocked"):
            self._enter_blocked(status)
            return
        if self._test_start is None:
            self._fail("测试起点丢失")
            return
        moved = math.hypot(
            float(status["x"]) - self._test_start[0],
            float(status["y"]) - self._test_start[1],
        )
        if moved >= self._test_distance:
            self.agv_motion.stop()
            self._last_vx = self._last_w = 0.0
            self._transition(STOPPED, f"短距离测试完成，实际 {moved:.3f}m")
            return
        speed = float(self.control["test_speed_mps"])
        target_vx = speed if state == TEST_FORWARD else -speed
        if state == TEST_BACKWARD and not reverse_motion_allowed(self.safety):
            self._fail("车尾雷达未验证，运行时安全锁已禁止倒车")
            return
        now = time.monotonic()
        dt = min(max(now - self._last_velocity_update, 0.0), 0.2)
        self._last_velocity_update = now
        vx = slew_rate(
            self._last_vx,
            target_vx,
            float(self.control.get("linear_accel_limit_mps2", 0.08)),
            dt,
        )
        self.agv_motion.set_velocity(vx, 0.0)
        self._last_vx, self._last_w = vx, 0.0
        self.state_detail = f"测试中 vx={vx:.3f} 已走={moved:.3f}/{self._test_distance:.3f}m"

    def _control_tick(self):
        self._lock.acquire()
        try:
            self._publish_static()
            status = self.agv_status.get_status()
            self._publish_pose(status)
            state = self.state

            if (
                state != CAMERA_WAIT
                and self._camera_required_for_state(state)
                and not self._camera_is_fresh()
            ):
                self._enter_camera_wait(state)
                state = CAMERA_WAIT

            if state == CAMERA_WAIT:
                self._handle_camera_wait(status)
            elif state == ROUTE_MOVE:
                self._run_route_state(status)
            elif state == END_PAUSE:
                self._handle_end_pause(status)
            elif state == TARGET_ALIGN_REVERSE:
                self._run_target_align(status)
            elif state == TARGET_RELOCALIZE:
                self._run_target_relocalize(status)
            elif state == TARGET_FIXED:
                self._run_target_fixed(status)
            elif state == TARGET_RECOVER:
                self._run_target_recover(status)
            elif state in {TEST_FORWARD, TEST_BACKWARD}:
                self._run_test_state(status, state)
            elif state == ARM_TEST:
                arm = self.arm.snapshot()
                if arm["state"] == "ERROR":
                    self._fail(f"机械臂独立测试失败: {arm['error']}")
                else:
                    self.state_detail = f"底盘停止，JAKA={arm['state']}"
            elif state == ARM_HOME:
                failure = self._runtime_safety(status, require_arm=False)
                if failure:
                    self._fail(failure)
                else:
                    self.agv_motion.stop()
                    self._last_vx = self._last_w = 0.0
                    arm = self.arm.snapshot()
                    if arm["state"] == "ERROR":
                        self._fail(f"goto home 失败: {arm['error']}")
                    elif arm["sequence_completed"]:
                        self.run_log.event("goto_home_completed")
                        self._transition(STOPPED, "机械臂已回到初始 camera 姿态")
                    else:
                        self.state_detail = f"底盘停止，goto home: {arm['state']}"
            elif state == ARM_FIXED:
                failure = self._runtime_safety(status, require_arm=False)
                if failure:
                    self._fail(failure)
                else:
                    self.agv_motion.stop()
                    self._last_vx = self._last_w = 0.0
                    arm = self.arm.snapshot()
                    if arm["state"] == "ERROR":
                        self._fail(f"固定示教抵近失败: {arm['error']}")
                    elif arm["sequence_completed"]:
                        self.run_log.event("fixed_approach_completed", side=arm["sequence_side"])
                        self._transition(STOPPED, "固定示教抵近完成，机械臂已回到侧面入口")
                    else:
                        self.state_detail = f"底盘停止，固定示教抵近: {arm['state']}"
            elif state == BLOCKED:
                self.agv_motion.stop()
                failure = self._runtime_safety(status, require_arm=False)
                if failure:
                    self._fail(failure)
                elif status.get("blocked"):
                    self._blocked_clear_since = None
                else:
                    if self._blocked_clear_since is None:
                        self._blocked_clear_since = time.monotonic()
                    clear_time = float(self.safety.get("blocked_clear_s", 2.0))
                    if time.monotonic() - self._blocked_clear_since >= clear_time:
                        resume = self._resume_state or STOPPED
                        if resume in ROUTE_STATES:
                            ok, message = self.arm.start()
                            if not ok:
                                self._fail(f"阻挡解除后 J5 恢复失败: {message}")
                            else:
                                self._transition(
                                    resume,
                                    f"阻挡已连续解除 {clear_time:.1f}s，J5 从当前位置恢复",
                                )
                        else:
                            self._transition(resume, "阻挡解除，自动继续")

            now = time.monotonic()
            if now - self._last_log_sample >= float(self.config["logging"].get("sample_period_s", 0.2)):
                arm = self.arm.snapshot()
                self.run_log.sample_agv(self.state, status, self._last_vx, self._last_w)
                self.run_log.sample_jaka(arm["state"], arm["joint"])
                self._last_log_sample = now
        except Exception as exc:
            self.get_logger().exception("控制循环异常")
            if self.state != ERROR:
                self._fail(f"控制循环异常: {exc}")
        finally:
            self._lock.release()

    def print_status(self):
        agv_ok, agv_message = self._ensure_agv_status()
        arm_ok, arm_message = self.arm.connect_and_check()
        status = self.agv_status.get_status()
        arm = self.arm.snapshot()
        camera_age = time.monotonic() - self._camera_last_ok if self._camera_last_ok else None
        nearest = "-"
        if status.get("x") is not None and status.get("y") is not None:
            name, distance = min(
                (
                    (name, math.hypot(float(status["x"]) - pose[0], float(status["y"]) - pose[1]))
                    for name, pose in self.stations.items()
                ),
                key=lambda item: item[1],
            )
            nearest = f"{name}({distance:.2f}m)"
        print("\n=== wens1 多站点巡检 ===")
        print(f"状态: {self.state}  {self.state_detail}")
        print(f"路线: {' -> '.join(self.station_order)}  当前段: {self._route_label()}")
        print(
            "AGV: "
            f"connected={status['connected']} nearest={nearest} "
            f"x={status.get('x')} y={status.get('y')} angle={status.get('angle')} "
            f"age={status.get('status_age')} blocked={status.get('blocked')} "
            f"emergency={status.get('emergency')}"
        )
        print(
            f"JAKA: connected={arm['connected']} state={arm['state']} "
            f"J5={arm['joint'][4] if arm['joint'] else None} error={arm['error'] or '-'}"
        )
        for line in self.arm.diagnostic_lines(arm):
            print(line)
        if not agv_ok:
            print(f"AGV 状态读取失败: {agv_message}")
        if not arm_ok:
            print(f"JAKA 状态读取失败: {arm_message}")
        print(f"D435: {self._camera_status} age={camera_age}")
        with self._image_lock:
            color_age = self._latest_frame_age(self._latest_color)
            depth_age = self._latest_frame_age(self._latest_depth)
        color_age_text = f"{color_age:.2f}s" if color_age is not None else "None"
        depth_age_text = f"{depth_age:.2f}s" if depth_age is not None else "None"
        print(
            "视觉: "
            f"color_age={color_age_text} depth_age={depth_age_text} "
            f"保存目录={self.vision_image_dir}"
        )
        print(f"日志: {self.run_log.run_dir}\n")

    def print_logs(self):
        print(f"当前日志: {self.run_log.run_dir}")
        previous = self.run_log.previous_runs()
        if not previous:
            print("没有以前的运行日志")
            return
        print("以前的运行日志:")
        for path in previous:
            print(f"  {path}")

    def run_console(self):
        print("\nWenshi Wens1 多站点巡检")
        help_text = (
            "命令: start | start loop | status | stop | goto home | collect | detect | "
            "test arm | test fixed right | test fixed left | test forward 0.2 | test back 0.2 | logs | q"
        )
        print(help_text)
        print("路线: " + " -> ".join(self.station_order))
        while rclpy.ok() and not self._exit_requested.is_set():
            try:
                command = input("wens1> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not command:
                continue
            parts = command.lower().split()
            if parts[0] == "start" and len(parts) == 1:
                print(self.start_route(loop=False)[1])
            elif parts == ["start", "loop"]:
                print(self.start_route(loop=True)[1])
            elif parts[0] == "status" and len(parts) == 1:
                self.print_status()
            elif parts[0] == "stop" and len(parts) == 1:
                self.stop_all()
                print("底盘和 J5 已停止；RViz 与相机继续运行")
            elif parts[0] in {"collect", "capture", "采集"} and len(parts) == 1:
                print(self.collect_image(run_detection=False)[1])
            elif parts[0] in {"detect", "recognize", "识别"} and len(parts) == 1:
                print(self.collect_image(run_detection=True)[1])
            elif parts == ["test", "arm"]:
                print(self.start_arm_test()[1])
            elif len(parts) == 3 and parts[:2] == ["test", "fixed"] and parts[2] in {"right", "left"}:
                print(self.start_fixed_approach(parts[2])[1])
            elif parts[0] == "test" and len(parts) == 3 and parts[1] in {"forward", "back"}:
                try:
                    distance = float(parts[2])
                except ValueError:
                    print("测试距离必须是数字")
                    continue
                direction = 1 if parts[1] == "forward" else -1
                print(self.start_test(direction, distance)[1])
            elif parts == ["goto", "home"]:
                print(self.goto_home()[1])
            elif parts[0] == "logs" and len(parts) == 1:
                self.print_logs()
            elif parts[0] in {"q", "quit", "exit"} and len(parts) == 1:
                break
            else:
                print(help_text)

    def request_exit(self):
        self._exit_requested.set()

    def cleanup(self):
        with self._lock:
            if self._cleanup_done:
                return
            self._cleanup_done = True
            self._exit_requested.set()
            try:
                self.agv_motion.stop()
            except Exception:
                pass
            try:
                self.arm.cleanup()
            except Exception:
                pass
            try:
                run = json.loads(self.patrol_run_store.run_path.read_text(encoding="utf-8"))
                if run.get("status") == "running":
                    self.patrol_run_store.finish("terminated")
            except Exception:
                pass
            self.agv_motion.disconnect()
            self.agv_status.disconnect()
            self._last_vx = self._last_w = 0.0
            self._transition(STOPPED, "程序退出")
            self.run_log.event("program_stopped", state=self.state)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    arguments, ros_args = parser.parse_known_args(argv)
    config = load_config(arguments.config)
    rclpy.init(args=ros_args)
    node = WenshiPatrolNode(config)
    executor = rclpy.executors.MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, name="ros-executor", daemon=True)
    spin_thread.start()

    def handle_signal(_signum, _frame):
        node.request_exit()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    try:
        node.run_console()
    except KeyboardInterrupt:
        pass
    finally:
        node.cleanup()
        executor.shutdown(timeout_sec=2.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        spin_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
