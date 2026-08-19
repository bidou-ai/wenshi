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


@dataclass
class DatasetManifest:
    classes: dict[str, int] = field(default_factory=lambda: {"rice": 0, "flower": 1})
    images: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def add_image(self, filename: str, width: int, height: int, status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid image status: {status}")
        if not filename or Path(filename).is_absolute() or ".." in Path(filename).parts:
            raise ValueError("filename must be a relative path")
        if int(width) <= 0 or int(height) <= 0:
            raise ValueError("image dimensions must be positive")
        self.images.append(
            {
                "filename": filename,
                "width": int(width),
                "height": int(height),
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    def write(self, path: Path) -> None:
        save_json_atomic(
            Path(path),
            {"classes": self.classes, "created_at": self.created_at, "images": self.images},
        )
