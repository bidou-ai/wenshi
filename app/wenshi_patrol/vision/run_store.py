"""Run/target storage for the formal Wenshi patrol demo."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any

import cv2
import numpy as np

from .run_schema import target_metadata_defaults, now_iso


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent), text=True)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class TargetStore:
    def __init__(self, path: Path, target_id: str):
        self.path = Path(path).expanduser().resolve()
        self.target_id = target_id
        self.path.mkdir(parents=True, exist_ok=True)
        self.metadata_path = self.path / "metadata.json"
        if not self.metadata_path.exists():
            _atomic_json(self.metadata_path, target_metadata_defaults(target_id))
        self._lock = threading.Lock()

    def _write_image(self, name: str, image: np.ndarray, quality: int = 95) -> Path:
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise ValueError("cannot save an empty patrol image")
        output = self.path / name
        ok = cv2.imwrite(str(output), image, [cv2.IMWRITE_JPEG_QUALITY, max(80, min(int(quality), 100))])
        if not ok:
            raise OSError(f"failed to write patrol image: {output}")
        return output

    def metadata(self) -> dict[str, Any]:
        try:
            value = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid target metadata: {self.metadata_path}") from exc
        if not isinstance(value, dict):
            raise ValueError("target metadata must be an object")
        return value

    def write_metadata(self, metadata: dict[str, Any]) -> None:
        if not isinstance(metadata, dict):
            raise ValueError("target metadata must be an object")
        value = self.metadata()
        value.update(metadata)
        value["updated_at"] = now_iso()
        with self._lock:
            _atomic_json(self.metadata_path, value)

    def save_far(self, image: np.ndarray, metadata: dict[str, Any], quality: int = 95) -> Path:
        output = self._write_image("far.jpg", image, quality)
        self.write_metadata({"far": {"file": output.name, **metadata}, "status": "far_captured"})
        return output

    def save_near(self, image: np.ndarray, metadata: dict[str, Any], quality: int = 95) -> Path:
        output = self._write_image("near.jpg", image, quality)
        self.write_metadata({"near": {"file": output.name, **metadata}, "status": "near_captured"})
        return output


class PatrolRunStore:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir).expanduser().resolve()
        if not self.run_dir.name.startswith("run_"):
            raise ValueError("formal patrol storage must be a run_ directory")
        self.targets_dir = self.run_dir / "targets"
        self.events_path = self.run_dir / "events.jsonl"
        self.run_path = self.run_dir / "run.json"
        self.targets_dir.mkdir(parents=True, exist_ok=True)
        if not self.run_path.exists():
            _atomic_json(self.run_path, {"run_id": self.run_dir.name, "created_at": now_iso(), "status": "running"})
        self._lock = threading.Lock()

    @classmethod
    def create(cls, root: Path) -> "PatrolRunStore":
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
        store = cls(run_dir)
        _atomic_json(store.run_path, {"run_id": run_dir.name, "created_at": now_iso(), "status": "running"})
        return store

    def append_event(self, event: str, **values: Any) -> None:
        record = {"time": now_iso(), "event": event, **values}
        with self._lock, self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def create_target(self) -> TargetStore:
        existing = [path for path in self.targets_dir.iterdir() if path.is_dir() and path.name.startswith("T")]
        target_id = f"T{len(existing) + 1:04d}"
        target = TargetStore(self.targets_dir / target_id, target_id)
        self.append_event("target_created", target_id=target_id)
        return target

    def target(self, target_id: str) -> TargetStore:
        if not target_id.startswith("T") or "/" in target_id or ".." in target_id:
            raise ValueError("invalid target id")
        path = self.targets_dir / target_id
        if not path.is_dir():
            raise FileNotFoundError(target_id)
        return TargetStore(path, target_id)

    def finish(self, status: str = "finished") -> None:
        run = json.loads(self.run_path.read_text(encoding="utf-8")) if self.run_path.exists() else {}
        run.update({"status": status, "finished_at": now_iso()})
        _atomic_json(self.run_path, run)

    def reopen(self) -> None:
        run = json.loads(self.run_path.read_text(encoding="utf-8")) if self.run_path.exists() else {}
        run.pop("finished_at", None)
        run.update({"run_id": self.run_dir.name, "status": "running"})
        _atomic_json(self.run_path, run)
