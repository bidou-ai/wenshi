"""JAKA 机械臂控制器：扫描、固定抵近和示教回撤。"""

from __future__ import annotations

import threading
from typing import Any

from .config import load_viewpoints, require_joint_pose, resolve_config_path
from .fixed_approach import (
    SIDE_POSES,
    HOME_SAFE_POSE,
    plan_home_return,
    plan_teach_return,
)
from .jaka import JakaClient
from .target_follow import TargetFollowController


class ArmSweepWorker:
    def __init__(self, config: dict[str, Any], log):
        self.config = config
        arm = config["jaka"]
        self.arm_config = arm
        self.viewpoints_file = resolve_config_path(config, str(arm["viewpoints_file"]))
        self.center: list[float] = []
        self.left: list[float] = []
        self.right: list[float] = []
        self.fixed_poses: dict[str, list[float]] = {}
        self.home_safe: list[float] = []
        self.reload_viewpoints()
        self.move_speed = float(arm.get("move_to_camera_speed_deg_s", 20.0))
        self.center_tolerance = float(arm.get("center_tolerance_deg", 5.0))
        self.startup_pose_tolerance = float(
            arm.get("startup_pose_tolerance_deg", 5.0)
        )
        self.startup_joint_wait = float(arm.get("startup_joint_wait_s", 1.0))
        self.sweep_speed = float(arm.get("sweep_speed_deg_s", 20.0))
        self.accel = float(arm.get("accel_deg_s2", 40.0))
        self.transition_speed = float(arm.get("fixed_transition_speed_deg_s", 20.0))
        self.approach_speed = float(arm.get("fixed_approach_speed_deg_s", 12.0))
        self.photo_speed = float(arm.get("fixed_photo_speed_deg_s", 8.0))
        self.retract_speed = float(arm.get("fixed_retract_speed_deg_s", 12.0))
        self.joint_tolerance = float(arm.get("joint_tolerance_deg", 0.5))
        self.fixed_config = config.get("fixed_approach", config.get("fixed_demo", {}))
        self.photo_hold_s = float(self.fixed_config.get("photo_hold_s", 3.0))
        self.motion_timeout = float(arm.get("motion_timeout_s", 120.0))
        self.connect_timeout = float(arm.get("connect_timeout_s", 3.0))
        self.log = log
        self.client = JakaClient(
            ip=str(arm["ip"]),
            port=int(arm.get("port", 10001)),
            joint_tolerance_deg=float(arm.get("joint_tolerance_deg", 0.5)),
            command_interval_s=float(arm.get("command_interval_s", 0.1)),
            motion_start_wait_s=float(arm.get("motion_start_wait_s", 0.5)),
            motion_stall_timeout_s=float(arm.get("motion_stall_timeout_s", 10.0)),
            motion_progress_epsilon_deg=float(
                arm.get("motion_progress_epsilon_deg", 0.05)
            ),
            motion_progress_log_s=float(arm.get("motion_progress_log_s", 5.0)),
            log=log,
        )
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = "DISCONNECTED"
        self._error = ""
        self._sequence_side: str | None = None
        self._sequence_phase = ""
        self._sequence_completed = False
        self._follow_detection = None
        self._follow_image_width = 0
        self._follow_controller = TargetFollowController(
            gain=float(config.get("vision", {}).get("target_follow_gain", 0.8)),
            max_speed_deg_s=float(
                config.get("vision", {}).get("target_follow_max_speed_deg_s", 10.0)
            ),
            deadband_ratio=float(
                config.get("vision", {}).get("target_follow_deadband_ratio", 0.03)
            ),
        )

    def reload_viewpoints(self):
        viewpoints = load_viewpoints(self.config)
        self.center = require_joint_pose(
            viewpoints, str(self.arm_config.get("center_pose", "camera"))
        )
        self.left = require_joint_pose(
            viewpoints, str(self.arm_config.get("left_pose", "camera_left"))
        )
        self.right = require_joint_pose(
            viewpoints, str(self.arm_config.get("right_pose", "camera_right"))
        )
        self.fixed_poses = {}
        home_pose = viewpoints.get(HOME_SAFE_POSE)
        if isinstance(home_pose, dict) and isinstance(home_pose.get("joint"), list) and len(home_pose["joint"]) == 6:
            self.home_safe = [float(value) for value in home_pose["joint"]]
        for names in SIDE_POSES.values():
            for name in names:
                pose = viewpoints.get(name)
                joint = pose.get("joint") if isinstance(pose, dict) else None
                if isinstance(joint, list) and len(joint) == 6:
                    self.fixed_poses[name] = [float(value) for value in joint]

    def connect_and_check(self) -> tuple[bool, str]:
        if not self.client.connected and not self.client.connect(timeout=self.connect_timeout):
            return False, self.client.last_error or "JAKA 连接失败"
        if not self.client.wait_for_joint_state(timeout=self.startup_joint_wait):
            return False, "JAKA 已连接，但无法读取关节角；请确认已上电和使能"
        with self._lock:
            if self._state == "DISCONNECTED":
                self._state = "READY"
        return True, "ok"

    def quick_start_check(self) -> tuple[bool, str]:
        """Read the arm once and reject an unexpected non-J5 patrol pose."""
        ok, message = self.connect_and_check()
        if not ok:
            return ok, message
        joint = self.client.snapshot().get("joint")
        if not joint or len(joint) != 6:
            return False, "JAKA 关节状态不完整"

        checked_indices = (0, 1, 2, 3, 5)
        errors = {
            index: abs(float(joint[index]) - self.center[index])
            for index in checked_indices
        }
        worst = max(errors, key=errors.get)
        if errors[worst] > self.startup_pose_tolerance:
            return (
                False,
                f"机械臂不在巡视姿态: J{worst + 1}偏差={errors[worst]:.2f}deg "
                f"限制={self.startup_pose_tolerance:.2f}deg",
            )

        lower = min(self.left[4], self.right[4]) - self.startup_pose_tolerance
        upper = max(self.left[4], self.right[4]) + self.startup_pose_tolerance
        if not lower <= float(joint[4]) <= upper:
            return False, f"J5超出巡视范围: {float(joint[4]):.2f}deg"
        return True, "机械臂快速检查通过"

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False, "J5 扫动线程仍在运行"
            snapshot = self.client.snapshot()
            if not snapshot["connected"] or not snapshot["joint"]:
                return False, "JAKA 未连接或无关节状态"
            self._stop.clear()
            self._state = "STARTING"
            self._error = ""
            self._thread = threading.Thread(target=self._run, name="j5-sweep", daemon=True)
            self._thread.start()
        return True, "J5 启动中"

    def start_fixed_sequence(self, side: str, resume: bool = False) -> tuple[bool, str]:
        if not bool(self.fixed_config.get("enabled", False)):
            return False, "固定示教抵近未启用；请完成现场复核后修改 fixed_approach.enabled"
        if side not in SIDE_POSES:
            return False, f"未知目标侧: {side}"
        self.reload_viewpoints()
        missing = [name for name in SIDE_POSES[side] if name not in self.fixed_poses]
        if missing:
            return False, f"缺少示教点: {', '.join(missing)}"
        previous_phase = self._sequence_phase if resume else ""
        return self._start_action(
            target=lambda: self._run_fixed_sequence(side, previous_phase),
            thread_name=f"fixed-{side}",
            initial_state=f"FIXED_{side.upper()}_STARTING",
            side=side,
        )

    def start_retract(self, side: str) -> tuple[bool, str]:
        if side not in SIDE_POSES:
            return False, f"未知目标侧: {side}"
        self.reload_viewpoints()
        missing = [name for name in SIDE_POSES[side] if name not in self.fixed_poses]
        if missing:
            return False, f"缺少示教点: {', '.join(missing)}"
        phase = self._sequence_phase
        return self._start_action(
            target=lambda: self._run_retract(side, phase),
            thread_name=f"retract-{side}",
            initial_state=f"RETRACT_{side.upper()}_STARTING",
            side=side,
        )

    def start_teach_return(self, side: str) -> tuple[bool, str]:
        if side not in SIDE_POSES:
            return False, f"未知目标侧: {side}"
        self.reload_viewpoints()
        snapshot = self.client.snapshot()
        joint = snapshot.get("joint")
        if not joint or len(joint) != 6:
            return False, "JAKA 未连接或无完整关节状态"
        viewpoints = load_viewpoints(self.config)
        corridor = float(
            self.fixed_config.get("teach_return_corridor_deg", 15.0)
        )
        try:
            pose_names = plan_teach_return(viewpoints, side, joint, corridor)
        except ValueError as exc:
            return False, str(exc)
        ok, message = self._start_action(
            target=lambda: self._run_teach_return(side, pose_names),
            thread_name=f"teach-return-{side}",
            initial_state=f"TEACH_RETURN_{side.upper()}_STARTING",
            side=side,
        )
        if not ok:
            return False, message
        return True, "自动回撤路径: " + " -> ".join(pose_names)

    def start_centering(self) -> tuple[bool, str]:
        return self._start_action(
            target=self._run_centering,
            thread_name="arm-centering",
            initial_state="CENTERING_STARTING",
            side=None,
        )

    def start_home(self) -> tuple[bool, str]:
        self.reload_viewpoints()
        snapshot = self.client.snapshot()
        joint = snapshot.get("joint")
        if not joint or len(joint) != 6:
            return False, "JAKA 未连接或无完整关节状态"
        viewpoints = load_viewpoints(self.config)
        corridor = float(
            self.fixed_config.get("teach_return_corridor_deg", 15.0)
        )
        try:
            pose_names = plan_home_return(
                viewpoints,
                joint,
                corridor,
                self.startup_pose_tolerance,
                float(
                    self.fixed_config.get("home_nearby_tolerance_deg", 35.0)
                ),
                str(self.arm_config.get("center_pose", "camera")),
            )
        except ValueError as exc:
            return False, str(exc)
        targets = [(name, require_joint_pose(viewpoints, name)) for name in pose_names]
        ok, message = self._start_action(
            target=lambda: self._run_home(targets),
            thread_name="arm-home",
            initial_state="HOME_STARTING",
            side=None,
        )
        if not ok:
            return False, message
        return True, "goto home 路径: " + " -> ".join(pose_names)

    def _start_action(self, target, thread_name: str, initial_state: str, side: str | None):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False, "机械臂动作线程仍在运行"
            snapshot = self.client.snapshot()
            if not snapshot["connected"] or not snapshot["joint"]:
                return False, "JAKA 未连接或无关节状态"
            self._stop.clear()
            self._state = initial_state
            self._error = ""
            self._sequence_side = side
            self._sequence_completed = False
            self._thread = threading.Thread(target=target, name=thread_name, daemon=True)
            self._thread.start()
        return True, "机械臂动作已启动"

    def stop(self):
        self._stop.set()
        self.client.stop()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._lock:
            if self._state != "ERROR":
                self._state = "STOPPED"

    def start_target_follow(self, image_width: int) -> tuple[bool, str]:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False, "机械臂已有动作线程"
            snapshot = self.client.snapshot()
            if not snapshot["connected"] or not snapshot["joint"]:
                return False, "JAKA 未连接或无关节状态"
            self._follow_image_width = int(image_width)
            self._follow_detection = None
            self._stop.clear()
            self._state = "TARGET_FOLLOW"
            self._error = ""
            self._sequence_completed = False
            self._thread = threading.Thread(target=self._run_target_follow, name="j5-target-follow", daemon=True)
            self._thread.start()
        return True, "J5 目标跟随已启动"

    def update_target_follow(self, detection, image_width: int | None = None) -> None:
        with self._lock:
            self._follow_detection = detection
            if image_width is not None:
                self._follow_image_width = int(image_width)

    def stop_target_follow(self) -> None:
        self.stop()

    def _run_target_follow(self):
        while not self._stop.wait(0.10):
            with self._lock:
                detection = self._follow_detection
                width = self._follow_image_width
            if detection is None or width <= 0:
                continue
            snapshot = self.client.snapshot()
            joint = snapshot.get("joint")
            if not joint or len(joint) != 6:
                self._set_state("ERROR", "目标跟随时无有效关节状态")
                return
            command = self._follow_controller.update(detection, width, 0.10)
            if abs(command.speed_deg_s) <= 1e-6:
                continue
            target = list(joint)
            target[4] += command.speed_deg_s * 0.10
            if not self.client.joint_move(target, max(abs(command.speed_deg_s), 1.0), self.accel, timeout=1.0):
                if not self._stop.is_set():
                    self._set_state("ERROR", self.client.last_error or "J5 目标跟随失败")
                return
        self._set_state("STOPPED")

    def cleanup(self):
        self.stop()
        self.client.disconnect()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
            error = self._error
            sequence_side = self._sequence_side
            sequence_phase = self._sequence_phase
            sequence_completed = self._sequence_completed
        client = self.client.snapshot()
        return {
            "state": state,
            "error": error or str(client.get("error") or ""),
            "connected": client["connected"],
            "joint": client["joint"],
            "tcp": client["tcp"],
            "status_age": client["status_age"],
            "last_control_response": client.get("last_control_response"),
            "sequence_side": sequence_side,
            "sequence_phase": sequence_phase,
            "sequence_completed": sequence_completed,
        }

    def diagnostic_lines(self, snapshot: dict[str, Any] | None = None) -> list[str]:
        snapshot = snapshot or self.snapshot()
        lines: list[str] = []
        joint = snapshot.get("joint")
        if isinstance(joint, list) and len(joint) == 6:
            joint_text = ", ".join(
                f"J{index + 1}={float(value):.2f}" for index, value in enumerate(joint)
            )
            lines.append(f"JAKA关节: {joint_text} age={snapshot.get('status_age')}")
            errors = [abs(float(joint[index]) - self.center[index]) for index in range(6)]
            worst = max(range(6), key=errors.__getitem__)
            error_text = ", ".join(
                f"J{index + 1}={value:.2f}" for index, value in enumerate(errors)
            )
            nearby_limit = float(
                self.fixed_config.get("home_nearby_tolerance_deg", 35.0)
            )
            lines.append(
                "到camera偏差: "
                f"{error_text}; 最大=J{worst + 1} {errors[worst]:.2f}deg; "
                f"启动限制={self.startup_pose_tolerance:.2f}deg; "
                f"近端回家限制={nearby_limit:.2f}deg"
            )
        response = snapshot.get("last_control_response")
        if response:
            lines.append(f"JAKA最后响应: {response}")
        return lines

    def _set_state(self, state: str, error: str = ""):
        with self._lock:
            self._state = state
            self._error = error
        self.log(f"机械臂状态: {state}{': ' + error if error else ''}")

    def _run(self):
        joint = self.client.snapshot().get("joint")
        if not joint or len(joint) != 6:
            self._set_state("ERROR", "JAKA 关节状态不完整")
            return
        left_error = abs(float(joint[4]) - self.left[4])
        right_error = abs(float(joint[4]) - self.right[4])
        if left_error <= self.joint_tolerance:
            targets = (("RIGHT", self.right), ("LEFT", self.left))
        elif right_error <= self.joint_tolerance:
            targets = (("LEFT", self.left), ("RIGHT", self.right))
        elif left_error <= right_error:
            targets = (("LEFT", self.left), ("RIGHT", self.right))
        else:
            targets = (("RIGHT", self.right), ("LEFT", self.left))

        while not self._stop.is_set():
            for name, target in targets:
                if self._stop.is_set():
                    break
                self._set_state(f"SWEEPING_{name}")
                if not self.client.joint_move(
                    target,
                    self.sweep_speed,
                    self.accel,
                    self.motion_timeout,
                ):
                    if self._stop.is_set():
                        self._set_state("STOPPED")
                    else:
                        self._set_state("ERROR", self.client.last_error or "J5 扫动失败")
                    return
        self._set_state("STOPPED")

    def _move_sequence_pose(self, state: str, phase: str, target: list[float], speed: float) -> bool:
        self._sequence_phase = phase
        self._set_state(state)
        if self.client.joint_move(target, speed, self.accel, self.motion_timeout):
            return True
        if self._stop.is_set():
            self._set_state("STOPPED")
        else:
            self._set_state("ERROR", self.client.last_error or f"{state} 失败")
        return False

    def _run_fixed_sequence(self, side: str, resume_phase: str):
        entry_name, pre_name, photo_name = SIDE_POSES[side]
        entry = self.fixed_poses[entry_name]
        pre = self.fixed_poses[pre_name]
        photo = self.fixed_poses[photo_name]
        prefix = side.upper()

        if resume_phase == "RETRACT_ENTRY":
            steps = [(f"{prefix}_RETRACT_ENTRY", "RETRACT_ENTRY", entry, self.transition_speed)]
        elif resume_phase in {"MOVE_PRE", "MOVE_PHOTO", "PHOTO_HOLD", "RETRACT_PRE"}:
            steps = [
                (f"{prefix}_MOVE_PRE", "MOVE_PRE", pre, self.approach_speed),
                (f"{prefix}_MOVE_PHOTO", "MOVE_PHOTO", photo, self.photo_speed),
            ]
        else:
            steps = [
                (f"{prefix}_ALIGN_ENTRY", "ALIGN_ENTRY", entry, self.transition_speed),
                (f"{prefix}_MOVE_PRE", "MOVE_PRE", pre, self.approach_speed),
                (f"{prefix}_MOVE_PHOTO", "MOVE_PHOTO", photo, self.photo_speed),
            ]

        for state, phase, target, speed in steps:
            if not self._move_sequence_pose(state, phase, target, speed):
                return

        if resume_phase != "RETRACT_ENTRY":
            self._sequence_phase = "PHOTO_HOLD"
            self._set_state(f"{prefix}_PHOTO_HOLD")
            if self._stop.wait(self.photo_hold_s):
                self._set_state("STOPPED")
                return
            if not self._move_sequence_pose(
                f"{prefix}_RETRACT_PRE", "RETRACT_PRE", pre, self.retract_speed
            ):
                return
            if not self._move_sequence_pose(
                f"{prefix}_RETRACT_ENTRY", "RETRACT_ENTRY", entry, self.transition_speed
            ):
                return

        self._sequence_phase = "DONE"
        with self._lock:
            self._sequence_completed = True
        self._set_state(f"FIXED_{prefix}_DONE")

    def _run_retract(self, side: str, interrupted_phase: str):
        entry_name, pre_name, _photo_name = SIDE_POSES[side]
        entry = self.fixed_poses[entry_name]
        pre = self.fixed_poses[pre_name]
        prefix = side.upper()
        if interrupted_phase not in {"ALIGN_ENTRY", "RETRACT_ENTRY", "DONE", ""}:
            if not self._move_sequence_pose(
                f"{prefix}_RECOVER_PRE", "RETRACT_PRE", pre, self.retract_speed
            ):
                return
        if not self._move_sequence_pose(
            f"{prefix}_RECOVER_ENTRY", "RETRACT_ENTRY", entry, self.transition_speed
        ):
            return
        self._sequence_phase = "RECOVERED"
        with self._lock:
            self._sequence_completed = True
        self._set_state(f"RETRACT_{prefix}_DONE")

    def _run_teach_return(self, side: str, pose_names: list[str]):
        prefix = side.upper()
        for name in pose_names:
            phase = "TEACH_RETURN_ENTRY" if name == SIDE_POSES[side][0] else "TEACH_RETURN_PRE"
            if not self._move_sequence_pose(
                f"{prefix}_{phase}", phase, self.fixed_poses[name], self.retract_speed
            ):
                return
        self._sequence_phase = "TEACH_RETURN_DONE"
        with self._lock:
            self._sequence_completed = True
        self._set_state(f"TEACH_RETURN_{prefix}_DONE")

    def _run_centering(self):
        if self._move_sequence_pose(
            "CENTERING", "CENTERING", self.center, self.transition_speed
        ):
            self._sequence_phase = "CENTERED"
            with self._lock:
                self._sequence_completed = True
            self._set_state("CENTERING_DONE")

    def _run_home(self, targets: list[tuple[str, list[float]]]):
        for name, target in targets:
            phase = f"HOME_{name.upper()}"
            if not self._move_sequence_pose(phase, phase, target, self.retract_speed):
                return
        self._sequence_phase = "HOME_DONE"
        with self._lock:
            self._sequence_completed = True
        self._set_state("HOME_DONE")
