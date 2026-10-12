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
SPEED_LIMITS = {"transition": 45.0, "approach": 30.0, "scan": 15.0}
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
        MotionStep(pre, center, "approach", 0.7),
        MotionStep(center, scan_a, "scan", 0.5),
        MotionStep(scan_a, center, "scan", 0.2),
        MotionStep(center, scan_b, "scan", 0.5),
        MotionStep(scan_b, center, "scan", 0.7),
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


def _joint_distance(left: Sequence[float], right: Sequence[float]) -> float:
    return max(abs(float(a) - float(b)) for a, b in zip(left, right))


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
        self.startup_bridge_limit = _bounded_number(
            demo.get("startup_bridge_max_joint_deg", 20.0),
            "startup_bridge_max_joint_deg",
            minimum=3.0,
            maximum=20.0,
        )
        self.recovery_speed_cap = _bounded_number(
            demo.get("recovery_speed_cap_deg_s", 20.0),
            "recovery_speed_cap_deg_s",
            minimum=5.0,
            maximum=20.0,
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
        self._startup_bridge_anchor: str | None = None
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

    def connect(self) -> str:
        if not self.client.connect(timeout=self.connect_timeout):
            detail = str(getattr(self.client, "last_error", "")).strip()
            raise RuntimeError("无法连接 JAKA" + (f": {detail}" if detail else ""))
        if not self.client.wait_for_joint_state(timeout=self.connect_timeout):
            raise RuntimeError("无法读取新的 JAKA 关节状态")
        joint, _seq = self._joint_snapshot()
        if _near(joint, self.poses["home_safe"], self.home_tolerance):
            location = "home_safe"
            confirmed_node = "home_safe"
            recovery_anchor = None
            recovery_edge = None
            startup_bridge_anchor = None
        else:
            node_matches = sorted(
                (
                    _joint_distance(joint, self.poses[name]),
                    name,
                )
                for name in POSE_NAMES
                if name != "home_safe"
                and _near(joint, self.poses[name], self.recovery_corridor)
            )
            if node_matches:
                if (
                    len(node_matches) > 1
                    and node_matches[1][0] - node_matches[0][0]
                    <= self.arrival_tolerance
                ):
                    raise RuntimeError("当前机械臂无法唯一匹配已示教姿态，禁止自动回家")
                _distance, location = node_matches[0]
                confirmed_node = location
                recovery_anchor = location
                recovery_edge = None
                startup_bridge_anchor = None
            else:
                edge_matches = sorted(
                    (
                        _edge_residual(joint, self.poses[child], self.poses[parent]),
                        child,
                        parent,
                    )
                    for child, parent in PARENT.items()
                    if _edge_residual(joint, self.poses[child], self.poses[parent])
                    <= self.recovery_corridor
                )
                edge_ambiguous = bool(
                    len(edge_matches) > 1
                    and edge_matches[1][0] - edge_matches[0][0]
                    <= self.arrival_tolerance
                )
                if edge_matches and not edge_ambiguous:
                    _residual, child, parent = edge_matches[0]
                    location = f"{child}->{parent} 之间"
                    confirmed_node = None
                    recovery_anchor = parent
                    recovery_edge = (child, parent)
                    startup_bridge_anchor = None
                else:
                    nearest_nodes = sorted(
                        (_joint_distance(joint, target), name)
                        for name, target in self.poses.items()
                    )
                    nearest_node = nearest_nodes[0]
                    nearest_edge = min(
                        (
                            (
                                _edge_residual(
                                    joint, self.poses[child], self.poses[parent]
                                ),
                                child,
                                parent,
                            )
                            for child, parent in PARENT.items()
                        ),
                        default=(math.inf, "none", "none"),
                    )
                    nearest_unique = (
                        len(nearest_nodes) == 1
                        or nearest_nodes[1][0] - nearest_nodes[0][0]
                        > self.arrival_tolerance
                    )
                    if nearest_node[0] > self.startup_bridge_limit or not nearest_unique:
                        raise RuntimeError(
                            "当前机械臂无法安全接入已示教路径，"
                            f"最近姿态={nearest_node[1]} 偏差={nearest_node[0]:.1f}deg，"
                            f"自动接入上限={self.startup_bridge_limit:.1f}deg，"
                            f"最近示教边={nearest_edge[1]}->{nearest_edge[2]} "
                            f"残差={nearest_edge[0]:.1f}deg"
                        )
                    recovery_anchor = nearest_node[1]
                    startup_bridge_anchor = recovery_anchor
                    recovery_edge = None
                    confirmed_node = None
                    location = f"当前位置->{recovery_anchor} 低速接入"
        with self._lock:
            self._state = "READY" if location == "home_safe" else "HOLD"
            self._error = ""
            self._confirmed_node = confirmed_node
            self._active_edge = None
            self._recovery_anchor = recovery_anchor
            self._recovery_edge = recovery_edge
            self._startup_bridge_anchor = startup_bridge_anchor
        return location

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

    def _speed(
        self,
        speed_class: str,
        test_once: bool,
        speed_cap: float | None = None,
    ) -> float:
        if speed_class not in self.speeds:
            raise RuntimeError(f"未知速度类型: {speed_class}")
        value = self.speeds[speed_class]
        if test_once:
            value = min(value, 5.0)
        return value if speed_cap is None else min(value, speed_cap)

    def _move(
        self,
        context: _RunContext,
        step: MotionStep,
        *,
        state: str,
        test_once: bool,
        startup_bridge: bool = False,
        speed_cap: float | None = None,
    ) -> bool:
        valid_bridge = (
            startup_bridge
            and step.source == "startup_current"
            and step.target in self.poses
        )
        if not valid_bridge and (
            step.source not in self.poses
            or step.target not in self.poses
            or (step.source, step.target) not in ALLOWED_EDGES
        ):
            self._fault_from_worker(context, f"禁止的示教姿态边: {step.source}->{step.target}")
            return False
        try:
            current_joint, before_seq = self._fresh_joint_snapshot()
        except RuntimeError as exc:
            self._fault_from_worker(context, str(exc))
            return False
        if valid_bridge and _joint_distance(
            current_joint, self.poses[step.target]
        ) > self.startup_bridge_limit:
            self._fault_from_worker(context, "启动接入距离超过上限")
            return False
        with self._lock:
            if not self._context_valid_locked(context, state):
                return False
            self._active_edge = None if valid_bridge else (step.source, step.target)
        try:
            moved = bool(
                self.client.joint_move(
                    self.poses[step.target],
                    self._speed(step.speed_class, test_once, speed_cap),
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
                second = 2 if first == 1 else 1
                self.log(f"开始观察水稻{first}")
                for step in build_lap_steps(first):
                    if not self._move(
                        context, step, state="RUNNING", test_once=test_once
                    ):
                        return
                    if (
                        step.source == f"rice{first}_pre"
                        and step.target == "left_clearance"
                    ):
                        self.log(f"水稻{first}观察完成，前往水稻{second}")
                    elif (
                        step.source == f"rice{second}_pre"
                        and step.target == "left_clearance"
                    ):
                        self.log(f"水稻{second}观察完成")
                with self._lock:
                    if not self._context_valid_locked(context, "RUNNING"):
                        return
                    self._lap += 1
                    completed_lap = self._lap
                    if test_once:
                        self._state = "READY"
                        self._context = None
                        self._confirmed_node = "home_safe"
                self.log(f"本轮完成：第{completed_lap}轮")
                if test_once:
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
        try:
            self.log(f"机械臂演示故障停臂: {message}")
        except Exception:
            pass
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
            bridge_anchor = self._startup_bridge_anchor
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=self.motion_timeout + 1.0)
            if worker.is_alive():
                raise RuntimeError("旧机械臂 worker 尚未退出，禁止回撤")
        joint, _seq = self._fresh_joint_snapshot()
        if anchor is None:
            raise RuntimeError("无法证明暂停位置，禁止自动回撤")
        if bridge_anchor is not None:
            if bridge_anchor != anchor or _joint_distance(
                joint, self.poses[bridge_anchor]
            ) > self.startup_bridge_limit:
                raise RuntimeError("当前位置超出启动低速接入范围")
        elif edge is not None:
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
            self._launch_locked(context, self._run_recovery, steps, bridge_anchor)

    def _run_recovery(
        self,
        context: _RunContext,
        steps: list[MotionStep],
        bridge_anchor: str | None = None,
    ) -> None:
        try:
            if bridge_anchor is not None and not self._move(
                context,
                MotionStep("startup_current", bridge_anchor, "scan"),
                state="RECOVERING",
                test_once=False,
                startup_bridge=True,
                speed_cap=self.recovery_speed_cap,
            ):
                return
            for step in steps:
                if not self._move(
                    context,
                    step,
                    state="RECOVERING",
                    test_once=False,
                    speed_cap=self.recovery_speed_cap,
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
                self._startup_bridge_anchor = None
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
    auto_start: bool = False,
    fullscreen: bool = True,
    max_age_s: float = 5.0,
    canvas_size: tuple[int, int] = (1600, 900),
    cv: Any = cv2,
    log: Callable[[str], None] = print,
) -> int:
    """Own the complete OpenCV GUI lifecycle on the calling thread."""

    window = "Wenshi Arm Panicle Demo"
    photos = Path(photo_dir).expanduser().resolve()
    vision_warning_active = False
    vision_warning = ""
    window_created = False
    auto_start_pending = bool(auto_start)
    try:
        cv.namedWindow(window, cv.WINDOW_NORMAL)
        window_created = True
        if fullscreen:
            cv.setWindowProperty(window, cv.WND_PROP_FULLSCREEN, cv.WINDOW_FULLSCREEN)
        while True:
            result = vision.latest()
            health_error = vision.health_error(max_age_s)
            if health_error:
                vision_warning = str(health_error).strip() or "未知视觉错误"
                if not vision_warning_active:
                    log(
                        "视觉链路告警（机械臂继续循环并自动重试）: "
                        f"{vision_warning}"
                    )
                vision_warning_active = True
            elif vision_warning_active:
                log("视觉链路已恢复")
                vision_warning_active = False
                vision_warning = ""
            if result is None:
                time.sleep(0.01)
                continue
            status_line = _state_line(controller.snapshot())
            if vision_warning_active:
                status_line += "  VISION RECONNECTING"
            frame = compose_dual_view(
                result, status_line, canvas_size=canvas_size
            )
            cv.imshow(window, frame)
            wait_key = getattr(cv, "waitKeyEx", None)
            key = int(wait_key(15) if callable(wait_key) else cv.waitKey(15))
            if key in (ord("q"), ord("Q"), 27):
                snapshot = controller.snapshot()
                key_name = "Esc" if key == 27 else chr(key)
                error = str(snapshot.get("error", "")).strip() or "none"
                log(
                    f"界面退出: 按键 {key_name} raw={key} "
                    f"state={snapshot.get('state', 'UNKNOWN')} error={error}"
                )
                break
            if key == 32:
                state = controller.snapshot().get("state")
                if state == "READY":
                    controller.start(test_once=test_once)
                    auto_start_pending = False
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
            if auto_start_pending and controller.snapshot().get("state") == "READY":
                controller.start(test_once=False)
                auto_start_pending = False
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
    return camera.capture().image


def _preflight_setup_camera(
    camera: SnapshotCamera,
    server_url: str,
    sleep_fn: Callable[[float], None],
) -> None:
    """Prove the field-tested D435 frame endpoint before JAKA access."""

    last_error = "unknown error"
    for attempt in range(3):
        try:
            camera.capture()
        except Exception as exc:
            last_error = str(exc)
        else:
            return
        if attempt < 2:
            sleep_fn(0.2)
    raise RuntimeError(
        f"D435 {server_url} frame 预检连续 3 次失败: {last_error}"
    )


def _connect_setup_jaka(
    client: Any,
    arm_cfg: Mapping[str, Any],
    sleep_fn: Callable[[float], None],
) -> None:
    """Connect and require a fresh joint update before interactive teaching."""

    ip = str(arm_cfg["ip"])
    port = int(arm_cfg.get("port", 10001))
    timeout = float(arm_cfg.get("connect_timeout_s", 3.0))
    last_error = "unknown error"
    for attempt in range(1, 4):
        try:
            connected = bool(client.connect(timeout=timeout))
            fresh = connected and bool(client.wait_for_joint_state(timeout=2.0))
            if fresh:
                return
            last_error = str(getattr(client, "last_error", "") or "连接或新鲜关节状态握手失败")
        except Exception as exc:
            last_error = str(exc)
        client.disconnect()
        if attempt < 3:
            sleep_fn(0.5)
    raise RuntimeError(f"setup 无法连接 JAKA {ip}:{port}，3 次尝试失败: {last_error}")


def _sample_pose(client: Any, timeout_s: float = 3.0):
    samples: list[list[float]] = []
    tcp: list[float] | None = None
    for sample_index in range(3):
        if not client.wait_for_joint_state(timeout=timeout_s):
            snapshot = client.snapshot()
            detail = str(getattr(client, "last_error", "") or snapshot.get("error") or "").strip()
            raise RuntimeError(
                f"读取 JAKA 示教姿态第 {sample_index + 1}/3 次超时"
                f"（connected={snapshot.get('connected')}, "
                f"joint_seq={snapshot.get('joint_seq')}, "
                f"joint_age={snapshot.get('joint_status_age')}）"
                + (f": {detail}" if detail else "")
            )
        snapshot = client.snapshot()
        samples.append(_finite_joint(snapshot.get("joint"), "JAKA 示教关节角"))
        tcp = _finite_joint(snapshot.get("tcp"), "JAKA 示教 TCP")
    return samples, tcp


def _read_setup_pose(
    client: Any,
    arm_cfg: Mapping[str, Any],
    sleep_fn: Callable[[float], None],
) -> tuple[list[list[float]], list[float]]:
    """Read one taught pose through a short-lived, read-only JAKA session."""

    client.disconnect()
    try:
        _connect_setup_jaka(client, arm_cfg, sleep_fn)
        samples, tcp = _sample_pose(client)
        return samples, tcp
    finally:
        client.disconnect()


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
    sleep_fn: Callable[[float], None] = time.sleep,
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
    print(
        f"setup 证据目录: {setup.root}；已记录 {len(setup.poses)}/{len(POSE_NAMES)}；"
        f"恢复命令: ./arm_demo.sh --setup --resume {setup.root}"
    )
    if not setup.operator:
        setup.set_operator(input_fn("操作员姓名/编号: "))
    arm_cfg = config.get("jaka", {})
    cam_cfg = config.get("camera", {})
    server_url = str(cam_cfg["server_url"])
    camera = camera or SnapshotCamera(
        server_url, timeout_s=float(cam_cfg.get("timeout_s", 1.0))
    )
    _preflight_setup_camera(camera, server_url, sleep_fn)
    client = client or JakaClient(str(arm_cfg["ip"]), int(arm_cfg.get("port", 10001)))
    window = "Arm Demo Setup"
    window_created = False
    announced_name: str | None = None
    preview_error: str | None = None
    preview_delay_ms = max(
        30,
        int(round(1000.0 / max(float(cam_cfg.get("rate_hz", 10.0)), 1.0))),
    )
    try:
        cv.namedWindow(window, cv.WINDOW_NORMAL)
        window_created = True
        while len(setup.poses) < len(POSE_NAMES):
            name = POSE_NAMES[len(setup.poses)]
            if announced_name != name:
                print(
                    f"[{len(setup.poses) + 1}/{len(POSE_NAMES)}] {name}: "
                    f"摆好姿态后点击预览窗口，再按 Enter 登记；Q/Esc 保存草稿退出。",
                    flush=True,
                )
                announced_name = name
            try:
                image = _camera_frame(camera)
            except (OSError, RuntimeError, ValueError) as exc:
                detail = str(exc)
                if detail != preview_error:
                    print(
                        f"D435 预览暂时不可用: {detail}；"
                        f"仍停留在 {name}，正在自动重试。",
                        flush=True,
                    )
                    preview_error = detail
                key = cv.waitKey(preview_delay_ms) & 0xFF
                if key in (ord("q"), ord("Q"), 27):
                    return 2
                continue
            if preview_error is not None:
                print("D435 预览已恢复。", flush=True)
                preview_error = None
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
            key = cv.waitKey(preview_delay_ms) & 0xFF
            if key in (ord("q"), ord("Q"), 27):
                return 2
            if key not in (10, 13):
                continue
            print(f"正在读取 {name} 的 3 次 JAKA 关节状态...", flush=True)
            try:
                samples, tcp = _read_setup_pose(client, arm_cfg, sleep_fn)
                evidence_image = image.copy()
            except (OSError, RuntimeError, ValueError) as exc:
                print(
                    f"{name} 登记失败: {exc}；仍停留在 {name}，"
                    "请保持姿态并在预览窗口再次按 Enter。",
                    flush=True,
                )
                continue
            try:
                setup.record_pose(name, samples, tcp, evidence_image)
            except ValueError as exc:
                print(
                    f"{name} 登记失败: {exc}；仍停留在 {name}，"
                    "请保持姿态并在预览窗口再次按 Enter。",
                    flush=True,
                )
                continue
            print(
                f"已登记 {name}（{len(setup.poses)}/{len(POSE_NAMES)}）",
                flush=True,
            )
            announced_name = None
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


def _recover_startup_home(controller: Any, location: str) -> None:
    snapshot = controller.snapshot()
    state = snapshot.get("state")
    if state == "READY":
        return
    if state != "HOLD":
        raise RuntimeError(f"启动回家前状态异常: {state}")
    print(
        f"[3/4] 当前位置 {location}，正在沿已示教路径自动低速回 home_safe...",
        flush=True,
    )
    controller.recover_home()
    while True:
        snapshot = controller.snapshot()
        state = snapshot.get("state")
        if state == "READY":
            print("[3/4] 已自动回到 home_safe", flush=True)
            return
        if state == "ERROR":
            raise RuntimeError(
                "启动自动回家失败: "
                + (str(snapshot.get("error", "")).strip() or "未知错误")
            )
        if state != "RECOVERING":
            raise RuntimeError(f"启动自动回家状态异常: {state}")
        time.sleep(0.05)


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
        except KeyboardInterrupt:
            print("arm demo setup 已中止；已登记姿态仍保存在 setup 草稿中。", file=__import__("sys").stderr)
            return 130
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
        print("[1/4] 加载稻穗检测模型...", flush=True)
        vision.start()
        print("[2/4] 等待 D435 首帧与首次检测...", flush=True)
        deadline = time.monotonic() + float(camera_cfg.get("startup_wait_s", 5.0))
        while vision.latest() is None and time.monotonic() < deadline:
            if vision.fatal_error:
                raise RuntimeError(vision.fatal_error)
            time.sleep(0.02)
        if vision.latest() is None:
            raise RuntimeError(vision.health_error(1.0) or "相机/模型启动超时")
        print("[2/4] D435 与检测画面已就绪", flush=True)
        print("[3/4] 连接 JAKA 并识别当前已示教姿态...", flush=True)
        controller = ArmDemoController(config, setup)
        start_pose = controller.connect()
        if start_pose == "home_safe":
            print("[3/4] JAKA 已连接，当前位于 home_safe", flush=True)
        else:
            print(
                f"[3/4] JAKA 已连接，当前匹配已示教路径 {start_pose}",
                flush=True,
            )
            _recover_startup_home(controller, start_pose)
        if args.test_once:
            print("[4/4] 打开双画面；按 Space 后开始低速单轮测试", flush=True)
        else:
            print("[4/4] 打开双画面并自动开始两株循环观察", flush=True)
        ui = config.get("display", {})
        return run_display(
            controller,
            vision,
            Path(args.photos),
            test_once=args.test_once,
            auto_start=not args.test_once,
            fullscreen=bool(ui.get("fullscreen", True)),
            max_age_s=float(vision_cfg.get("max_age_s", 5.0)),
            canvas_size=(
                int(ui.get("width", 1600)),
                int(ui.get("height", 900)),
            ),
        )
    except KeyboardInterrupt:
        print("arm demo 已由 Ctrl+C 中止", file=__import__("sys").stderr)
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
