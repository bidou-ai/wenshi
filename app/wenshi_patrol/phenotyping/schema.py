"""Data-only schema for phenotype observation configuration and plant records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True)
class PlantSpec:
    plant_id: str
    tag_id: int | None
    region: str
    row: str
    index: int
    observation_group: str
    camera_side: str
    slot_top_to_water_m: float | None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PlantSpec":
        return cls(
            plant_id=str(value.get("plant_id", "")),
            tag_id=_optional_int(value.get("tag_id")),
            region=str(value.get("region", "")),
            row=str(value.get("row", "")),
            index=_optional_int(value.get("index")) or 0,
            observation_group=str(value.get("observation_group", "")),
            camera_side=str(value.get("camera_side", "")),
            slot_top_to_water_m=_optional_float(value.get("slot_top_to_water_m")),
        )


@dataclass(frozen=True)
class ObservationGroup:
    group_id: str
    route_segment: str | None
    approximate_along_track_m: float | None
    slowdown_before_m: float | None
    trigger_distance_m: float | None
    left_plant_id: str | None
    right_plant_id: str | None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ObservationGroup":
        return cls(
            group_id=str(value.get("id", value.get("group_id", ""))),
            route_segment=_optional_text(value.get("route_segment")),
            approximate_along_track_m=_optional_float(value.get("approximate_along_track_m")),
            slowdown_before_m=_optional_float(value.get("slowdown_before_m")),
            trigger_distance_m=_optional_float(value.get("trigger_distance_m")),
            left_plant_id=_optional_text(value.get("left_plant_id")),
            right_plant_id=_optional_text(value.get("right_plant_id")),
        )


@dataclass(frozen=True)
class AprilTagConfig:
    family: str
    physical_size_m: float | None
    detector_backend: str | None
    mounting_orientation: str | None


@dataclass(frozen=True)
class ArmPostures:
    left: str | None
    center: str | None
    right: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "ArmPostures":
        data = value if isinstance(value, dict) else {}
        return cls(*(_optional_text(data.get(view)) for view in ("left", "center", "right")))


@dataclass(frozen=True)
class CalibrationEvidence:
    calibrated: bool
    evidence: str | None

    @classmethod
    def from_dict(cls, value: Any) -> "CalibrationEvidence":
        data = value if isinstance(value, dict) else {}
        return cls(data.get("calibrated") is True, _optional_text(data.get("evidence")))


@dataclass(frozen=True)
class PhenotypingConfig:
    enabled: bool
    traits: tuple[str, ...]
    processing_mode: str
    capture_burst_count: int
    capture_burst_max: int
    max_retries_per_view: int
    plants: tuple[PlantSpec, ...]
    observation_groups: tuple[ObservationGroup, ...]
    april_tag: AprilTagConfig
    arm_postures: ArmPostures
    camera_calibration: CalibrationEvidence
    water_compensation: CalibrationEvidence
    validation_errors: tuple[str, ...] = ()

    @property
    def formal_ready(self) -> bool:
        return self.enabled and not self.validation_errors


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if str(value).strip() == str(parsed) else None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def new_plant_record(plant: PlantSpec, run_id: str) -> dict[str, Any]:
    """Return a complete, JSON-serializable initial record for one plant."""
    return {
        "schema_version": 1,
        "run_id": run_id,
        "plant_id": plant.plant_id,
        "tag_id": plant.tag_id,
        "region": plant.region,
        "row": plant.row,
        "index": plant.index,
        "observation_group": plant.observation_group,
        "camera_side": plant.camera_side,
        "slot_top_to_water_m": plant.slot_top_to_water_m,
        "captures": {view: None for view in ("left", "center", "right")},
        "traits": {
            "plant_height": {
                "auto_value_m": None,
                "reviewed_value_m": None,
                "difference_m": None,
                "status": "pending",
            },
            "effective_panicle_count": {
                "automatic_value": None,
                "reviewed_value": None,
                "difference": None,
                "status": "pending",
            },
        },
        "review": {"status": "pending", "reasons": [], "events": []},
    }


def plant_to_dict(plant: PlantSpec) -> dict[str, Any]:
    return asdict(plant)
