"""Immutable-ish evidence storage for height-test runs."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

import cv2
import numpy as np

from .capture import DetectionBundle
from .models import FramePacket, PlantHeightResult, ViewAnalysis


_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _safe_id(value: str, label: str) -> str:
    text = str(value)
    if not text or not _SAFE.fullmatch(text) or text in {".", ".."}:
        raise ValueError(f"invalid {label}: {value!r}")
    return text


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent), text=True)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HeightTestStore:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().resolve()
        if not self.path.name.startswith("run_"):
            raise ValueError("height-test storage must be a run_* directory")
        self.path.mkdir(parents=True, exist_ok=True)
        self.events_path = self.path / "events.jsonl"
        self._lock = threading.Lock()
        (self.path / "stations").mkdir(exist_ok=True)

    @classmethod
    def create(cls, root: Path, config_snapshot: dict[str, Any] | None = None) -> "HeightTestStore":
        parent = Path(root).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            path = parent / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
            try:
                path.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError("cannot allocate a height-test run directory")
        store = cls(path)
        _atomic_json(path / "run.json", {"run_id": path.name, "created_at": _now(), "status": "running", "config": config_snapshot or {}})
        return store

    def append_event(self, event: str, **values: Any) -> None:
        record = {"time": _now(), "event": str(event), **values}
        with self._lock, self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")

    def write_setup_snapshot(self, value: Any) -> Path:
        path = self.path / "setup_snapshot.json"
        _atomic_json(path, value.to_dict() if hasattr(value, "to_dict") else value)
        return path

    def write_station(self, group_id: str, value: Any) -> Path:
        group_id = _safe_id(group_id, "group_id")
        path = self.path / "stations" / f"{group_id}.json"
        _atomic_json(path, value.to_dict() if hasattr(value, "to_dict") else value)
        return path

    def save_view(self, group_id: str, plant_id: str, view: str, packet: FramePacket, detections: DetectionBundle, analysis: ViewAnalysis) -> Path:
        group_id, plant_id, view = _safe_id(group_id, "group_id"), _safe_id(plant_id, "plant_id"), _safe_id(view, "view")
        target = self.path / "plants" / plant_id / "views" / view
        target.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target / "color.jpg"), packet.color, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"failed to write {target / 'color.jpg'}")
        if not cv2.imwrite(str(target / "depth.png"), packet.depth):
            raise OSError(f"failed to write {target / 'depth.png'}")
        overlay = detections.overlay if detections.overlay is not None else packet.color
        if not cv2.imwrite(str(target / "overlay.jpg"), overlay, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"failed to write {target / 'overlay.jpg'}")
        depth_values = np.asarray(packet.depth).reshape(-1)
        valid_ratio = float(np.count_nonzero(np.isfinite(depth_values) & (depth_values > 0)) / max(depth_values.size, 1))
        _atomic_json(target / "frame.json", packet.to_dict() | {"group_id": group_id, "plant_id": plant_id, "view": view, "depth_valid_ratio": valid_ratio})
        _atomic_json(target / "detections.json", detections.to_dict() | {"analysis": analysis.to_dict()})
        self.append_event("view_saved", group_id=group_id, plant_id=plant_id, view=view, seq=packet.seq)
        return target

    def write_manual_points(self, plant_id: str, points: Any) -> Path:
        plant_id = _safe_id(plant_id, "plant_id")
        path = self.path / "plants" / plant_id / "manual.json"
        value = points.to_dict() if hasattr(points, "to_dict") else getattr(points, "__dict__", points)
        _atomic_json(path, value)
        return path

    def write_result(self, plant_id: str, result: PlantHeightResult) -> Path:
        plant_id = _safe_id(plant_id, "plant_id")
        path = self.path / "plants" / plant_id / "results.json"
        _atomic_json(path, result.to_dict())
        self.append_event("result_saved", plant_id=plant_id, quality=result.quality, candidate_m=result.candidate_value_m)
        return path

    def finish(self, status: str = "finished") -> None:
        path = self.path / "run.json"
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"run_id": self.path.name}
        value.update({"status": status, "finished_at": _now()})
        _atomic_json(path, value)


def load_store(run_dir: Path) -> HeightTestStore:
    path = Path(run_dir).expanduser().resolve()
    if not (path / "run.json").is_file():
        raise FileNotFoundError(f"height-test run.json not found: {path}")
    return HeightTestStore(path)


__all__ = ["HeightTestStore", "load_store"]
