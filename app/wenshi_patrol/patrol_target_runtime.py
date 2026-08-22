"""ROS-free target-task coordinator consumed by the patrol node and replay tests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from .vision.dedupe import DedupeRegistry, TargetKey
from .vision.detector import Detection
from .vision.targeting import StableTargetTracker, side_from_bbox


@dataclass(frozen=True)
class RuntimeConfig:
    stability_window: int = 5
    stability_min_hits: int = 3
    station_safety_band_m: float = 0.5
    dedupe_ttl_s: float = 7200.0
    neighbor_suppression_radius_m: float = 0.30


@dataclass(frozen=True)
class TargetEvent:
    kind: str
    side: str
    detection: Detection
    route_segment: str
    along_track_m: float
    reason: str = ""


class PatrolTargetRuntime:
    def __init__(self, config: RuntimeConfig = RuntimeConfig()):
        self.config = config
        self.tracker = StableTargetTracker(config.stability_window, config.stability_min_hits)
        self.dedupe = DedupeRegistry(config.dedupe_ttl_s, config.neighbor_suppression_radius_m)
        self.locked_side: str | None = None
        self.locked_key: TargetKey | None = None
        self._last_reset_request_id = ""

    def detection_allowed(self, segment: str, along_track_m: float, segment_length_m: float) -> bool:
        band = max(float(self.config.station_safety_band_m), 0.0)
        return float(along_track_m) >= band and float(along_track_m) <= float(segment_length_m) - band

    def reset_target(self) -> None:
        self.tracker.reset()
        self.locked_side = None
        self.locked_key = None

    def reject_current(self) -> None:
        if self.locked_key is not None:
            self.dedupe.forget(self.locked_key)
        self.reset_target()

    def apply_reset_marker(self, marker: Path) -> bool:
        try:
            value = json.loads(Path(marker).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        request_id = str(value.get("request_id", "")).strip() if isinstance(value, dict) else ""
        if not request_id or request_id == self._last_reset_request_id:
            return False
        self.dedupe.reset()
        self.reset_target()
        self._last_reset_request_id = request_id
        return True

    def observe(self, image: np.ndarray, detections: list[Detection], image_width: int, image_height: int, segment: str, along_track_m: float, loop_id: int, now: float, segment_length_m: float | None = None) -> TargetEvent | None:
        del image
        if self.locked_side is not None:
            return None
        if segment_length_m is not None and not self.detection_allowed(segment, along_track_m, segment_length_m):
            self.tracker.reset()
            return None
        candidate = self.tracker.observe(detections, image_width, image_height)
        if candidate is None:
            return None
        side = side_from_bbox(candidate.detection, image_width)
        key = TargetKey(segment, side, float(along_track_m))
        decision = self.dedupe.can_process(key, now, loop_id)
        if not decision.allowed:
            self.tracker.reset()
            return None
        self.locked_side = side
        self.locked_key = key
        self.dedupe.mark_selected(key, now, loop_id)
        self.tracker.reset()
        return TargetEvent("far_capture", side, candidate.detection, segment, float(along_track_m))
