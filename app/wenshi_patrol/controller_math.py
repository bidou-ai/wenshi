"""Pure patrol-control calculations, kept separate for offline tests."""

from __future__ import annotations

import math
from typing import Any

import threading


FORWARD_TO_LM5 = 1
BACKWARD_TO_LM4 = -1


def reverse_motion_allowed(safety: dict) -> bool:
    """Allow reverse only when enabled and either verified or explicitly overridden."""
    reverse_enabled = bool(
        safety.get("reverse_motion_allowed", safety.get("reverse_motion_enabled", False))
    )
    rear_verified = bool(safety.get("rear_radar_verified", False))
    unverified_override = bool(safety.get("allow_unverified_reverse", False))
    return reverse_enabled and (rear_verified or unverified_override)


def route_camera_required(camera: dict, target_enabled: bool) -> bool:
    return bool(target_enabled) or bool(camera.get("required_for_route", False))


def reverse_distance_travelled(start_along_m: float, current_along_m: float) -> float:
    return max(0.0, float(start_along_m) - float(current_along_m))


class MotionOwner:
    """Exclusive owner token for JAKA/AGV task-level commands."""

    def __init__(self):
        self._lock = threading.Lock()
        self._owner: str | None = None

    def acquire(self, owner: str) -> bool:
        with self._lock:
            if self._owner is not None:
                return self._owner == owner
            self._owner = str(owner)
            return True

    def release(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._owner = None

    def assert_owner(self, owner: str) -> None:
        with self._lock:
            if self._owner != owner:
                raise RuntimeError(f"motion owner is {self._owner!r}, not {owner!r}")


def reverse_target_velocity(state: str, distance_remaining_m: float, configured_speed_mps: float, hard_limit_m: float) -> float:
    if state != "ALIGN_REVERSE":
        raise ValueError("negative velocity is only permitted in ALIGN_REVERSE")
    distance = float(distance_remaining_m)
    if distance <= 0.0 or distance > float(hard_limit_m):
        return 0.0
    return -min(abs(float(configured_speed_mps)), 0.05)


def unverified_reverse_override_active(safety: dict) -> bool:
    """Report the hazardous mode without implying that the rear radar works."""
    return reverse_motion_allowed(safety) and not bool(
        safety.get("rear_radar_verified", False)
    )


def normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def choose_initial_direction(
    x: float,
    lm4_x: float,
    lm5_x: float,
    endpoint_tolerance_m: float,
) -> int:
    """Leave an endpoint; from the middle, first initialize at the nearer endpoint."""
    distance_lm4 = abs(float(x) - float(lm4_x))
    distance_lm5 = abs(float(x) - float(lm5_x))
    if distance_lm4 <= endpoint_tolerance_m:
        return FORWARD_TO_LM5
    if distance_lm5 <= endpoint_tolerance_m:
        return BACKWARD_TO_LM4
    return BACKWARD_TO_LM4 if distance_lm4 <= distance_lm5 else FORWARD_TO_LM5


def endpoint_reached(
    x: float,
    direction: int,
    lm4_x: float,
    lm5_x: float,
    endpoint_tolerance_m: float,
) -> bool:
    if direction == FORWARD_TO_LM5:
        return float(x) <= float(lm5_x) + endpoint_tolerance_m
    return float(x) >= float(lm4_x) - endpoint_tolerance_m


def endpoint_distance(x: float, direction: int, lm4_x: float, lm5_x: float) -> float:
    """Longitudinal distance remaining to the active endpoint."""
    target = float(lm5_x) if direction == FORWARD_TO_LM5 else float(lm4_x)
    return abs(float(x) - target)


def endpoint_approach_speed(
    distance_m: float,
    cruise_speed_mps: float,
    slowdown_distance_m: float,
    stop_distance_m: float,
    minimum_speed_mps: float,
) -> float:
    """Linearly reduce speed between the slowdown and stop distances."""
    cruise = abs(float(cruise_speed_mps))
    minimum = min(abs(float(minimum_speed_mps)), cruise)
    slowdown = max(float(slowdown_distance_m), float(stop_distance_m))
    distance = max(float(distance_m), float(stop_distance_m))
    if distance >= slowdown or slowdown <= float(stop_distance_m):
        return cruise
    ratio = (distance - float(stop_distance_m)) / (slowdown - float(stop_distance_m))
    return minimum + (cruise - minimum) * clamp(ratio, 0.0, 1.0)


def slew_rate(current: float, target: float, rate_per_s: float, dt_s: float) -> float:
    """Move current toward target without changing faster than rate_per_s."""
    maximum_change = abs(float(rate_per_s)) * max(float(dt_s), 0.0)
    return float(current) + clamp(float(target) - float(current), -maximum_change, maximum_change)


def compute_velocity(
    status: dict[str, Any],
    direction: int,
    speed_mps: float,
    line_y: float,
    nominal_yaw: float,
    cross_track_gain: float,
    heading_gain: float,
    max_angular_speed: float,
    correction_threshold_m: float = 0.0,
) -> tuple[float, float, float, float]:
    """Return vx, w, cross-track error and heading error."""
    y = float(status["y"])
    yaw = float(status["angle"])
    sign = 1.0 if direction == FORWARD_TO_LM5 else -1.0
    vx = sign * abs(float(speed_mps))
    cross_track_error = y - float(line_y)
    correction_error = math.copysign(
        max(0.0, abs(cross_track_error) - abs(float(correction_threshold_m))),
        cross_track_error,
    )
    desired_yaw = normalize_angle(
        float(nominal_yaw) + sign * float(cross_track_gain) * correction_error
    )
    heading_error = normalize_angle(desired_yaw - yaw)
    w = clamp(
        float(heading_gain) * heading_error,
        -abs(float(max_angular_speed)),
        abs(float(max_angular_speed)),
    )
    return vx, w, cross_track_error, heading_error
