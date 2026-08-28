"""Schemas and safe serialization helpers for phenotyping captures."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

import numpy as np


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
VIEWS = ("left", "center", "right")
TRAITS = ("plant_height", "effective_panicle_count")


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise ValueError("JSON value must be an object")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent), text=True)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_component(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"invalid {label}")
    return value


def validate_view(view: str) -> str:
    if view not in VIEWS:
        raise ValueError(f"invalid capture view: {view!r}")
    return view


def _array_info(image: np.ndarray, file_name: str, encoding: str) -> dict[str, Any]:
    return {
        "file": file_name,
        "encoding": encoding,
        "shape": list(image.shape),
        "dtype": str(image.dtype),
    }


def capture_frame_metadata(view: str, color: np.ndarray, depth: np.ndarray | None, frame: dict[str, Any]) -> dict[str, Any]:
    validate_view(view)
    if not isinstance(frame, dict):
        raise ValueError("frame metadata must be an object")
    value = dict(frame)
    value["view"] = view
    value["color"] = _array_info(color, "color.jpg", "jpeg")
    value["depth"] = None if depth is None else _array_info(depth, "depth.png", "png")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("frame metadata must be JSON serializable with finite numbers") from exc
    return value


def plant_defaults(plant_id: str) -> dict[str, Any]:
    validate_component(plant_id, "plant id")
    return {
        "plant_id": plant_id,
        "tag_id": None,
        "region": None,
        "row": None,
        "index": None,
        "observation_group": None,
        "status": "created",
        "captures": {},
        "missing_views": list(VIEWS),
        "failure_reasons": {},
        "automatic_processing": "pending",
        "review_status": "pending",
    }
