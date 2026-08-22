"""Run-local target de-duplication and current-loop suppression."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal


@dataclass(frozen=True)
class TargetKey:
    segment: str
    side: Literal["left", "right"]
    along_track_m: float


@dataclass(frozen=True)
class DedupeDecision:
    allowed: bool
    reason: str


@dataclass
class _Seen:
    key: TargetKey
    timestamp: float
    loop_id: int | None


class DedupeRegistry:
    def __init__(self, ttl_s: float = 7200.0, suppression_radius_m: float = 0.30):
        self.ttl_s = max(float(ttl_s), 0.0)
        self.suppression_radius_m = max(float(suppression_radius_m), 0.0)
        self._selected: list[_Seen] = []
        self._deferred: list[_Seen] = []

    def _prune(self, now: float) -> None:
        self._selected = [item for item in self._selected if float(now) - item.timestamp < self.ttl_s]

    @staticmethod
    def _near(first: TargetKey, second: TargetKey, radius: float) -> bool:
        if first.segment != second.segment or first.side != second.side:
            return False
        return abs(float(first.along_track_m) - float(second.along_track_m)) <= radius

    def mark_selected(self, key: TargetKey, now: float, loop_id: int | None = None) -> None:
        self._prune(now)
        self._selected.append(_Seen(key, float(now), loop_id))
        self._deferred.append(_Seen(key, float(now), loop_id))

    def can_process(self, key: TargetKey, now: float, loop_id: int | None = None) -> DedupeDecision:
        self._prune(now)
        for item in self._selected:
            if self._near(item.key, key, self.suppression_radius_m) and float(now) - item.timestamp < self.ttl_s:
                if abs(float(item.key.along_track_m) - float(key.along_track_m)) <= 0.10:
                    return DedupeDecision(False, "two_hour_dedupe")
                if loop_id is not None and item.loop_id == loop_id:
                    return DedupeDecision(False, "current_loop_suppressed")
        for item in self._deferred:
            if loop_id is not None and item.loop_id == loop_id and self._near(item.key, key, self.suppression_radius_m):
                return DedupeDecision(False, "current_loop_suppressed")
        return DedupeDecision(True, "new_target")

    def reset(self) -> None:
        self._selected.clear()
        self._deferred.clear()

    def forget(self, key: TargetKey) -> None:
        self._selected = [item for item in self._selected if item.key != key]
        self._deferred = [item for item in self._deferred if item.key != key]
