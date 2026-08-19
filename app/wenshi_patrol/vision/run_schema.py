"""Small schema helpers for formal patrol run metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def target_metadata_defaults(target_id: str) -> dict[str, Any]:
    if not target_id or not target_id.startswith("T"):
        raise ValueError("target id must start with T")
    return {
        "target_id": target_id,
        "created_at": now_iso(),
        "status": "created",
        "side": None,
        "route_segment": None,
        "along_track_m": None,
        "far": None,
        "near": None,
        "failure_reason": None,
    }
