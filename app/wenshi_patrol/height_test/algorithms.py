"""Hardware-free RGB-D height algorithms used by live capture and replay.

The methods intentionally remain small and explainable.  A failed method is
represented in the result instead of being silently discarded, which makes a
field run useful even when one view or one model is imperfect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Sequence

import cv2
import numpy as np

from ..vision.detector import Detection
from .models import FramePacket, MethodResult, PlantHeightResult, TagObservation, ViewAnalysis


@dataclass(frozen=True)
class ManualPoints:
    base: tuple[float, float] | None = None
    tip: tuple[float, float] | None = None
    path: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class AlgorithmParams:
    depth_scale: float = 0.001
    view_disagreement_m: float = 0.08
    min_depth_samples: int = 5
    min_height_m: float = 0.02
    max_height_m: float = 3.0
    hsv_green_min: tuple[int, int, int] = (20, 30, 20)
    hsv_green_max: tuple[int, int, int] = (100, 255, 255)


def _intrinsics(intrinsics: Mapping[str, Any]) -> np.ndarray:
    matrix = intrinsics.get("camera_matrix", intrinsics.get("K"))
    if matrix is not None:
        arr = np.asarray(matrix, dtype=np.float64)
        if arr.size == 9:
            return arr.reshape(3, 3)
    fx = float(intrinsics.get("fx", 1.0))
    fy = float(intrinsics.get("fy", fx))
    cx = float(intrinsics.get("cx", 0.0))
    cy = float(intrinsics.get("cy", 0.0))
    if not all(math.isfinite(value) and value > 0 for value in (fx, fy)):
        raise ValueError("camera intrinsics fx/fy must be finite and positive")
    return np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]], dtype=np.float64)


def _depth_m(depth: np.ndarray, px: float, py: float, scale: float, radius: int = 2) -> float | None:
    if not isinstance(depth, np.ndarray) or depth.ndim not in (2, 3):
        return None
    if not math.isfinite(float(px)) or not math.isfinite(float(py)):
        return None
    x, y = int(round(px)), int(round(py))
    h, w = depth.shape[:2]
    x0, x1 = max(0, x - radius), min(w, x + radius + 1)
    y0, y1 = max(0, y - radius), min(h, y + radius + 1)
    values = np.asarray(depth[y0:y1, x0:x1]).reshape(-1).astype(np.float64)
    values = values[np.isfinite(values) & (values > 0)]
    if values.size == 0:
        return None
    result = float(np.median(values)) * float(scale)
    return result if math.isfinite(result) and result > 0 else None


def depth_patch_m(depth: np.ndarray, px: float, py: float, depth_scale: float, radius: int = 2) -> float | None:
    """Return the median positive depth around a pixel in metres."""
    return _depth_m(depth, px, py, depth_scale, radius)


def _point(packet: FramePacket, px: float, py: float, params: AlgorithmParams) -> np.ndarray | None:
    scale_value = packet.profile.get("depth_scale") if packet.profile else packet.intrinsics.get("depth_scale", params.depth_scale)
    scale = float(scale_value if scale_value is not None else params.depth_scale)
    depth = _depth_m(packet.depth, px, py, scale)
    if depth is None:
        return None
    k = _intrinsics(packet.intrinsics)
    fx, fy, cx, cy = k[0, 0], k[1, 1], k[0, 2], k[1, 2]
    return np.array([(px - cx) * depth / fx, (py - cy) * depth / fy, depth], dtype=np.float64)


def solve_tag_pose(
    corners: Sequence[Sequence[float]],
    tag_size_m: float,
    intrinsics: Mapping[str, Any],
    distortion: Sequence[float] | None = None,
    *,
    tag_id: int = -1,
    score: float | None = None,
    family: str = "tag25h7",
) -> TagObservation:
    """Solve a tag pose using the four outer black-square corners."""
    if len(corners) != 4 or not math.isfinite(float(tag_size_m)) or float(tag_size_m) <= 0:
        raise ValueError("tag pose requires four corners and a positive tag size")
    image = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    half = float(tag_size_m) / 2.0
    object_points = np.array([[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]], dtype=np.float64)
    k = _intrinsics(intrinsics)
    if distortion is None or len(distortion) == 0:
        dist = np.zeros((5, 1), dtype=np.float64)
    else:
        dist = np.asarray(distortion, dtype=np.float64)
    ok, rvec, tvec = cv2.solvePnP(object_points, image, k, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    if not ok:
        ok, rvec, tvec = cv2.solvePnP(object_points, image, k, dist, flags=cv2.SOLVEPNP_ITERATIVE)
    return TagObservation(
        tag_id=int(tag_id),
        corners=tuple((float(x), float(y)) for x, y in image),
        score=score,
        rvec=tuple(float(value) for value in rvec.reshape(-1)[:3]),
        tvec=tuple(float(value) for value in tvec.reshape(-1)[:3]),
        pose_valid=bool(ok),
        family=family,
    )


def _method(name: str, value: float | None, *, quality: str = "ok", diagnostic: bool = False, reasons: Sequence[str] = (), details: Mapping[str, Any] | None = None) -> MethodResult:
    return MethodResult(name, value, quality, diagnostic, tuple(reasons), details or {})


def _box_top(box: Detection) -> tuple[float, float]:
    return float(box.cx), float(box.cy - box.height / 2.0)


def analyze_view(
    packet: FramePacket,
    plant_box: Detection | None,
    panicle_boxes: Sequence[Detection],
    tag: TagObservation | None,
    water_offset_m: float | None,
    manual: ManualPoints | None = None,
    params: AlgorithmParams | None = None,
) -> ViewAnalysis:
    params = params or AlgorithmParams()
    methods: dict[str, MethodResult] = {}
    reasons: list[str] = []
    highest = min(panicle_boxes, key=lambda item: item.cy - item.height / 2.0, default=None)

    # M0: image-space diagnostic only; it is deliberately never the candidate.
    if plant_box is not None and plant_box.height > 0:
        methods["bbox_pixel_height"] = _method("bbox_pixel_height", float(plant_box.height), diagnostic=True, details={"pixels": plant_box.height})
    else:
        methods["bbox_pixel_height"] = _method("bbox_pixel_height", None, quality="needs_review", diagnostic=True, reasons=("plant_box_missing",))

    # M1: tag/base to highest panicle top in 3-D.
    top = _box_top(highest) if highest is not None else None
    top_point = _point(packet, *top, params) if top else None
    base_point = np.asarray(tag.tvec, dtype=np.float64) if tag and tag.pose_valid and tag.tvec else None
    if top_point is None or base_point is None or water_offset_m is None:
        why = "missing_tag_or_panicle_depth" if top_point is None or base_point is None else "missing_water_offset"
        methods["tag_panicle_3d"] = _method("tag_panicle_3d", None, quality="needs_review", reasons=(why,))
    else:
        value = abs(float(top_point[1] - base_point[1])) + float(water_offset_m)
        methods["tag_panicle_3d"] = _method("tag_panicle_3d", value, details={"base": base_point.tolist(), "top": top_point.tolist()})

    # M2: robust depth envelope inside the plant ROI.
    if plant_box is None:
        methods["depth_roi_envelope"] = _method("depth_roi_envelope", None, quality="needs_review", reasons=("plant_box_missing",))
    else:
        x0 = max(0, int(plant_box.cx - plant_box.width / 2)); x1 = min(packet.depth.shape[1], int(plant_box.cx + plant_box.width / 2))
        y0 = max(0, int(plant_box.cy - plant_box.height / 2)); y1 = min(packet.depth.shape[0], int(plant_box.cy + plant_box.height / 2))
        scale_value = packet.profile.get("depth_scale") if packet.profile else packet.intrinsics.get("depth_scale", params.depth_scale)
        scale = float(scale_value if scale_value is not None else params.depth_scale)
        raw_values = packet.depth[y0:y1, x0:x1].astype(np.float64) * scale
        valid = np.isfinite(raw_values) & (raw_values > 0)
        ys_grid = np.arange(y0, y1, dtype=np.float64)[:, None]
        k = _intrinsics(packet.intrinsics)
        vertical = (ys_grid - k[1, 2]) * raw_values / k[1, 1]
        values = vertical[valid]
        if values.size < params.min_depth_samples:
            methods["depth_roi_envelope"] = _method("depth_roi_envelope", None, quality="needs_review", reasons=("insufficient_depth",), details={"valid_samples": int(values.size)})
        else:
            value = float(np.percentile(values, 95) - np.percentile(values, 5)) + float(water_offset_m or 0.0)
            methods["depth_roi_envelope"] = _method("depth_roi_envelope", value, details={"valid_samples": int(values.size)})

    # M3: green-mask envelope, kept as a diagnostic because occlusion is common.
    hsv = cv2.cvtColor(packet.color[..., :3], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.asarray(params.hsv_green_min, dtype=np.uint8), np.asarray(params.hsv_green_max, dtype=np.uint8))
    ys = np.where(mask > 0)[0]
    if ys.size < params.min_depth_samples:
        methods["mask_envelope"] = _method("mask_envelope", None, quality="needs_review", diagnostic=True, reasons=("insufficient_green_mask",))
    else:
        methods["mask_envelope"] = _method("mask_envelope", float(ys.max() - ys.min()) / max(packet.color.shape[0], 1), quality="ok", diagnostic=True)

    # M4: operator clicks, converted to 3-D when depth is available.
    if manual and manual.base and manual.tip:
        a, b = _point(packet, *manual.base, params), _point(packet, *manual.tip, params)
        if a is not None and b is not None:
            methods["manual_endpoints_3d"] = _method("manual_endpoints_3d", float(np.linalg.norm(b - a)), details={"base": a.tolist(), "tip": b.tolist()})
        else:
            methods["manual_endpoints_3d"] = _method("manual_endpoints_3d", None, quality="needs_review", reasons=("manual_depth_missing",))
    else:
        methods["manual_endpoints_3d"] = _method("manual_endpoints_3d", None, quality="needs_review", diagnostic=True, reasons=("manual_points_missing",))

    # M5: clicked stem path, a useful curved-length diagnostic.
    if manual and len(manual.path) >= 2:
        points = [_point(packet, *item, params) for item in manual.path]
        if all(item is not None for item in points):
            length = sum(float(np.linalg.norm(points[i] - points[i - 1])) for i in range(1, len(points)))
            methods["manual_stem_path_3d"] = _method("manual_stem_path_3d", length, diagnostic=True)
        else:
            methods["manual_stem_path_3d"] = _method("manual_stem_path_3d", None, quality="needs_review", diagnostic=True, reasons=("manual_path_depth_missing",))
    else:
        methods["manual_stem_path_3d"] = _method("manual_stem_path_3d", None, quality="needs_review", diagnostic=True, reasons=("manual_path_missing",))

    usable = [result.value_m for result in methods.values() if result.value_m is not None and not result.diagnostic]
    if not usable:
        reasons.append("no_usable_height_method")
    for result in methods.values():
        if result.quality != "ok" and result.reasons:
            reasons.extend(result.reasons)
    return ViewAnalysis(methods=methods, quality="ok" if usable else "needs_review", reasons=tuple(dict.fromkeys(reasons)), tag=tag, metadata={"panicle_count": len(panicle_boxes)})


def fuse_views(views: Sequence[ViewAnalysis], params: AlgorithmParams | None = None, plant_id: str = "") -> PlantHeightResult:
    params = params or AlgorithmParams()
    methods: dict[str, MethodResult] = {}
    reasons: list[str] = []
    for name in ("tag_panicle_3d", "depth_roi_envelope", "mask_envelope", "manual_endpoints_3d", "manual_stem_path_3d", "bbox_pixel_height"):
        values = [view.methods[name].value_m for view in views if name in view.methods and view.methods[name].value_m is not None]
        if values:
            methods[name] = _method(name, float(np.median(values)), diagnostic=all(view.methods[name].diagnostic for view in views if name in view.methods), details={"views": len(values), "min": min(values), "max": max(values)})
            if name in {"tag_panicle_3d", "depth_roi_envelope", "manual_endpoints_3d"} and len(values) > 1 and max(values) - min(values) > params.view_disagreement_m:
                reasons.append("view_disagreement")
        else:
            methods[name] = _method(name, None, quality="needs_review", diagnostic=name in {"bbox_pixel_height", "mask_envelope", "manual_stem_path_3d"}, reasons=("no_valid_view",))
    candidates = [(name, result.value_m) for name, result in methods.items() if result.value_m is not None and not result.diagnostic and name in {"tag_panicle_3d", "depth_roi_envelope", "manual_endpoints_3d"}]
    chosen_name, chosen_value = (candidates[0] if candidates else (None, None))
    if len(candidates) > 1 and max(value for _, value in candidates) - min(value for _, value in candidates) > params.view_disagreement_m:
        reasons.append("view_disagreement")
    if chosen_value is None:
        reasons.append("no_candidate")
    elif not params.min_height_m <= chosen_value <= params.max_height_m:
        reasons.append("height_out_of_range")
    quality = "ok" if chosen_value is not None and not reasons else "needs_review"
    return PlantHeightResult(plant_id=plant_id, methods=methods, candidate_method=chosen_name, candidate_value_m=chosen_value, quality=quality, reasons=tuple(dict.fromkeys(reasons)), views=tuple(views))


__all__ = ["AlgorithmParams", "ManualPoints", "analyze_view", "depth_patch_m", "fuse_views", "solve_tag_pose"]
