"""Pure guarded target-task state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .controller_math import reverse_target_velocity


TargetTaskState = Literal["CONFIRM", "FAR_CAPTURE", "ALIGN_REVERSE", "RELOCALIZE", "FIXED_APPROACH", "NEAR_CAPTURE", "RETRACT", "ABORT"]


@dataclass(frozen=True)
class TaskObservation:
    camera_age_s: float
    target_visible: bool
    distance_remaining_m: float
    j5_speed_deg_s: float
    depth_valid: bool = True
    agv_blocked: bool = False
    emergency: bool = False


@dataclass(frozen=True)
class TaskCommand:
    state: TargetTaskState
    reverse_velocity_mps: float = 0.0
    j5_speed_deg_s: float = 0.0
    stop: bool = False
    reason: str = ""


class TargetTask:
    def __init__(self, side: str, reverse_speed_mps: float = 0.05, reverse_limit_m: float = 0.6, camera_timeout_s: float = 2.0):
        if side not in {"left", "right"}:
            raise ValueError("target side must be left or right")
        self.side = side
        self.reverse_speed_mps = abs(float(reverse_speed_mps))
        self.reverse_limit_m = abs(float(reverse_limit_m))
        self.camera_timeout_s = max(float(camera_timeout_s), 0.1)
        self.state: TargetTaskState = "ALIGN_REVERSE"
        self.reason = ""

    def tick(self, observation: TaskObservation) -> TaskCommand:
        if self.state == "ABORT":
            return TaskCommand(self.state, stop=True, reason=self.reason)
        if observation.camera_age_s > self.camera_timeout_s:
            return self._abort("camera_stale")
        if not observation.target_visible:
            return self._abort("target_lost")
        if not observation.depth_valid:
            return self._abort("depth_invalid")
        if observation.agv_blocked or observation.emergency:
            return self._abort("agv_safety")
        if self.state == "ALIGN_REVERSE":
            velocity = reverse_target_velocity(self.state, observation.distance_remaining_m, self.reverse_speed_mps, self.reverse_limit_m)
            if velocity == 0.0:
                self.state = "RELOCALIZE"
                return TaskCommand(self.state, stop=True, reason="alignment_distance_reached")
            return TaskCommand(self.state, reverse_velocity_mps=velocity, j5_speed_deg_s=observation.j5_speed_deg_s)
        return TaskCommand(self.state, stop=True)

    def _abort(self, reason: str) -> TaskCommand:
        self.state = "ABORT"
        self.reason = reason
        return TaskCommand(self.state, stop=True, reason=reason)

    def stop(self, reason: str) -> None:
        self.state = "ABORT"
        self.reason = str(reason)
