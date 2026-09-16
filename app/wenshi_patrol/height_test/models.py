"""Immutable, JSON-safe data types shared by the height-test pipeline.

The height test is deliberately independent from the legacy phenotype controller.
These types contain no hardware clients and can therefore be used by both the
live runner and an offline replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import numpy as np

from ..vision.detector import Detection


def _json_value(value: Any) -> Any:
    """Convert common NumPy/dataclass values into strict JSON primitives."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if isinstance(value, np.ndarray):
        return [_json_value(item) for item in value.tolist()]
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if hasattr(value, "to_dict"):
        return _json_value(value.to_dict())
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    return str(value)


def _finite_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if str(value).strip() == str(result) else None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


@dataclass(frozen=True)
class PlantSpec:
    """One registered plant, including plants intentionally excluded from tests."""

    plant_id: str
    tag_id: int | None
    region: str
    observation_group: str
    excluded_from_detection: bool = False
    slot_top_to_water_m: float | None = None
    # These metadata fields mirror the project YAML and are retained for setup
    # and reports without making them mandatory for callers constructing a spec.
    row: str = ""
    index: int = 0
    camera_side: str = ""

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlantSpec":
        return cls(
            plant_id=_text(value.get("plant_id")),
            tag_id=_optional_int(value.get("tag_id")),
            region=_text(value.get("region")),
            observation_group=_text(value.get("observation_group")),
            excluded_from_detection=bool(value.get("excluded_from_detection", False)),
            slot_top_to_water_m=_finite_or_none(value.get("slot_top_to_water_m")),
            row=_text(value.get("row")),
            index=_optional_int(value.get("index")) or 0,
            camera_side=_text(value.get("camera_side")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plant_id": self.plant_id,
            "tag_id": self.tag_id,
            "region": self.region,
            "observation_group": self.observation_group,
            "excluded_from_detection": self.excluded_from_detection,
            "slot_top_to_water_m": self.slot_top_to_water_m,
            "row": self.row,
            "index": self.index,
            "camera_side": self.camera_side,
        }


@dataclass(frozen=True)
class HeightTestConfig:
    """Validated project view for the 24 active plants and 16 stations."""

    plants: tuple[PlantSpec, ...]
    groups: Mapping[str, tuple[str, ...]]
    tag_family: str = "tag25h7"
    tag_size_m: float | None = None
    plant_model_path: str = ""
    panicle_model_path: str = ""
    quality: Mapping[str, Any] = field(default_factory=dict)
    motion: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "plants", tuple(self.plants))
        object.__setattr__(self, "groups", MappingProxyType({str(k): tuple(v) for k, v in self.groups.items()}))
        object.__setattr__(self, "quality", MappingProxyType(dict(self.quality)))
        object.__setattr__(self, "motion", MappingProxyType(dict(self.motion)))

    @property
    def active_plants(self) -> tuple[PlantSpec, ...]:
        return tuple(plant for plant in self.plants if not plant.excluded_from_detection)

    @property
    def excluded_plants(self) -> tuple[PlantSpec, ...]:
        return tuple(plant for plant in self.plants if plant.excluded_from_detection)

    @property
    def active_plant_ids(self) -> tuple[str, ...]:
        return tuple(plant.plant_id for plant in self.active_plants)

    @property
    def excluded_plant_ids(self) -> tuple[str, ...]:
        return tuple(plant.plant_id for plant in self.excluded_plants)

    def group_plant_ids(self, group_id: str) -> tuple[str, ...]:
        """Return only detectable plants assigned to a station.

        The right stations retain C-row IDs in the legacy registration table,
        but this API intentionally filters those records from execution.
        """
        return tuple(plant_id for plant_id in self.groups.get(str(group_id), ()) if plant_id in self.active_plant_ids)

    @classmethod
    def from_project(cls, config: Mapping[str, Any]) -> "HeightTestConfig":
        if not isinstance(config, Mapping):
            raise ValueError("project configuration must be a mapping")
        raw_plants = config.get("plants", ())
        if not isinstance(raw_plants, Sequence) or isinstance(raw_plants, (str, bytes)):
            raise ValueError("project configuration plants must be a list")
        plants = tuple(PlantSpec.from_dict(item) for item in raw_plants if isinstance(item, Mapping))
        if len({plant.plant_id for plant in plants}) != len(plants):
            raise ValueError("plant IDs must be unique")
        if len(plants) != 32:
            raise ValueError(f"project configuration must register 32 plants, got {len(plants)}")
        active = tuple(plant for plant in plants if not plant.excluded_from_detection)
        excluded = tuple(plant for plant in plants if plant.excluded_from_detection)
        if len(active) != 24 or len(excluded) != 8:
            raise ValueError("height test requires 24 active plants and 8 excluded C-row plants")
        if any(not plant.plant_id.startswith("C-") for plant in excluded):
            raise ValueError("only C-row plants may be excluded from detection")
        raw_groups = config.get("observation_groups", ())
        if not isinstance(raw_groups, Sequence) or isinstance(raw_groups, (str, bytes)):
            raise ValueError("project configuration observation_groups must be a list")
        groups: dict[str, tuple[str, ...]] = {}
        for item in raw_groups:
            if not isinstance(item, Mapping):
                continue
            group_id = _text(item.get("id", item.get("group_id")))
            if not group_id:
                raise ValueError("observation group ID must not be empty")
            ids = tuple(
                _text(item.get(key))
                for key in ("left_plant_id", "right_plant_id")
                if _text(item.get(key))
            )
            if group_id in groups:
                raise ValueError(f"duplicate observation group: {group_id}")
            groups[group_id] = ids
        expected = {f"left-{index:02d}" for index in range(1, 9)} | {f"right-{index:02d}" for index in range(1, 9)}
        if set(groups) != expected:
            raise ValueError("height test requires left-01..08 and right-01..08 observation groups")
        tag = config.get("april_tag", {})
        tag = tag if isinstance(tag, Mapping) else {}
        test = config.get("height_test", {})
        test = test if isinstance(test, Mapping) else {}
        models = test.get("models", {})
        models = models if isinstance(models, Mapping) else {}
        return cls(
            plants=plants,
            groups=groups,
            tag_family=_text(test.get("tag_family", tag.get("family", "tag25h7"))) or "tag25h7",
            tag_size_m=_finite_or_none(test.get("tag_size_m", tag.get("physical_size_m"))),
            plant_model_path=_text(models.get("rice_plant", test.get("plant_model_path", ""))),
            panicle_model_path=_text(models.get("panicle", test.get("panicle_model_path", ""))),
            quality=test.get("quality", {}) if isinstance(test.get("quality", {}), Mapping) else {},
            motion=test.get("motion", {}) if isinstance(test.get("motion", {}), Mapping) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plants": [_json_value(plant.to_dict()) for plant in self.plants],
            "groups": {group_id: list(plant_ids) for group_id, plant_ids in self.groups.items()},
            "tag_family": self.tag_family,
            "tag_size_m": self.tag_size_m,
            "plant_model_path": self.plant_model_path,
            "panicle_model_path": self.panicle_model_path,
            "quality": _json_value(self.quality),
            "motion": _json_value(self.motion),
        }


@dataclass(frozen=True)
class FramePacket:
    """One aligned RGB-D frame and the metadata needed to reproduce analysis."""

    color: np.ndarray
    depth: np.ndarray
    seq: int
    intrinsics: Mapping[str, Any]
    stamp: float | None = None
    profile: Mapping[str, Any] | None = None
    received_at: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.color, np.ndarray) or self.color.ndim != 3 or self.color.shape[2] not in (3, 4):
            raise ValueError("color must be an HxWx3 or HxWx4 ndarray")
        if not isinstance(self.depth, np.ndarray) or self.depth.ndim not in (2, 3):
            raise ValueError("depth must be an HxW ndarray")
        if self.color.shape[:2] != self.depth.shape[:2]:
            raise ValueError("color and depth dimensions must match")
        if isinstance(self.seq, bool) or int(self.seq) < 0:
            raise ValueError("seq must be a non-negative integer")
        if not isinstance(self.intrinsics, Mapping):
            raise ValueError("intrinsics must be a mapping")
        object.__setattr__(self, "seq", int(self.seq))
        object.__setattr__(self, "intrinsics", MappingProxyType(dict(self.intrinsics)))
        if self.profile is not None:
            object.__setattr__(self, "profile", MappingProxyType(dict(self.profile)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "stamp": _finite_or_none(self.stamp),
            "received_at": _finite_or_none(self.received_at),
            "intrinsics": _json_value(self.intrinsics),
            "profile": _json_value(self.profile),
            "color_shape": list(self.color.shape),
            "depth_shape": list(self.depth.shape),
            "depth_dtype": str(self.depth.dtype),
        }


@dataclass(frozen=True)
class DetectionBundle:
    """Outputs from the independent plant and panicle models."""

    plant_model: str
    panicle_model: str
    plant: tuple[Detection, ...] = ()
    panicle: tuple[Detection, ...] = ()
    overlay: np.ndarray | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def plant_detections(self) -> tuple[Detection, ...]:
        return self.plant

    @property
    def panicle_detections(self) -> tuple[Detection, ...]:
        return self.panicle

    def to_dict(self) -> dict[str, Any]:
        return {
            "plant_model": self.plant_model,
            "panicle_model": self.panicle_model,
            "plant": [_json_value(item) for item in self.plant],
            "panicle": [_json_value(item) for item in self.panicle],
            "metadata": _json_value(self.metadata),
        }


@dataclass(frozen=True)
class TagObservation:
    """AprilTag identity and pose in the camera frame."""

    tag_id: int
    corners: tuple[tuple[float, float], ...] = ()
    score: float | None = None
    rvec: tuple[float, float, float] | None = None
    tvec: tuple[float, float, float] | None = None
    pose_valid: bool = False
    family: str = "tag25h7"

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "tag_id": self.tag_id,
            "corners": self.corners,
            "score": self.score,
            "rvec": self.rvec,
            "tvec": self.tvec,
            "pose_valid": self.pose_valid,
            "family": self.family,
        })


@dataclass(frozen=True)
class MethodResult:
    """Result from one method, preserving failure reasons for auditability."""

    name: str
    value_m: float | None
    quality: str = "ok"
    diagnostic: bool = False
    reasons: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "value_m", _finite_or_none(self.value_m))
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "name": self.name,
            "value_m": self.value_m,
            "quality": self.quality,
            "diagnostic": self.diagnostic,
            "reasons": self.reasons,
            "details": self.details,
        })


@dataclass(frozen=True)
class ViewAnalysis:
    """All per-view methods and quality information for one plant."""

    methods: Mapping[str, MethodResult] = field(default_factory=dict)
    quality: str = "ok"
    reasons: tuple[str, ...] = ()
    tag: TagObservation | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "methods", MappingProxyType(dict(self.methods)))
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "methods": {name: result.to_dict() for name, result in self.methods.items()},
            "quality": self.quality,
            "reasons": self.reasons,
            "tag": self.tag.to_dict() if self.tag else None,
            "metadata": self.metadata,
        })


@dataclass(frozen=True)
class PlantHeightResult:
    """Fused per-plant height result with every method retained."""

    plant_id: str
    methods: Mapping[str, MethodResult] = field(default_factory=dict)
    candidate_method: str | None = None
    candidate_value_m: float | None = None
    quality: str = "needs_review"
    reasons: tuple[str, ...] = ()
    views: tuple[ViewAnalysis, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "methods", MappingProxyType(dict(self.methods)))
        object.__setattr__(self, "candidate_value_m", _finite_or_none(self.candidate_value_m))
        object.__setattr__(self, "reasons", tuple(str(item) for item in self.reasons))
        object.__setattr__(self, "views", tuple(self.views))

    def to_dict(self) -> dict[str, Any]:
        return _json_value({
            "plant_id": self.plant_id,
            "methods": {name: result.to_dict() for name, result in self.methods.items()},
            "candidate_method": self.candidate_method,
            "candidate_value_m": self.candidate_value_m,
            "quality": self.quality,
            "reasons": self.reasons,
            "views": [view.to_dict() for view in self.views],
        })

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)


__all__ = [
    "Detection",
    "DetectionBundle",
    "FramePacket",
    "HeightTestConfig",
    "MethodResult",
    "PlantHeightResult",
    "PlantSpec",
    "TagObservation",
    "ViewAnalysis",
]
