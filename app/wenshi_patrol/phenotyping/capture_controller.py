"""Injected-dependency orchestration for safe three-view plant capture."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Callable

import numpy as np


VIEWS = ("left", "center", "right")


@dataclass(frozen=True)
class CapturePolicy:
    burst_count: int = 3
    burst_max: int = 5
    max_retries_per_view: int = 3

    def __post_init__(self) -> None:
        if not 1 <= self.burst_count <= self.burst_max:
            raise ValueError("burst_count must be between 1 and burst_max")
        if self.burst_max < 1 or self.max_retries_per_view < 0:
            raise ValueError("burst limits are invalid")


@dataclass(frozen=True)
class CaptureReport:
    plant_id: str
    accepted_views: tuple[str, ...]
    missing_views: tuple[str, ...]
    retry_reasons: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.missing_views


def capture_plant_views(
    plant_id: str,
    arm: Any,
    camera: Any,
    store: Any,
    policy: CapturePolicy | None = None,
    *,
    base_is_stopped: Callable[[], bool] | None = None,
    tag_decision: Any,
    on_safe_for_route: Callable[[], bool] | None = None,
    observation_stop_plant_ids: tuple[str, str] | None = None,
    completed_plant_ids: tuple[str, ...] = (),
    interrupted: bool = False,
) -> CaptureReport:
    """Capture one plant, isolating view failures and restoring safe pose.

    The dependencies are deliberately duck-typed test doubles. This function
    has no imports of AGV, JAKA, ROS or legacy target controllers.
    """
    if not isinstance(plant_id, str) or not plant_id:
        raise ValueError("plant_id must be a non-empty string")
    policy = policy or CapturePolicy()
    _require_matched_tag(tag_decision)
    stopped = base_is_stopped or _base_stopped_from_arm(arm)
    _require_base_stopped(stopped, "arm motion")

    accepted: list[str] = []
    missing: list[str] = []
    reasons: dict[str, tuple[str, ...]] = {}
    hard_stop = False
    try:
        for view in VIEWS:
            view_reasons: list[str] = []
            try:
                _require_base_stopped(stopped, "arm motion")
                arm.move_to_view(view)
            except Exception as exc:
                hard_stop = True
                prefix = "arm_motion" if not isinstance(exc, RuntimeError) or "base must be stopped" not in str(exc) else "RuntimeError"
                detail = str(exc) if prefix == "RuntimeError" else f"{type(exc).__name__}: {exc}"
                view_reasons.append(f"{prefix}: {detail}")
                missing.extend(item for item in VIEWS[VIEWS.index(view):] if item not in missing)
                reasons[view] = tuple(view_reasons)
                try:
                    _stop_arm(arm)
                except RuntimeError as stop_error:
                    reasons[view] = tuple((*view_reasons, f"arm_stop: {stop_error}"))
                    raise stop_error from exc
                if isinstance(exc, RuntimeError) and "base must be stopped" in str(exc):
                    raise RuntimeError(str(exc)) from exc
                raise RuntimeError(f"arm motion failed before {view} capture") from exc
            captured = False
            for _attempt in range(policy.max_retries_per_view + 1):
                try:
                    _require_base_stopped(stopped, "capture")
                except RuntimeError as exc:
                    hard_stop = True
                    view_reasons.append(f"RuntimeError: {exc}")
                    missing.extend(item for item in VIEWS[VIEWS.index(view):] if item not in missing)
                    reasons[view] = tuple(view_reasons)
                    try:
                        _stop_arm(arm)
                    except RuntimeError as stop_error:
                        reasons[view] = tuple((*view_reasons, f"arm_stop: {stop_error}"))
                        raise stop_error from exc
                    raise
                try:
                    frames = list(camera.capture_burst(view, policy.burst_count))
                    frame = _best_frame(frames)
                    color, depth, metadata = _frame_parts(frame, plant_id, view)
                    store.save_capture(view, color, depth, metadata)
                    accepted.append(view)
                    captured = True
                    break
                except Exception as exc:  # isolate one plant view from the run
                    view_reasons.append(f"{type(exc).__name__}: {exc}")
            if not captured:
                missing.append(view)
            if view_reasons:
                reasons[view] = tuple(view_reasons)
    finally:
        if reasons:
            _persist_failures(store, reasons)
        if not hard_stop:
            arm.move_to_safe()
            _assert_safe_pose(arm)
        if not hard_stop and on_safe_for_route is not None and _may_resume_route(
            plant_id, observation_stop_plant_ids, completed_plant_ids, interrupted, not missing
        ):
            if on_safe_for_route() is not True:
                raise RuntimeError("route resume callback did not confirm success")

    return CaptureReport(plant_id, tuple(accepted), tuple(missing), reasons)


def _best_frame(frames: list[Any]) -> Any:
    if not frames:
        raise ValueError("camera returned an empty burst")
    return max(frames, key=_quality)


def _quality(frame: Any) -> float:
    value = getattr(frame, "quality", None)
    if value is None and isinstance(frame, dict):
        value = frame.get("quality", frame.get("score", 0.0))
        if isinstance(value, dict):
            value = value.get("score", 0.0)
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return score if math.isfinite(score) else float("-inf")


def _frame_parts(frame: Any, plant_id: str, view: str) -> tuple[np.ndarray, np.ndarray | None, dict[str, Any]]:
    if isinstance(frame, dict):
        color = frame.get("color")
        depth = frame.get("depth")
        metadata = dict(frame.get("metadata") or {})
        metadata["quality"] = _quality(frame)
    else:
        color = getattr(frame, "color", None)
        depth = getattr(frame, "depth", None)
        metadata = dict(getattr(frame, "metadata", {}) or {})
        metadata["quality"] = _quality(frame)
    if not isinstance(color, np.ndarray) or color.size == 0:
        raise ValueError("selected frame has no color image")
    if depth is not None and (not isinstance(depth, np.ndarray) or depth.size == 0):
        raise ValueError("selected frame has invalid depth image")
    metadata.update({"plant_id": plant_id, "view": view, "selection": "best_quality"})
    return color, depth, metadata


def _assert_safe_pose(arm: Any) -> None:
    checker = getattr(arm, "is_safe_pose", None)
    if checker is not None and not checker():
        raise RuntimeError("arm did not return to safe pose")


def _base_stopped_from_arm(arm: Any) -> Callable[[], bool]:
    checker = getattr(arm, "is_base_stopped", None)
    if callable(checker):
        return checker
    value = getattr(arm, "base_is_stopped", None)
    if value is not None:
        return lambda: bool(value)
    raise RuntimeError("base stop status is required before arm motion")


def _require_matched_tag(tag_decision: Any) -> None:
    if getattr(tag_decision, "status", None) != "matched" or not bool(
        getattr(tag_decision, "bindable", False)
    ):
        raise RuntimeError("a matched Tag decision is required before arm motion")


def _require_base_stopped(stopped: Callable[[], bool], operation: str) -> None:
    try:
        is_stopped = stopped()
    except Exception as exc:
        raise RuntimeError(f"base stop status is unavailable before {operation}") from exc
    if is_stopped is not True:
        raise RuntimeError(f"base must be stopped before {operation}")


def _stop_arm(arm: Any) -> None:
    for name in ("stop_motion", "stop", "emergency_stop"):
        stopper = getattr(arm, name, None)
        if callable(stopper):
            stopper()
            return
    raise RuntimeError("arm motion failed and no stop operation is available")


def _persist_failures(store: Any, reasons: dict[str, tuple[str, ...]]) -> None:
    updater = getattr(store, "update_review", None)
    if not callable(updater):
        raise RuntimeError("capture store must persist failure reasons through update_review")
    updater({"state": "needs_review", "failure_reasons": {key: list(value) for key, value in reasons.items()}})


def _may_resume_route(
    plant_id: str,
    observation_stop_plant_ids: tuple[str, str] | None,
    completed_plant_ids: tuple[str, ...],
    interrupted: bool,
    capture_complete: bool,
) -> bool:
    if interrupted:
        return True
    if not capture_complete:
        return False
    if observation_stop_plant_ids is None:
        return False
    if len(observation_stop_plant_ids) != 2 or plant_id not in observation_stop_plant_ids:
        raise ValueError("observation stop must name this plant and exactly one paired plant")
    return set(completed_plant_ids) == set(observation_stop_plant_ids)
