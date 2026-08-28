"""Backend-neutral adapter for tag25h7 detections.

The adapter deliberately does not depend on an OpenCV AprilTag dictionary.
Detection libraries are optional and can be supplied as a callable backend,
which keeps offline tests and field deployment independent of one package's
availability.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import math
from typing import Any, Callable, Mapping

import numpy as np


class BackendUnavailableError(RuntimeError):
    """Raised when the selected optional detector backend cannot be loaded."""


class DuplicateTagError(ValueError):
    """Raised when one image contains more than one detection for an ID."""


@dataclass(frozen=True)
class TagDetection:
    """A normalized detector result in image coordinates."""

    tag_id: int
    corners: list[tuple[float, float]]
    score: float
    pose: dict[str, Any] | None = None


BackendFactory = Callable[[np.ndarray, str], Any]


class TagDetector:
    """Normalize results from a configurable tag25h7 detector backend."""

    SUPPORTED_FAMILY = "tag25h7"

    def __init__(
        self,
        family: str,
        backend: str,
        physical_size_m: float | None,
        *,
        backend_factory: BackendFactory | None = None,
        mounting_orientation: str = "unknown",
    ) -> None:
        if family != self.SUPPORTED_FAMILY:
            raise ValueError(
                f"unsupported tag family {family!r}; only {self.SUPPORTED_FAMILY!r} is supported"
            )
        if not backend or not backend.strip():
            raise ValueError("tag detector backend must be configured")
        if physical_size_m is not None and (
            not math.isfinite(float(physical_size_m)) or float(physical_size_m) <= 0
        ):
            raise ValueError("physical_size_m must be a positive finite number or None")
        if mounting_orientation not in {"unknown", "upward", "side"}:
            raise ValueError("mounting_orientation must be unknown, upward, or side")

        self.family = family
        self.backend = backend.strip()
        self.physical_size_m = physical_size_m
        self.backend_factory = backend_factory
        self.mounting_orientation = mounting_orientation

    def detect(self, color: np.ndarray) -> list[TagDetection]:
        if not isinstance(color, np.ndarray) or color.ndim not in (2, 3):
            raise TypeError("color must be a 2D or 3D numpy array")

        raw_detections = self._backend()(color, self.family)
        detections = [self._normalize(raw) for raw in (raw_detections or [])]
        seen: set[int] = set()
        for detection in detections:
            if detection.tag_id in seen:
                raise DuplicateTagError(
                    f"ambiguous duplicate detection for tag ID {detection.tag_id}"
                )
            seen.add(detection.tag_id)
        return sorted(detections, key=lambda item: item.score, reverse=True)

    def _backend(self) -> BackendFactory:
        if self.backend_factory is not None:
            return self.backend_factory

        if self.backend == "pupil_apriltags":
            try:
                module = importlib.import_module("pupil_apriltags")
            except ImportError as exc:
                raise BackendUnavailableError(
                    "backend 'pupil_apriltags' is unavailable; install it or inject a tested backend"
                ) from exc
            return _pupil_backend(module)

        if self.backend == "apriltag":
            try:
                module = importlib.import_module("apriltag")
            except ImportError as exc:
                raise BackendUnavailableError(
                    "backend 'apriltag' is unavailable; install it or inject a tested backend"
                ) from exc
            return _apriltag_backend(module)

        raise BackendUnavailableError(
            f"backend {self.backend!r} is unavailable; no backend factory was provided"
        )

    def _normalize(self, raw: TagDetection | Mapping[str, Any] | Any) -> TagDetection:
        if isinstance(raw, TagDetection):
            tag_id = raw.tag_id
            corners = raw.corners
            score = raw.score
            pose = dict(raw.pose) if raw.pose is not None else {}
        else:
            if isinstance(raw, Mapping):
                get = raw.get
            else:
                get = lambda name, default=None: getattr(raw, name, default)
            tag_id = get("tag_id", get("id"))
            corners = get("corners")
            score = get("score", get("decision_margin", 0.0))
            raw_pose = get("pose")
            pose = dict(raw_pose) if isinstance(raw_pose, Mapping) else {}

        if isinstance(tag_id, bool) or not isinstance(tag_id, (int, np.integer)):
            raise ValueError("tag detection ID must be an integer")
        normalized_corners = _normalize_corners(corners)
        try:
            normalized_score = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError("tag detection score must be numeric") from exc
        if not math.isfinite(normalized_score):
            raise ValueError("tag detection score must be finite")

        pose.setdefault("mounting_orientation", self.mounting_orientation)
        if self.physical_size_m is not None:
            pose.setdefault("physical_size_m", float(self.physical_size_m))
        return TagDetection(int(tag_id), normalized_corners, normalized_score, pose)


def match_expected_tag(
    detections: list[TagDetection], expected_tag_id: int | None
) -> dict[str, Any]:
    """Return an explicit identity decision without guessing a missing ID."""

    if expected_tag_id is None:
        return {"status": "unconfirmed", "expected_tag_id": None}
    if not detections:
        return {"status": "missing", "expected_tag_id": expected_tag_id}

    by_id: dict[int, TagDetection] = {}
    for detection in detections:
        if detection.tag_id in by_id:
            raise DuplicateTagError(
                f"ambiguous duplicate detection for tag ID {detection.tag_id}"
            )
        by_id[detection.tag_id] = detection

    if len(by_id) > 1:
        return {
            "status": "ambiguous",
            "expected_tag_id": expected_tag_id,
            "detected_tag_ids": sorted(by_id),
        }
    if expected_tag_id in by_id:
        return {
            "status": "matched",
            "expected_tag_id": expected_tag_id,
            "detection": by_id[expected_tag_id],
        }
    strongest = max(detections, key=lambda item: item.score)
    return {
        "status": "mismatched",
        "expected_tag_id": expected_tag_id,
        "detected_tag_id": strongest.tag_id,
        "detection": strongest,
    }


def _normalize_corners(corners: Any) -> list[tuple[float, float]]:
    if corners is None or len(corners) != 4:
        raise ValueError("tag detection must contain exactly four corners")
    result: list[tuple[float, float]] = []
    for corner in corners:
        if len(corner) != 2:
            raise ValueError("each tag corner must contain x and y")
        x, y = float(corner[0]), float(corner[1])
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("tag corners must be finite")
        result.append((x, y))
    return result


def _pupil_backend(module: Any) -> BackendFactory:
    detector = module.Detector(families="tag25h7")

    def detect(image: np.ndarray, _family: str) -> Any:
        gray = image if image.ndim == 2 else np.mean(image, axis=2).astype(np.uint8)
        return detector.detect(gray)

    return detect


def _apriltag_backend(module: Any) -> BackendFactory:
    detector = module.Detector(module.DetectorOptions(families="tag25h7"))

    def detect(image: np.ndarray, _family: str) -> Any:
        gray = image if image.ndim == 2 else np.mean(image, axis=2).astype(np.uint8)
        return detector.detect(gray)

    return detect
