"""Safe, resumable storage for one phenotyping run."""

from __future__ import annotations

from datetime import datetime, timezone
import csv
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any

import cv2
import numpy as np

from .capture_schema import (
    TRAITS,
    VIEWS,
    atomic_json_write,
    capture_frame_metadata,
    plant_defaults,
    validate_component,
    validate_view,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _atomic_image(path: Path, image: np.ndarray, params: list[int]) -> None:
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("cannot save an empty capture image")
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or ".tmp"
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", suffix=suffix, dir=str(path.parent))
    os.close(fd)
    temporary = Path(name)
    try:
        if not cv2.imwrite(str(temporary), image, params):
            raise OSError(f"failed to write capture image: {path}")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class PlantStore:
    def __init__(self, path: Path, plant_id: str):
        self.path = Path(path).resolve()
        self.plant_id = validate_component(plant_id, "plant id")
        self.path.mkdir(parents=True, exist_ok=True)
        self.captures_dir = self.path / "captures"
        self.traits_dir = self.path / "traits"
        self.captures_dir.mkdir(exist_ok=True)
        self.traits_dir.mkdir(exist_ok=True)
        self.plant_path = self.path / "plant.json"
        self.review_path = self.path / "review.json"
        if not self.plant_path.exists():
            atomic_json_write(self.plant_path, plant_defaults(self.plant_id))
        if not self.review_path.exists():
            atomic_json_write(self.review_path, {"state": "pending", "reasons": [], "updated_at": _now()})
        self._lock = threading.Lock()

    def metadata(self) -> dict[str, Any]:
        return _load_object(self.plant_path)

    def trait(self, name: str) -> dict[str, Any]:
        validate_component(name, "trait name")
        path = self.traits_dir / f"{name}.json"
        if not path.is_file():
            raise FileNotFoundError(name)
        return _load_object(path)

    def save_capture(self, view: str, color: np.ndarray, depth: np.ndarray | None, frame: dict[str, Any]) -> Path:
        validate_view(view)
        if depth is not None and (not isinstance(depth, np.ndarray) or depth.size == 0):
            raise ValueError("depth must be a non-empty ndarray or None")
        if depth is not None and depth.ndim != 2:
            raise ValueError("depth image must be two-dimensional")
        if not isinstance(color, np.ndarray) or color.size == 0:
            raise ValueError("color must be a non-empty ndarray")
        if depth is not None and color.shape[:2] != depth.shape[:2]:
            raise ValueError("color and depth dimensions must match")
        frame_metadata = capture_frame_metadata(view, color, depth, frame)
        view_dir = self.captures_dir / view
        with self._lock:
            _atomic_image(view_dir / "color.jpg", color, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if depth is not None:
                _atomic_image(view_dir / "depth.png", depth, [])
            elif (view_dir / "depth.png").exists():
                (view_dir / "depth.png").unlink()
            atomic_json_write(view_dir / "frame.json", frame_metadata)
            metadata = self.metadata()
            captures = dict(metadata.get("captures") or {})
            captures[view] = {"directory": f"captures/{view}", "frame_file": f"captures/{view}/frame.json", "status": "captured"}
            missing = [item for item in VIEWS if item not in captures]
            metadata.update({"captures": captures, "missing_views": missing, "status": "complete" if not missing else "partial", "updated_at": _now()})
            atomic_json_write(self.plant_path, metadata)
        return view_dir / "color.jpg"

    def write_trait(self, name: str, value: dict[str, Any]) -> None:
        validate_component(name, "trait name")
        if name not in TRAITS:
            raise ValueError(f"unsupported trait: {name}")
        if not isinstance(value, dict):
            raise ValueError("trait value must be an object")
        path = self.traits_dir / f"{name}.json"
        with self._lock:
            current = _load_object(path) if path.exists() else {}
            current.update(value)
            atomic_json_write(path, current)

    def update_review(self, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise ValueError("review value must be an object")
        with self._lock:
            current = _load_object(self.review_path)
            current.update(value)
            current["updated_at"] = _now()
            atomic_json_write(self.review_path, current)


class PhenotypingRunStore:
    CSV_HEADERS = {
        "agv.csv": ["time", "x", "y", "angle", "linear", "angular"],
        "jaka.csv": ["time", "joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "tcp"],
    }

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir).expanduser().resolve()
        if not self.run_dir.name.startswith("run_"):
            raise ValueError("phenotyping storage must be a run_ directory")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.plants_dir = self.run_dir / "plants"
        self.plants_dir.mkdir(exist_ok=True)
        self.run_path = self.run_dir / "run.json"
        self.events_path = self.run_dir / "events.jsonl"
        if not self.run_path.exists():
            atomic_json_write(self.run_path, {"run_id": self.run_dir.name, "created_at": _now(), "status": "running", "config_snapshot": {}})
        self.events_path.touch(exist_ok=True)
        for name, headers in self.CSV_HEADERS.items():
            path = self.run_dir / name
            if not path.exists() or path.stat().st_size == 0:
                with path.open("w", newline="", encoding="utf-8") as stream:
                    csv.writer(stream).writerow(headers)
        self._lock = threading.Lock()

    def append_event(self, event: str, **values: Any) -> None:
        record = {"time": _now(), "event": event, **values}
        with self._lock, self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")

    def plant(self, plant_id: str) -> PlantStore:
        validate_component(plant_id, "plant id")
        return PlantStore(self.plants_dir / plant_id, plant_id)


def create_phenotyping_run(root: Path, config_snapshot: dict[str, Any]) -> PhenotypingRunStore:
    if not isinstance(config_snapshot, dict):
        raise ValueError("config snapshot must be an object")
    parent = Path(root).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    for _ in range(100):
        run_dir = parent / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"unable to create run directory below {parent}")
    store = PhenotypingRunStore(run_dir)
    atomic_json_write(store.run_path, {"run_id": run_dir.name, "created_at": _now(), "status": "running", "config_snapshot": config_snapshot})
    return store
