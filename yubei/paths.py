"""Filesystem primitives for removable yubei sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def save_json_atomic(path: Path, value: dict[str, Any]) -> None:
    """Write one JSON object without leaving a partial destination."""
    if not isinstance(value, dict):
        raise ValueError("JSON value must be an object")
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.tmp-", dir=str(output.parent), text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_json(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


@dataclass(frozen=True)
class SessionPaths:
    """Directory layout for one standalone dataset capture session."""

    root: Path
    images_dir: Path
    labels_dir: Path
    ambiguous_dir: Path
    manifest_path: Path

    @classmethod
    def create(cls, base: Path, prefix: str = "dataset") -> "SessionPaths":
        parent = Path(base).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            root = parent / f"{prefix}_{stamp}"
            try:
                root.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(f"cannot create unique session below {parent}")

        images = root / "images"
        labels = root / "labels"
        ambiguous = root / "ambiguous"
        for directory in (images, labels, ambiguous):
            directory.mkdir()
        return cls(root, images, labels, ambiguous, root / "manifest.json")

