"""Fixed-base JAKA and panicle-vision expert demonstration."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable, Mapping, Sequence

import cv2
import numpy as np

from .arm_demo_setup import ArmDemoSetupSession, POSE_NAMES, load_arm_demo_setup
from .arm_demo_vision import PanicleVisionPipeline, SnapshotCamera, compose_dual_view
from .config import load_config, resolve_config_path
from .jaka import JakaClient


STATES = {"READY", "RUNNING", "HOLD", "RECOVERING", "ERROR", "STOPPED"}
SPEED_LIMITS = {"transition": 18.0, "approach": 9.0, "scan": 4.0}
PARENT = {
    "left_clearance": "home_safe",
    "rice1_pre": "left_clearance",
    "rice1_center": "rice1_pre",
    "rice1_scan_a": "rice1_center",
    "rice1_scan_b": "rice1_center",
    "rice2_pre": "left_clearance",
    "rice2_center": "rice2_pre",
    "rice2_scan_a": "rice2_center",
    "rice2_scan_b": "rice2_center",
}


@dataclass(frozen=True)
class MotionStep:
    source: str
    target: str
    speed_class: str
    hold_s: float = 0.0


@dataclass
class _RunContext:
    epoch: int
    cancel: threading.Event
    reason: str = ""


def _plant_steps(plant: int) -> list[MotionStep]:
    prefix = f"rice{plant}"
    pre = f"{prefix}_pre"
    center = f"{prefix}_center"
    scan_a = f"{prefix}_scan_a"
    scan_b = f"{prefix}_scan_b"
    return [
        MotionStep("left_clearance", pre, "approach"),
        MotionStep(pre, center, "approach", 1.5),
        MotionStep(center, scan_a, "scan", 1.0),
        MotionStep(scan_a, center, "scan", 0.4),
        MotionStep(center, scan_b, "scan", 1.0),
        MotionStep(scan_b, center, "scan", 1.5),
        MotionStep(center, pre, "approach"),
        MotionStep(pre, "left_clearance", "approach"),
    ]


def build_lap_steps(first_plant: int) -> list[MotionStep]:
    """Build one closed taught-pose lap, alternating plant order by caller."""

    if first_plant not in (1, 2):
        raise ValueError("first_plant 必须是 1 或 2")
    second = 2 if first_plant == 1 else 1
    return [
        MotionStep("home_safe", "left_clearance", "transition"),
        *_plant_steps(first_plant),
        *_plant_steps(second),
        MotionStep("left_clearance", "home_safe", "transition"),
    ]


def _allowed_edges() -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for first in (1, 2):
        for step in build_lap_steps(first):
            edges.add((step.source, step.target))
            edges.add((step.target, step.source))
    return edges


ALLOWED_EDGES = _allowed_edges()


def _finite_joint(value: Any, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise RuntimeError(f"{label} 必须是有限六轴关节角")
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"{label} 必须是有限六轴关节角") from exc
    if not all(math.isfinite(item) for item in result):
        raise RuntimeError(f"{label} 包含非有限关节角")
    return result


def _near(left: Sequence[float], right: Sequence[float], tolerance: float) -> bool:
    return max(abs(float(a) - float(b)) for a, b in zip(left, right)) <= tolerance


def _bounded_number(
    value: Any,
    label: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} 必须是有限数值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须是有限数值")
    number = max(number, minimum)
    return number if maximum is None else min(number, maximum)


def _edge_residual(
    joint: Sequence[float], source: Sequence[float], target: Sequence[float]
) -> float:
    point = np.asarray(joint, dtype=np.float64)
    start = np.asarray(source, dtype=np.float64)
    end = np.asarray(target, dtype=np.float64)
    direction = end - start
    denominator = float(np.dot(direction, direction))
    if denominator <= 1e-12:
        projected = start
    else:
        ratio = float(np.clip(np.dot(point - start, direction) / denominator, 0.0, 1.0))
        projected = start + ratio * direction
    return float(np.max(np.abs(point - projected)))


class ArmDemoController:
    """Threaded arm state machine that only targets whitelisted taught poses."""

    def __init__(
        self,
        config: dict[str, Any],
        setup: dict[str, Any],
        client: Any | None = None,
        log: Callable[[str], None] = print,
    ):
        self.config = config
        self.log = log
        records = setup.get("poses") if isinstance(setup, dict) else None
        if not isinstance(records, Mapping) or set(records) != set(POSE_NAMES):
            raise ValueError("arm demo setup 缺少完整 10 姿态")
        self.poses = {
            name: _finite_joint(records[name].get("joint"), f"姿态 {name}")
            for name in POSE_NAMES
        }
        arm = config.get("jaka", {})
        demo = config.get("arm_demo", {})
        self.home_tolerance = _bounded_number(
            demo.get("home_tolerance_deg", 3.0),
            "home_tolerance_deg",
            minimum=0.1,
            maximum=3.0,
        )
        self.arrival_tolerance = _bounded_number(
            arm.get("joint_tolerance_deg", 0.5),
            "joint_tolerance_deg",
            minimum=0.05,
            maximum=2.0,
        )
        self.joint_max_age = _bounded_number(
            demo.get("joint_state_max_age_s", 0.5),
            "joint_state_max_age_s",
            minimum=0.05,
            maximum=1.0,
        )
        self.recovery_corridor = _bounded_number(
            demo.get("recovery_corridor_deg", 3.0),
            "recovery_corridor_deg",
            minimum=0.1,
            maximum=3.0,
        )
        self.motion_timeout = _bounded_number(
            arm.get("motion_timeout_s", 120.0),
            "motion_timeout_s",
            minimum=0.1,
        )
        self.accel = _bounded_number(
            demo.get("accel_deg_s2", 60.0),
            "accel_deg_s2",
            minimum=1.0,
            maximum=60.0,
        )
        self.speeds = {
            name: _bounded_number(
                demo.get(f"{name}_speed_deg_s", limit),
                f"{name}_speed_deg_s",
                minimum=0.1,
                maximum=limit,
            )
            for name, limit in SPEED_LIMITS.items()
        }
        self.connect_timeout = _bounded_number(
            arm.get("connect_timeout_s", 3.0),
            "connect_timeout_s",
            minimum=0.1,
        )
        self.client = client or JakaClient(
            str(arm["ip"]),
            int(arm.get("port", 10001)),
            joint_tolerance_deg=self.arrival_tolerance,
            command_interval_s=float(arm.get("command_interval_s", 0.1)),
            motion_start_wait_s=float(arm.get("motion_start_wait_s", 0.5)),
            motion_stall_timeout_s=float(arm.get("motion_stall_timeout_s", 10.0)),
            joint_state_timeout_s=self.joint_max_age,
            log=log,
        )
        self._lock = threading.RLock()
        self._state = "STOPPED"
        self._error = ""
        self._epoch = 0
        self._context: _RunContext | None = None
        self._worker: threading.Thread | None = None
        self._stop_pending = 0
        self._confirmed_node: str | None = None
        self._active_edge: tuple[str, str] | None = None
        self._recovery_anchor: str | None = None
        self._recovery_edge: tuple[str, str] | None = None
        self._lap = 0
        self._test_once = False

    def _joint_snapshot(self, target: str | None = None, newer_than: int | None = None):
        snapshot = self.client.snapshot()
        if snapshot.get("connected") is not True:
            raise RuntimeError("JAKA 连接已断开")
        joint = _finite_joint(snapshot.get("joint"), "JAKA 关节状态")
        age = snapshot.get("joint_status_age")
        seq = snapshot.get("joint_seq")
        try:
            age_value = float(age)
            seq_value = int(seq)
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("JAKA 缺少独立的新鲜关节状态") from exc
        if not math.isfinite(age_value) or age_value < 0 or age_value > self.joint_max_age:
            raise RuntimeError(f"JAKA 关节状态过期: {age_value!r}s")
        if newer_than is not None and seq_value <= newer_than:
            raise RuntimeError("JAKA 运动后没有新的关节状态")
        if target is not None and not _near(joint, self.poses[target], self.arrival_tolerance):
            raise RuntimeError(f"JAKA 未到达示教姿态 {target}")
        return joint, seq_value

    def _fresh_joint_snapshot(self):
        if not self.client.wait_for_joint_state(timeout=self.connect_timeout):
            raise RuntimeError("无法读取新的 JAKA 关节状态")
        return self._joint_snapshot()

    def connect(self) -> None:
        if not self.client.connect(timeout=self.connect_timeout):
            detail = str(getattr(self.client, "last_error", "")).strip()
            raise RuntimeError("无法连接 JAKA" + (f": {detail}" if detail else ""))
        if not self.client.wait_for_joint_state(timeout=self.connect_timeout):
            raise RuntimeError("无法读取新的 JAKA 关节状态")
        joint, _seq = self._joint_snapshot()
        if not _near(joint, self.poses["home_safe"], self.home_tolerance):
            raise RuntimeError("当前机械臂不在 home_safe 3 deg 范围，禁止自动启动")
        with self._lock:
            self._state = "READY"
            self._error = ""
            self._confirmed_node = "home_safe"
            self._active_edge = None
            self._recovery_anchor = None
            self._recovery_edge = None

    def _new_context_locked(self) -> _RunContext:
        self._epoch += 1
        context = _RunContext(self._epoch, threading.Event())
        self._context = context
        return context

    def _context_valid_locked(self, context: _RunContext, state: str) -> bool:
        return (
            self._context is context
            and context.epoch == self._epoch
            and not context.cancel.is_set()
            and self._state == state
        )

    def _launch_locked(self, context: _RunContext, target: Callable[..., None], *args) -> None:
        worker = threading.Thread(
            target=target,
            args=(context, *args),
            name=f"arm-demo-{context.epoch}",
            daemon=True,
        )
        self._worker = worker
        worker.start()

    def start(self, test_once: bool = False) -> None:
        joint, _seq = self._fresh_joint_snapshot()
        if not _near(joint, self.poses["home_safe"], self.home_tolerance):
            raise RuntimeError("机械臂不在 home_safe，禁止开始")
        with self._lock:
            if self._state != "READY":
                raise RuntimeError(f"当前状态 {self._state} 不能开始")
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("旧机械臂 worker 尚未退出")
            context = self._new_context_locked()
            self._state = "RUNNING"
            self._error = ""
            self._test_once = bool(test_once)
            self._launch_locked(context, self._run_laps, bool(test_once))

    def _speed(self, speed_class: str, test_once: bool) -> float:
        if speed_class not in self.speeds:
            raise RuntimeError(f"未知速度类型: {speed_class}")
        value = self.speeds[speed_class]
        return min(value, 5.0) if test_once else value

    def _move(
        self,
        context: _RunContext,
        step: MotionStep,
        *,
        state: str,
        test_once: bool,
    ) -> bool:
        if (
            step.source not in self.poses
            or step.target not in self.poses
            or (step.source, step.target) not in ALLOWED_EDGES
        ):
            self._fault_from_worker(context, f"禁止的示教姿态边: {step.source}->{step.target}")
            return False
        try:
            _joint, before_seq = self._fresh_joint_snapshot()
        except RuntimeError as exc:
            self._fault_from_worker(context, str(exc))
            return False
        with self._lock:
            if not self._context_valid_locked(context, state):
                return False
            self._active_edge = (step.source, step.target)
        try:
            moved = bool(
                self.client.joint_move(
                    self.poses[step.target],
                    self._speed(step.speed_class, test_once),
                    self.accel,
                    self.motion_timeout,
                    cancel_requested=context.cancel.is_set,
                )
            )
        except Exception as exc:
            self.fault(f"机械臂运动异常: {exc}")
            return False
        if not moved:
            with self._lock:
                expected_cancel = context.cancel.is_set() and self._state in {
                    "HOLD",
                    "ERROR",
                    "STOPPED",
                }
            detail = str(getattr(self.client, "last_error", "")).strip()
            expected = expected_cancel and (
                context.reason in {"operator_pause", "fault", "stop"}
                and (not detail or "停止" in detail or "cancel" in detail.lower())
            )
            if not expected:
                self.fault(detail or "机械臂运动返回 false")
            return False
        try:
            self._joint_snapshot(target=step.target, newer_than=before_seq)
        except RuntimeError as exc:
            self._fault_from_worker(context, str(exc))
            return False
        with self._lock:
            if not self._context_valid_locked(context, state):
                return False
            self._confirmed_node = step.target
            self._active_edge = None
        return not (step.hold_s > 0 and context.cancel.wait(step.hold_s))

    def _run_laps(self, context: _RunContext, test_once: bool) -> None:
        try:
            while True:
                with self._lock:
                    if not self._context_valid_locked(context, "RUNNING"):
                        return
                    first = 1 if self._lap % 2 == 0 else 2
                for step in build_lap_steps(first):
                    if not self._move(
                        context, step, state="RUNNING", test_once=test_once
                    ):
                        return
                with self._lock:
                    if not self._context_valid_locked(context, "RUNNING"):
                        return
                    self._lap += 1
                    if test_once:
                        self._state = "READY"
                        self._context = None
                        self._confirmed_node = "home_safe"
                        return
        except Exception as exc:
            self._fault_from_worker(context, f"机械臂状态机异常: {exc}")

    def _invalidate_locked(self, reason: str) -> None:
        context = self._context
        if context is not None:
            context.reason = reason
            context.cancel.set()
        self._epoch += 1

    def _request_stop(self) -> None:
        try:
            self.client.stop()
        finally:
            with self._lock:
                self._stop_pending = max(0, self._stop_pending - 1)

    def pause(self) -> None:
        with self._lock:
            if self._state not in {"RUNNING", "RECOVERING"}:
                return
            was_recovering = self._state == "RECOVERING"
            self._recovery_edge = self._active_edge
            self._recovery_anchor = (
                self._active_edge[1 if was_recovering else 0]
                if self._active_edge
                else self._confirmed_node
            )
            self._invalidate_locked("operator_pause")
            self._state = "HOLD"
            self._stop_pending += 1
        self._request_stop()

    def _fault_from_worker(self, context: _RunContext, reason: str) -> None:
        with self._lock:
            if self._state in {"ERROR", "STOPPED"}:
                return
        self.fault(reason)

    def fault(self, reason: str) -> None:
        message = str(reason).strip() or "未知故障"
        with self._lock:
            if self._state == "STOPPED":
                return
            if self._state == "ERROR":
                if not self._error:
                    self._error = message
                return
            self._recovery_edge = self._active_edge
            self._recovery_anchor = (
                self._active_edge[0] if self._active_edge else self._confirmed_node
            )
            self._invalidate_locked("fault")
            self._state = "ERROR"
            self._error = message
            self._stop_pending += 1
        self._request_stop()

    def _recovery_steps(self, anchor: str, include_anchor: bool) -> list[MotionStep]:
        steps: list[MotionStep] = []
        current = anchor
        if include_anchor:
            edge = self._recovery_edge
            if edge is None:
                raise RuntimeError("缺少活动示教边，禁止自动回撤")
            if anchor not in edge:
                raise RuntimeError("回撤锚点不属于活动示教边")
            opposite = edge[1] if edge[0] == anchor else edge[0]
            steps.append(
                MotionStep(opposite, anchor, self._edge_speed_class(opposite, anchor))
            )
        while current != "home_safe":
            parent = PARENT.get(current)
            if parent is None:
                raise RuntimeError(f"姿态 {current} 没有安全回撤父节点")
            steps.append(
                MotionStep(current, parent, self._edge_speed_class(current, parent))
            )
            current = parent
        return steps

    @staticmethod
    def _edge_speed_class(source: str, target: str) -> str:
        if "scan_" in source or "scan_" in target:
            return "scan"
        if {source, target} == {"home_safe", "left_clearance"}:
            return "transition"
        return "approach"

    def recover_home(self) -> None:
        with self._lock:
            if self._state == "ERROR":
                raise RuntimeError("ERROR 故障状态禁止自动回家")
            if self._state != "HOLD":
                raise RuntimeError(f"当前状态 {self._state} 不能安全回撤")
            captured_epoch = self._epoch
            worker = self._worker
            anchor = self._recovery_anchor
            edge = self._recovery_edge
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self.motion_timeout + 1.0)
            if worker.is_alive():
                raise RuntimeError("旧机械臂 worker 尚未退出，禁止回撤")
        joint, _seq = self._fresh_joint_snapshot()
        if anchor is None:
            raise RuntimeError("无法证明暂停位置，禁止自动回撤")
        if edge is not None:
            residual = _edge_residual(
                joint, self.poses[edge[0]], self.poses[edge[1]]
            )
            if residual > self.recovery_corridor:
                raise RuntimeError("当前位置不在已示教边安全走廊，禁止自动回撤")
        elif not _near(joint, self.poses[anchor], self.home_tolerance):
            raise RuntimeError("当前位置无法匹配已确认姿态，禁止自动回撤")
        with self._lock:
            if (
                self._state != "HOLD"
                or self._epoch != captured_epoch
                or self._stop_pending != 0
            ):
                raise RuntimeError("停止命令或状态仍在变化，禁止回撤")
            context = self._new_context_locked()
            self._state = "RECOVERING"
            steps = self._recovery_steps(anchor, edge is not None)
            self._launch_locked(context, self._run_recovery, steps)

    def _run_recovery(self, context: _RunContext, steps: list[MotionStep]) -> None:
        try:
            for step in steps:
                if not self._move(
                    context, step, state="RECOVERING", test_once=True
                ):
                    return
            with self._lock:
                if not self._context_valid_locked(context, "RECOVERING"):
                    return
                self._state = "READY"
                self._context = None
                self._confirmed_node = "home_safe"
                self._active_edge = None
                self._recovery_anchor = None
                self._recovery_edge = None
        except Exception as exc:
            self._fault_from_worker(context, f"安全回撤异常: {exc}")

    def stop(self) -> None:
        with self._lock:
            if self._state == "STOPPED":
                return
            self._invalidate_locked("stop")
            self._state = "STOPPED"
            self._stop_pending += 1
        self._request_stop()

    def close(self) -> None:
        try:
            self.stop()
        finally:
            self.client.disconnect()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "error": self._error,
                "lap": self._lap,
                "current_pose": self._confirmed_node,
                "active_edge": self._active_edge,
                "stop_pending": self._stop_pending,
            }


def _state_line(snapshot: Mapping[str, Any]) -> str:
    state = snapshot.get("state", "UNKNOWN")
    pose = snapshot.get("current_pose") or "between poses"
    lap = snapshot.get("lap", 0)
    error = str(snapshot.get("error", "")).strip()
    base = f"ARM {state}  POSE {pose}  LAP {lap}  Space start/pause | H home | P photo | Q exit"
    return f"{base}  ERROR {error}" if error else base


def run_display(
    controller: Any,
    vision: Any,
    photo_dir: Path,
    *,
    test_once: bool = False,
    fullscreen: bool = True,
    max_age_s: float = 1.0,
    canvas_size: tuple[int, int] = (1600, 900),
    cv: Any = cv2,
) -> int:
    """Own the complete OpenCV GUI lifecycle on the calling thread."""

    window = "Wenshi Arm Panicle Demo"
    photos = Path(photo_dir).expanduser().resolve()
    fault_reported = False
    window_created = False
    try:
        cv.namedWindow(window, cv.WINDOW_NORMAL)
        window_created = True
        if fullscreen:
            cv.setWindowProperty(window, cv.WND_PROP_FULLSCREEN, cv.WINDOW_FULLSCREEN)
        while True:
            result = vision.latest()
            health_error = vision.health_error(max_age_s)
            if health_error and not fault_reported:
                controller.fault(health_error)
                fault_reported = True
            if result is None:
                time.sleep(0.01)
                continue
            frame = compose_dual_view(
                result, _state_line(controller.snapshot()), canvas_size=canvas_size
            )
            cv.imshow(window, frame)
            if cv.getWindowProperty(window, cv.WND_PROP_VISIBLE) < 1:
                break
            key = cv.waitKey(15) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                break
            if key == 32:
                state = controller.snapshot().get("state")
                if state == "READY":
                    controller.start(test_once=test_once)
                elif state in {"RUNNING", "RECOVERING"}:
                    controller.pause()
            elif key in (ord("h"), ord("H")):
                if controller.snapshot().get("state") == "HOLD":
                    controller.recover_home()
            elif key in (ord("p"), ord("P")):
                photos.mkdir(parents=True, exist_ok=True)
                destination = photos / f"arm_demo_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000_000:09d}.jpg"
                if not cv.imwrite(str(destination), frame, [cv.IMWRITE_JPEG_QUALITY, 95]):
                    raise OSError(f"无法保存展示截图: {destination}")
        return 0
    finally:
        try:
            controller.stop()
        finally:
            try:
                vision.stop()
            finally:
                if window_created:
                    cv.destroyWindow(window)


def _camera_frame(camera: SnapshotCamera) -> np.ndarray:
    camera.health()
    return camera.capture().image


def _sample_pose(client: Any, timeout_s: float = 2.0):
    samples: list[list[float]] = []
    tcp: list[float] | None = None
    last_seq = int(client.snapshot().get("joint_seq", 0))
    for _ in range(3):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            client.refresh()
            time.sleep(0.08)
            snapshot = client.snapshot()
            seq = int(snapshot.get("joint_seq", 0))
            if seq > last_seq:
                samples.append(_finite_joint(snapshot.get("joint"), "JAKA 示教关节角"))
                tcp = _finite_joint(snapshot.get("tcp"), "JAKA 示教 TCP")
                last_seq = seq
                break
        else:
            raise RuntimeError("读取 JAKA 示教姿态超时")
    return samples, tcp


def run_interactive_setup(
    config: dict[str, Any],
    destination: Path,
    runtime_root: Path,
    *,
    resume_root: Path | None = None,
    client: Any | None = None,
    camera: SnapshotCamera | None = None,
    cv: Any = cv2,
    input_fn: Callable[[str], str] = input,
) -> int:
    """Teach ten poses using state reads only; Enter records and Q checkpoints."""

    runtime_root = Path(runtime_root).expanduser().resolve()
    evidence_root = (
        Path(resume_root).expanduser().resolve()
        if resume_root is not None
        else runtime_root / time.strftime("setup_%Y%m%d_%H%M%S")
    )
    setup = (
        ArmDemoSetupSession.resume(evidence_root, runtime_root)
        if resume_root is not None
        else ArmDemoSetupSession(evidence_root, runtime_root)
    )
    if not setup.operator:
        setup.set_operator(input_fn("操作员姓名/编号: "))
    arm_cfg = config.get("jaka", {})
    cam_cfg = config.get("camera", {})
    client = client or JakaClient(str(arm_cfg["ip"]), int(arm_cfg.get("port", 10001)))
    camera = camera or SnapshotCamera(
        str(cam_cfg["server_url"]), timeout_s=float(cam_cfg.get("timeout_s", 1.0))
    )
    if not client.connect(timeout=float(arm_cfg.get("connect_timeout_s", 3.0))):
        raise RuntimeError("setup 无法连接 JAKA")
    window = "Arm Demo Setup"
    window_created = False
    try:
        cv.namedWindow(window, cv.WINDOW_NORMAL)
        window_created = True
        while len(setup.poses) < len(POSE_NAMES):
            name = POSE_NAMES[len(setup.poses)]
            image = _camera_frame(camera)
            preview = image.copy()
            cv.putText(
                preview,
                f"Teach {name}: ENTER record | Q save and exit",
                (20, 35),
                cv.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                cv.LINE_AA,
            )
            cv.imshow(window, preview)
            key = cv.waitKey(30) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                return 2
            if key not in (10, 13):
                continue
            samples, tcp = _sample_pose(client)
            setup.record_pose(name, samples, tcp, image)
        setup.publish(Path(destination))
        return 0
    finally:
        client.disconnect()
        if window_created:
            cv.destroyWindow(window)


def _local_preflight(config: dict[str, Any], setup_file: Path) -> dict[str, Any]:
    setup = load_arm_demo_setup(setup_file)
    model_value = config.get("vision", {}).get("model_path", "")
    model = resolve_config_path(config, str(model_value))
    if not model.is_file():
        raise ValueError(f"稻穗模型不存在: {model}")
    if bool(config.get("vision", {}).get("motion_enable", False)):
        raise ValueError("vision.motion_enable 必须为 false")
    return setup


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).parents[2]
    parser = argparse.ArgumentParser(description="Fixed-base JAKA panicle expert demo")
    parser.add_argument("--config", default=str(root / "config" / "arm_demo.yaml"))
    parser.add_argument("--runtime-root", default=str(root / "runtime" / "arm_demo"))
    parser.add_argument("--setup-file", default=str(root / "runtime" / "arm_demo" / "demo_setup.json"))
    parser.add_argument("--photos", default=str(root / "runtime" / "arm_demo" / "photos"))
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--resume")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--test-once", action="store_true")
    args = parser.parse_args(argv)
    if args.resume and not args.setup:
        parser.error("--resume 只能与 --setup 一起使用")
    if args.setup and (args.dry_run or args.test_once):
        parser.error("--setup 不能与 --dry-run/--test-once 同时使用")
    if args.dry_run and args.test_once:
        parser.error("--dry-run 与 --test-once 不能同时使用")
    config = load_config(args.config)
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    setup_file = Path(args.setup_file).expanduser().resolve()
    if args.setup:
        try:
            return run_interactive_setup(
                config,
                setup_file,
                runtime_root,
                resume_root=None if args.resume is None else Path(args.resume),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"arm demo setup 失败: {exc}", file=__import__("sys").stderr)
            return 1
    try:
        setup = _local_preflight(config, setup_file)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"arm demo 本地校验失败: {exc}", file=__import__("sys").stderr)
        return 2
    if args.dry_run:
        print("dry-run: config/model/setup validated; no hardware connection")
        return 0
    camera_cfg = config.get("camera", {})
    vision_cfg = config.get("vision", {})
    camera = SnapshotCamera(
        str(camera_cfg["server_url"]),
        timeout_s=float(camera_cfg.get("timeout_s", 1.0)),
    )
    vision = PanicleVisionPipeline(
        camera,
        str(resolve_config_path(config, vision_cfg["model_path"])),
        conf_threshold=float(vision_cfg.get("conf_threshold", 0.25)),
        poll_interval_s=1.0 / max(float(camera_cfg.get("rate_hz", 10.0)), 1.0),
    )
    controller: ArmDemoController | None = None
    try:
        vision.start()
        deadline = time.monotonic() + float(camera_cfg.get("startup_wait_s", 5.0))
        while vision.latest() is None and time.monotonic() < deadline:
            if vision.fatal_error:
                raise RuntimeError(vision.fatal_error)
            time.sleep(0.02)
        if vision.latest() is None:
            raise RuntimeError(vision.health_error(1.0) or "相机/模型启动超时")
        controller = ArmDemoController(config, setup)
        controller.connect()
        ui = config.get("display", {})
        return run_display(
            controller,
            vision,
            Path(args.photos),
            test_once=args.test_once,
            fullscreen=bool(ui.get("fullscreen", True)),
            max_age_s=float(vision_cfg.get("max_age_s", 1.0)),
            canvas_size=(
                int(ui.get("width", 1600)),
                int(ui.get("height", 900)),
            ),
        )
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"arm demo 启动失败: {exc}", file=__import__("sys").stderr)
        return 1
    finally:
        try:
            if controller is not None:
                controller.close()
        finally:
            vision.stop()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ArmDemoController",
    "MotionStep",
    "build_lap_steps",
    "main",
    "run_display",
    "run_interactive_setup",
]
