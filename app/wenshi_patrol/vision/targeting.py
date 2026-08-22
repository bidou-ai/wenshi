"""Pure target selection and RGB-D depth helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np

from .detector import Detection


@dataclass(frozen=True)
class DepthSummary:
    median_m: float | None
    mad_m: float | None
    valid_ratio: float
    sample_count: int
    valid: bool


@dataclass(frozen=True)
class TargetCandidate:
    detection: Detection
    hit_count: int
    observations: int


def choose_center_target(detections: list[Detection], image_width: int, image_height: int) -> Detection | None:
    rice = [item for item in detections if item.class_name.lower() == "rice"]
    if not rice:
        return None
    center_x = float(image_width) / 2.0
    center_y = float(image_height) / 2.0
    return min(rice, key=lambda item: math.hypot(item.cx - center_x, item.cy - center_y))


def side_from_bbox(detection: Detection, image_width: int) -> str:
    return "left" if float(detection.cx) < float(image_width) / 2.0 else "right"


def _bbox_iou(first: Detection, second: Detection) -> float:
    first_box = (
        first.cx - first.width / 2.0,
        first.cy - first.height / 2.0,
        first.cx + first.width / 2.0,
        first.cy + first.height / 2.0,
    )
    second_box = (
        second.cx - second.width / 2.0,
        second.cy - second.height / 2.0,
        second.cx + second.width / 2.0,
        second.cy + second.height / 2.0,
    )
    intersection_width = max(0.0, min(first_box[2], second_box[2]) - max(first_box[0], second_box[0]))
    intersection_height = max(0.0, min(first_box[3], second_box[3]) - max(first_box[1], second_box[1]))
    intersection = intersection_width * intersection_height
    union = first.area + second.area - intersection
    return intersection / union if union > 0.0 else 0.0


def match_locked_detection(
    detections: list[Detection],
    previous: Detection,
    max_center_distance_ratio: float = 0.65,
) -> Detection | None:
    """Continue one plant bbox across frames without reusing screen side as identity."""
    rice = [item for item in detections if item.class_name.lower() == "rice"]
    scale = max(math.hypot(previous.width, previous.height), 1.0)
    maximum_distance = max(scale * max(float(max_center_distance_ratio), 0.05), 24.0)
    candidates: list[tuple[float, Detection]] = []
    for item in rice:
        distance = math.hypot(item.cx - previous.cx, item.cy - previous.cy)
        if distance > maximum_distance:
            continue
        width_ratio = max(item.width, previous.width) / max(min(item.width, previous.width), 1.0)
        height_ratio = max(item.height, previous.height) / max(min(item.height, previous.height), 1.0)
        if width_ratio > 2.0 or height_ratio > 2.0:
            continue
        size_delta = abs(math.log(max(item.area, 1.0) / max(previous.area, 1.0)))
        score = distance / scale + 0.20 * size_delta - 0.35 * _bbox_iou(item, previous)
        candidates.append((score, item))
    return min(candidates, key=lambda value: value[0])[1] if candidates else None


def _map_detection_to_depth(
    depth: np.ndarray,
    detection: Detection,
    source_size: tuple[int, int] | None,
) -> Detection:
    if source_size is None or not isinstance(depth, np.ndarray) or depth.ndim < 2:
        return detection
    source_width, source_height = (float(source_size[0]), float(source_size[1]))
    if source_width <= 0 or source_height <= 0:
        return detection
    scale_x = float(depth.shape[1]) / source_width
    scale_y = float(depth.shape[0]) / source_height
    return Detection(
        detection.cx * scale_x,
        detection.cy * scale_y,
        detection.width * scale_x,
        detection.height * scale_y,
        detection.confidence,
        detection.class_id,
        detection.class_name,
    )


def robust_bbox_depth(
    depth: np.ndarray,
    detection: Detection,
    sample_ratio: float = 0.35,
    depth_scale_m: float = 0.001,
    source_size: tuple[int, int] | None = None,
) -> DepthSummary:
    detection = _map_detection_to_depth(depth, detection, source_size)
    if not isinstance(depth, np.ndarray) or depth.ndim != 2 or depth.size == 0:
        return DepthSummary(None, None, 0.0, 0, False)
    half_w = max(float(detection.width) * float(sample_ratio) / 2.0, 1.0)
    half_h = max(float(detection.height) * float(sample_ratio) / 2.0, 1.0)
    x1 = max(0, int(round(detection.cx - half_w)))
    x2 = min(depth.shape[1], int(round(detection.cx + half_w)))
    y1 = max(0, int(round(detection.cy - half_h)))
    y2 = min(depth.shape[0], int(round(detection.cy + half_h)))
    if x2 <= x1 or y2 <= y1:
        return DepthSummary(None, None, 0.0, 0, False)
    region = depth[y1:y2, x1:x2]
    values = region.astype(np.float64).reshape(-1)
    valid = np.isfinite(values) & (values > 0)
    selected = values[valid] * float(depth_scale_m) if np.issubdtype(depth.dtype, np.integer) else values[valid]
    if selected.size == 0:
        return DepthSummary(None, None, 0.0, 0, False)
    median = float(np.median(selected))
    mad = float(np.median(np.abs(selected - median)))
    return DepthSummary(median, mad, float(selected.size / values.size), int(selected.size), True)


def depth_valid_for_detection(
    depth: np.ndarray,
    detection: Detection,
    min_valid_ratio: float = 0.10,
    source_size: tuple[int, int] | None = None,
) -> bool:
    """Require finite, non-zero depth in the central part of a target bbox."""
    summary = robust_bbox_depth(depth, detection, source_size=source_size)
    return bool(
        summary.valid
        and summary.median_m is not None
        and math.isfinite(float(summary.median_m))
        and summary.valid_ratio >= max(float(min_valid_ratio), 0.0)
    )


class StableTargetTracker:
    def __init__(self, window_size: int = 5, min_hits: int = 3, match_distance_ratio: float = 0.15):
        self.window_size = max(int(window_size), 1)
        self.min_hits = max(int(min_hits), 1)
        self.match_distance_ratio = max(float(match_distance_ratio), 0.01)
        self._history: deque[Detection | None] = deque(maxlen=self.window_size)

    def reset(self) -> None:
        self._history.clear()

    def observe(self, detections: list[Detection], image_width: int, image_height: int) -> TargetCandidate | None:
        current = choose_center_target(detections, image_width, image_height)
        self._history.append(current)
        if current is None:
            return None
        matched = []
        for item in self._history:
            if item is None:
                continue
            distance = math.hypot(item.cx - current.cx, item.cy - current.cy)
            scale = max(math.hypot(current.width, current.height), 1.0)
            if distance <= scale * self.match_distance_ratio:
                matched.append(item)
        if len(matched) < self.min_hits:
            return None
        selected = max(matched, key=lambda item: item.confidence)
        return TargetCandidate(selected, len(matched), len(self._history))
