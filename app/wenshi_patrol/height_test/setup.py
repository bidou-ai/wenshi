"""Field setup registry for tags, water offsets, stations and viewpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np

from .models import HeightTestConfig, TagObservation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SetupSession:
    def __init__(self, root: Path, config: HeightTestConfig):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.tags: dict[str, dict[str, Any]] = {}
        self.stations: dict[str, dict[str, Any]] = {}
        self.water_offsets: dict[str, float] = {}
        self.viewpoints: dict[str, dict[str, Any]] = {}
        self.operator = ""
        self.software_version = "height_test_v1"

    @classmethod
    def begin(cls, root: Path, config: HeightTestConfig) -> "SetupSession":
        return cls(root, config)

    def record_tag(self, plant_id: str, detection: TagObservation, orientation: str = "side", photo: np.ndarray | None = None) -> None:
        plant = next((item for item in self.config.plants if item.plant_id == plant_id), None)
        if plant is None:
            raise ValueError(f"unknown plant: {plant_id}")
        if plant_id in self.tags:
            raise ValueError(f"duplicate tag registration for plant {plant_id}")
        if any(int(value["tag_id"]) == int(detection.tag_id) for value in self.tags.values()):
            raise ValueError(f"duplicate tag ID: {detection.tag_id}")
        if self.config.tag_family != detection.family:
            raise ValueError(f"tag family mismatch: expected {self.config.tag_family}")
        if orientation not in {"side", "upward"}:
            raise ValueError("orientation must be side or upward")
        record = {"tag_id": int(detection.tag_id), "family": detection.family, "orientation": orientation, "detection": detection.to_dict(), "recorded_at": _now()}
        self.tags[plant_id] = record
        if photo is not None:
            if not isinstance(photo, np.ndarray) or photo.size == 0:
                raise ValueError("tag photo must be a non-empty image")
            path = self.root / "tags" / f"{plant_id}.jpg"; path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), photo):
                raise OSError(f"failed to write tag photo: {path}")
            record["photo"] = str(path.relative_to(self.root))

    def record_station(self, group_id: str, pose: Mapping[str, float], route_segment: str | None = None) -> None:
        if group_id not in self.config.groups:
            raise ValueError(f"unknown observation group: {group_id}")
        if group_id in self.stations:
            raise ValueError(f"duplicate station: {group_id}")
        if not isinstance(pose, Mapping) or not pose:
            raise ValueError("station pose must be a non-empty mapping")
        values = {str(key): float(value) for key, value in pose.items()}
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("station pose values must be finite")
        self.stations[group_id] = {"group_id": group_id, "pose": values, "route_segment": route_segment, "plant_ids": list(self.config.group_plant_ids(group_id)), "recorded_at": _now()}

    def record_water_offset(self, plant_id: str, value_m: float) -> None:
        if plant_id not in self.config.active_plant_ids:
            raise ValueError("water offsets are required only for active plants")
        value = float(value_m)
        if not math.isfinite(value) or value < 0:
            raise ValueError("water offset must be finite and non-negative")
        self.water_offsets[plant_id] = value

    def record_viewpoint(self, name: str, joint: list[float] | tuple[float, ...], tcp: list[float] | tuple[float, ...] | None = None) -> None:
        if name not in {"home_safe", "left", "center", "right"}:
            raise ValueError("viewpoint name must be home_safe, left, center, or right")
        values = [float(item) for item in joint]
        if len(values) != 6 or not all(math.isfinite(item) for item in values):
            raise ValueError("viewpoint joint must contain six finite values")
        record: dict[str, Any] = {"joint": values, "recorded_at": _now()}
        if tcp is not None:
            tcp_values = [float(item) for item in tcp]
            if len(tcp_values) != 6 or not all(math.isfinite(item) for item in tcp_values):
                raise ValueError("viewpoint tcp must contain six finite values")
            record["tcp"] = tcp_values
        self.viewpoints[name] = record

    def _validate(self) -> None:
        missing_tags = [plant_id for plant_id in self.config.active_plant_ids if plant_id not in self.tags]
        missing_offsets = [plant_id for plant_id in self.config.active_plant_ids if plant_id not in self.water_offsets]
        if missing_tags:
            raise ValueError("active plants missing Tag registration: " + ", ".join(missing_tags))
        if missing_offsets:
            raise ValueError("active plants missing water offsets: " + ", ".join(missing_offsets))
        expected = set(self.config.groups)
        if set(self.stations) != expected:
            raise ValueError(f"setup requires exactly {len(expected)} unique stations; got {len(self.stations)}")
        for group_id, station in self.stations.items():
            if tuple(station["plant_ids"]) != self.config.group_plant_ids(group_id):
                raise ValueError(f"station coverage mismatch: {group_id}")

    def publish(self, destination: Path) -> None:
        self._validate()
        destination = Path(destination).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        plants: dict[str, Any] = {}
        for plant in self.config.plants:
            value = plant.to_dict()
            value.update(self.tags.get(plant.plant_id, {}))
            value["water_offset_m"] = self.water_offsets.get(plant.plant_id)
            value["excluded_from_detection"] = plant.excluded_from_detection
            plants[plant.plant_id] = value
        payload = {"schema_version": 1, "published_at": _now(), "operator": self.operator, "software_version": self.software_version, "tag_family": self.config.tag_family, "tag_size_m": self.config.tag_size_m, "active_plants": list(self.config.active_plant_ids), "excluded_plants": list(self.config.excluded_plant_ids), "plants": plants, "stations": self.stations}
        if self.viewpoints:
            payload["viewpoints"] = self.viewpoints
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(destination)


__all__ = ["SetupSession"]
