"""Explainable plant-height geometry and review result helpers.

This module deliberately has no image, robot, or camera dependency.  A caller
provides a manually edited or upstream-generated 3-D stem/panicle path.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence, TypeAlias


Point3D: TypeAlias = Sequence[float]


def _finite_point(point: Point3D) -> tuple[float, float, float] | None:
    try:
        values = tuple(float(value) for value in point)
    except (TypeError, ValueError):
        return None
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        return None
    return values  # type: ignore[return-value]


def polyline_arc_length(points_3d: Sequence[Point3D]) -> float:
    """Return the 3-D arc length, rejecting malformed or non-finite paths."""
    if len(points_3d) < 2:
        raise ValueError("at least two 3-D points are required")
    points = [_finite_point(point) for point in points_3d]
    if any(point is None for point in points):
        raise ValueError("path contains a non-finite or invalid 3-D point")
    return sum(math.dist(first, second) for first, second in zip(points, points[1:]))  # type: ignore[arg-type]


def resolve_compensation(
    global_compensation_m: float | None,
    region_compensation_m: Mapping[str, float | None],
    plant_compensation_m: Mapping[str, float | None],
    plant_id: str,
    region: str,
) -> float | None:
    """Resolve water-surface compensation in plant, region, global order."""
    for value in (
        plant_compensation_m.get(plant_id),
        region_compensation_m.get(region),
        global_compensation_m,
    ):
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed) and parsed >= 0:
            return parsed
    return None


def _base_result(path_3d: Sequence[Point3D], compensation: float | None, quality: str, reasons: list[str]) -> dict[str, Any]:
    return {
        "trait": "plant_height",
        "unit": "m",
        "auto_value_m": None,
        "reviewed_value_m": None,
        "difference_m": None,
        "slot_to_water_offset_m": compensation,
        "path_length_from_slot_m": None,
        "visible_path_ratio": None,
        "views_used": [],
        "quality": quality,
        "reasons": reasons,
        "auto_path_3d": _json_safe_path(path_3d),
        "reviewed_path_3d": [],
        "reviewed_by": None,
        "reviewed_at": None,
    }


def compute_height_candidate(
    path_3d: Sequence[Point3D],
    slot_to_water_offset_m: float | None,
    quality: Mapping[str, Any] | None,
    *,
    min_height_m: float = 0.0,
    max_height_m: float = 3.5,
) -> dict[str, Any]:
    """Compute a candidate height and explain why it needs human review."""
    metadata = dict(quality or {})
    reasons: list[str] = []
    for key, reason in (("occluded", "occlusion"), ("path_ambiguous", "ambiguous_path")):
        if metadata.get(key):
            reasons.append(reason)

    try:
        path_length = polyline_arc_length(path_3d)
    except ValueError:
        path_length = None
        reasons.append("insufficient_depth")
    compensation = _finite_nonnegative(slot_to_water_offset_m)
    if compensation is None:
        reasons.append("missing_compensation")
    if metadata.get("visible_path_ratio") is not None:
        ratio = _finite_number(metadata["visible_path_ratio"])
    else:
        ratio = None
    result = _base_result(path_3d, compensation, "needs_review", reasons)
    result["path_length_from_slot_m"] = path_length
    result["visible_path_ratio"] = ratio
    result["views_used"] = list(metadata.get("views_used") or [])
    if ratio is not None and not 0 <= ratio <= 1:
        result["reasons"].append("invalid_visible_path_ratio")
    if path_length is None or compensation is None:
        return result
    value = path_length + compensation
    if not math.isfinite(value) or not min_height_m <= value <= max_height_m:
        result["reasons"].append("out_of_range")
        return result
    if result["reasons"]:
        return result
    result["auto_value_m"] = value
    result["quality"] = "ok"
    return result


def recompute_reviewed_height(
    reviewed_path_3d: Sequence[Point3D],
    compensation: float | None,
    automatic: Mapping[str, Any] | None = None,
    reviewed_by: str | None = None,
    *,
    min_height_m: float = 0.0,
    max_height_m: float = 3.5,
) -> dict[str, Any]:
    """Recompute the manually edited path while preserving automatic evidence."""
    result = dict(automatic or {})
    result.setdefault("trait", "plant_height")
    result.setdefault("unit", "m")
    result.setdefault("auto_value_m", None)
    result.setdefault("auto_path_3d", [])
    result["reviewed_path_3d"] = _json_safe_path(reviewed_path_3d)
    result["reviewed_value_m"] = None
    result["difference_m"] = None
    result["reviewed_by"] = reviewed_by
    result["reviewed_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    offset = _finite_nonnegative(compensation)
    result["slot_to_water_offset_m"] = offset
    try:
        length = polyline_arc_length(reviewed_path_3d)
    except ValueError:
        result.setdefault("reasons", []).append("insufficient_depth")
        result["quality"] = "needs_review"
    else:
        if offset is None:
            result.setdefault("reasons", []).append("missing_compensation")
            result["quality"] = "needs_review"
        else:
            reviewed_value = length + offset
            if not math.isfinite(reviewed_value) or not min_height_m <= reviewed_value <= max_height_m:
                result.setdefault("reasons", []).append("out_of_range")
                result["quality"] = "needs_review"
            else:
                result["reviewed_value_m"] = reviewed_value
    automatic_value = _finite_number(result.get("auto_value_m"))
    reviewed_value = _finite_number(result.get("reviewed_value_m"))
    if automatic_value is not None and reviewed_value is not None:
        result["difference_m"] = reviewed_value - automatic_value
    return result


def _finite_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _finite_nonnegative(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _json_safe_path(path_3d: Sequence[Point3D]) -> list[list[float | None]]:
    """Keep invalid samples traceable without emitting NaN/Infinity JSON."""
    safe: list[list[float | None]] = []
    for point in path_3d:
        try:
            values = list(point)
        except TypeError:
            safe.append([None, None, None])
            continue
        safe.append([_finite_number(values[index]) if index < len(values) else None for index in range(3)])
    return safe
