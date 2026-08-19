"""Route-following math for station-to-station patrols."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class Segment:
    start_name: str
    end_name: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]


@dataclass(frozen=True)
class SegmentProgress:
    segment_yaw: float
    length: float
    along_track: float
    remaining_along: float
    distance_to_goal: float
    cross_track: float
    heading_error: float
    desired_yaw: float


def normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def make_segments(
    stations: dict[str, tuple[float, float, float]],
    order: list[str],
    loop: bool = False,
) -> list[Segment]:
    if len(order) < 2:
        raise ValueError("route.station_order 至少需要两个站点")
    missing = [name for name in order if name not in stations]
    if missing:
        raise ValueError("地图缺少路线站点: " + ", ".join(missing))
    names = list(order)
    if loop:
        names.append(order[0])
    return [
        Segment(start_name=a, end_name=b, start=stations[a], end=stations[b])
        for a, b in zip(names, names[1:])
    ]


def segment_progress(
    status: dict[str, Any],
    segment: Segment,
    cross_track_gain: float,
    correction_threshold_m: float = 0.0,
) -> SegmentProgress:
    x = float(status["x"])
    y = float(status["y"])
    yaw = float(status["angle"])
    sx, sy, _ = segment.start
    ex, ey, _ = segment.end
    dx = ex - sx
    dy = ey - sy
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        raise ValueError(f"路线段长度过短: {segment.start_name}->{segment.end_name}")

    ux = dx / length
    uy = dy / length
    px = x - sx
    py = y - sy
    along = px * ux + py * uy
    cross = ux * py - uy * px
    remaining = max(0.0, length - along)
    distance_to_goal = math.hypot(ex - x, ey - y)
    segment_yaw = math.atan2(dy, dx)

    correction = math.copysign(
        max(0.0, abs(cross) - abs(float(correction_threshold_m))),
        cross,
    )
    desired_yaw = normalize_angle(segment_yaw - float(cross_track_gain) * correction)
    heading_error = normalize_angle(desired_yaw - yaw)
    return SegmentProgress(
        segment_yaw=segment_yaw,
        length=length,
        along_track=along,
        remaining_along=remaining,
        distance_to_goal=distance_to_goal,
        cross_track=cross,
        heading_error=heading_error,
        desired_yaw=desired_yaw,
    )


def endpoint_reached(
    status: dict[str, Any],
    segment: Segment,
    tolerance_m: float,
) -> bool:
    progress = segment_progress(status, segment, cross_track_gain=0.0)
    return (
        progress.distance_to_goal <= float(tolerance_m)
        and progress.along_track >= progress.length - float(tolerance_m)
    )


def endpoint_approach_speed(
    distance_m: float,
    cruise_speed_mps: float,
    slowdown_distance_m: float,
    stop_distance_m: float,
    minimum_speed_mps: float,
) -> float:
    cruise = abs(float(cruise_speed_mps))
    minimum = min(abs(float(minimum_speed_mps)), cruise)
    slowdown = max(float(slowdown_distance_m), float(stop_distance_m))
    distance = max(float(distance_m), float(stop_distance_m))
    if distance >= slowdown or slowdown <= float(stop_distance_m):
        return cruise
    ratio = (distance - float(stop_distance_m)) / (slowdown - float(stop_distance_m))
    return minimum + (cruise - minimum) * clamp(ratio, 0.0, 1.0)


def compute_segment_velocity(
    status: dict[str, Any],
    segment: Segment,
    speed_mps: float,
    cross_track_gain: float,
    heading_gain: float,
    max_angular_speed: float,
    correction_threshold_m: float,
    rotate_in_place_threshold_rad: float,
    heading_slowdown_threshold_rad: float,
    min_heading_scale: float,
) -> tuple[float, float, SegmentProgress]:
    progress = segment_progress(
        status,
        segment,
        cross_track_gain=cross_track_gain,
        correction_threshold_m=correction_threshold_m,
    )
    w = clamp(
        float(heading_gain) * progress.heading_error,
        -abs(float(max_angular_speed)),
        abs(float(max_angular_speed)),
    )

    heading_error = abs(progress.heading_error)
    rotate_threshold = abs(float(rotate_in_place_threshold_rad))
    slow_threshold = min(abs(float(heading_slowdown_threshold_rad)), rotate_threshold)
    if heading_error >= rotate_threshold:
        scale = 0.0
    elif heading_error <= slow_threshold:
        scale = 1.0
    else:
        span = max(rotate_threshold - slow_threshold, 1e-6)
        ratio = (rotate_threshold - heading_error) / span
        scale = float(min_heading_scale) + (1.0 - float(min_heading_scale)) * ratio
    return abs(float(speed_mps)) * clamp(scale, 0.0, 1.0), w, progress
