"""Field setup registry for tags, water offsets, stations and viewpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np

from .models import HeightTestConfig, TagObservation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def expected_field_tag_id(plant_id: str) -> int | None:
    """Return the fixed ID from the field's 1-32 installation layout.

    The calibration board is ID 0 and is intentionally not part of this map.
    """
    text = str(plant_id)
    prefix, _, raw_index = text.rpartition("-")
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return None
    if not 1 <= index <= 8:
        return None
    if prefix == "A":
        return index
    if prefix == "B-L":
        return 17 - index
    if prefix == "B-R":
        return 16 + index
    if prefix == "C":
        return 33 - index
    return None


class SetupSession:
    def __init__(self, root: Path, config: HeightTestConfig):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.tags: dict[str, dict[str, Any]] = {}
        self.stations: dict[str, dict[str, Any]] = {}
        self.water_offsets: dict[str, float] = {}
        self.viewpoints: dict[str, dict[str, Any]] = {}
        self.calibration_board: dict[str, Any] = {"tag_id": 0}
        self.require_photo_evidence = False
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
        if int(detection.tag_id) == 0:
            raise ValueError("Tag ID 0 is reserved for the calibration board, not a plant")
        expected = expected_field_tag_id(plant_id)
        if expected is not None and int(detection.tag_id) != expected:
            raise ValueError(f"{plant_id} 的现场 Tag 应为 {expected}，收到 {detection.tag_id}")
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

    def record_calibration_board(self, photo: np.ndarray | None = None, note: str | None = None) -> None:
        """Record the reserved ID-0 board separately from plant identities."""
        record: dict[str, Any] = {"tag_id": 0, "recorded_at": _now()}
        if note:
            record["note"] = str(note).strip()
        if photo is not None:
            if not isinstance(photo, np.ndarray) or photo.size == 0:
                raise ValueError("calibration board photo must be a non-empty image")
            path = self.root / "reference" / "tag-0-calibration-board.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), photo, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise OSError(f"failed to write calibration board photo: {path}")
            record["photo"] = str(path.relative_to(self.root))
        self.calibration_board = record

    def record_station(
        self,
        group_id: str,
        pose: Mapping[str, float],
        route_segment: str | None = None,
        photo: np.ndarray | None = None,
        note: str | None = None,
        source: str = "manual",
    ) -> None:
        if group_id not in self.config.groups:
            raise ValueError(f"unknown observation group: {group_id}")
        if group_id in self.stations:
            raise ValueError(f"duplicate station: {group_id}")
        if not isinstance(pose, Mapping) or not pose:
            raise ValueError("station pose must be a non-empty mapping")
        values = {str(key): float(value) for key, value in pose.items()}
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("station pose values must be finite")
        source = str(source).strip().lower() or "manual"
        if source not in {"agv_status", "manual"}:
            raise ValueError("station source must be agv_status or manual")
        record: dict[str, Any] = {"group_id": group_id, "pose": values, "route_segment": route_segment, "plant_ids": list(self.config.group_plant_ids(group_id)), "recorded_at": _now(), "pose_source": source}
        if note:
            record["note"] = str(note).strip()
        if photo is not None:
            if not isinstance(photo, np.ndarray) or photo.size == 0:
                raise ValueError("station photo must be a non-empty image")
            path = self.root / "stations" / f"{group_id}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), photo, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                raise OSError(f"failed to write station photo: {path}")
            record["photo"] = str(path.relative_to(self.root))
        self.stations[group_id] = record

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
        missing_tags = [plant.plant_id for plant in self.config.plants if plant.plant_id not in self.tags]
        missing_offsets = [plant_id for plant_id in self.config.active_plant_ids if plant_id not in self.water_offsets]
        if missing_tags:
            raise ValueError("plants missing Tag registration: " + ", ".join(missing_tags))
        if self.require_photo_evidence:
            if not self.calibration_board.get("photo"):
                raise ValueError("calibration board Tag 0 photo is required")
            missing_tag_photos = [plant_id for plant_id, record in self.tags.items() if not record.get("photo")]
            if missing_tag_photos:
                raise ValueError("Tag photos missing: " + ", ".join(missing_tag_photos))
        if missing_offsets:
            raise ValueError("active plants missing water offsets: " + ", ".join(missing_offsets))
        expected = set(self.config.groups)
        if set(self.stations) != expected:
            raise ValueError(f"setup requires exactly {len(expected)} unique stations; got {len(self.stations)}")
        for group_id, station in self.stations.items():
            if tuple(station["plant_ids"]) != self.config.group_plant_ids(group_id):
                raise ValueError(f"station coverage mismatch: {group_id}")
            if self.require_photo_evidence and not station.get("photo"):
                raise ValueError(f"station photo missing: {group_id}")
            if self.require_photo_evidence and station.get("pose_source") != "agv_status":
                raise ValueError(f"AGV实时位姿 required for station: {group_id}")

    def publish(self, destination: Path) -> None:
        self._validate()
        destination = Path(destination).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        def published_record(record: Mapping[str, Any]) -> dict[str, Any]:
            value = dict(record)
            photo = value.get("photo")
            if photo:
                source = (self.root / str(photo)).resolve()
                value["photo"] = os.path.relpath(source, destination.parent)
            return value

        plants: dict[str, Any] = {}
        for plant in self.config.plants:
            value = plant.to_dict()
            value.update(published_record(self.tags.get(plant.plant_id, {})))
            value["water_offset_m"] = self.water_offsets.get(plant.plant_id)
            value["excluded_from_detection"] = plant.excluded_from_detection
            plants[plant.plant_id] = value
        stations = {group_id: published_record(station) for group_id, station in self.stations.items()}
        payload = {"schema_version": 1, "published_at": _now(), "operator": self.operator, "software_version": self.software_version, "tag_family": self.config.tag_family, "tag_size_m": self.config.tag_size_m, "calibration_board": published_record(self.calibration_board), "active_plants": list(self.config.active_plant_ids), "excluded_plants": list(self.config.excluded_plant_ids), "plants": plants, "stations": stations}
        if self.viewpoints:
            payload["viewpoints"] = self.viewpoints
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(destination)


__all__ = ["SetupSession", "expected_field_tag_id"]
