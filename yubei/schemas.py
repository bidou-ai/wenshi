"""JSON schemas used by independent yubei dataset tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .paths import save_json_atomic
except ImportError:  # direct module execution
    from paths import save_json_atomic


VALID_STATUSES = {"captured", "skipped", "ambiguous"}
CAPTURE_TAGS = {"neutral", "rice", "flower"}


@dataclass
class DatasetManifest:
    classes: dict[str, int] = field(default_factory=lambda: {"rice": 0, "flower": 1})
    images: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def add_image(
        self,
        filename: str,
        width: int,
        height: int,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid image status: {status}")
        if not filename or Path(filename).is_absolute() or ".." in Path(filename).parts:
            raise ValueError("filename must be a relative path")
        if int(width) <= 0 or int(height) <= 0:
            raise ValueError("image dimensions must be positive")
        item = {
                "filename": filename,
                "width": int(width),
                "height": int(height),
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        extra = dict(metadata or {})
        reserved = {"filename", "width", "height", "status", "updated_at"}
        if reserved.intersection(extra):
            raise ValueError("image metadata cannot override reserved fields")
        capture_tag = str(extra.get("capture_tag", "neutral"))
        if capture_tag not in CAPTURE_TAGS:
            raise ValueError(f"invalid capture tag: {capture_tag}")
        extra["capture_tag"] = capture_tag
        item.update(extra)
        self.images.append(item)

    def write(self, path: Path) -> None:
        save_json_atomic(
            Path(path),
            {"classes": self.classes, "created_at": self.created_at, "images": self.images},
        )
