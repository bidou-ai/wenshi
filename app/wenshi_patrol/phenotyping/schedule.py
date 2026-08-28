"""Pure scheduling and Tag identity decisions for phenotype observations."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from .tag_adapter import TagDetection, match_expected_tag


@dataclass(frozen=True)
class ObservationStop:
    group_id: str
    route_side: str
    left_plant_id: str
    right_plant_id: str
    route_segment: str | None = None
    approximate_along_track_m: float | None = None
    slowdown_before_m: float | None = None
    trigger_distance_m: float | None = None

    @property
    def plant_ids(self) -> tuple[str, str]:
        return self.left_plant_id, self.right_plant_id


@dataclass(frozen=True)
class TagDecision:
    status: str
    expected_tag_id: int | None
    detected_tag_id: int | None = None
    retries_remaining: int = 0
    reason: str | None = None

    @property
    def bindable(self) -> bool:
        return self.status == "matched"


def build_observation_schedule(config: Any) -> list[ObservationStop]:
    """Build a deterministic left-then-right schedule without hardware access."""
    groups = getattr(config, "observation_groups", None)
    if groups is None:
        raise ValueError("configuration must contain observation groups")
    plants = getattr(config, "plants", None)
    if plants is None:
        raise ValueError("configuration must contain configured plants")
    configured_plant_ids = [str(getattr(plant, "plant_id", "")) for plant in plants]
    if len(configured_plant_ids) != 32:
        raise ValueError("configuration must contain exactly 32 plants")
    if not all(configured_plant_ids) or len(set(configured_plant_ids)) != 32:
        raise ValueError("configured plant IDs must be 32 unique values")
    if len(groups) != 16:
        raise ValueError("configuration must contain exactly 16 observation stops")

    def sort_key(group: Any) -> tuple[int, int, str]:
        group_id = str(getattr(group, "group_id", ""))
        match = re.fullmatch(r"(left|right)-(\d+)", group_id)
        if not match:
            raise ValueError(f"invalid observation group id: {group_id!r}")
        return (0 if match.group(1) == "left" else 1, int(match.group(2)), group_id)

    ordered = sorted(tuple(groups), key=sort_key)
    expected_group_ids = {
        *(f"left-{index:02d}" for index in range(1, 9)),
        *(f"right-{index:02d}" for index in range(1, 9)),
    }
    actual_group_ids = {str(getattr(group, "group_id", "")) for group in ordered}
    if actual_group_ids != expected_group_ids:
        raise ValueError("observation groups must use the expected stop IDs")
    result: list[ObservationStop] = []
    seen_groups: set[str] = set()
    scheduled_plant_ids: list[str] = []
    for group in ordered:
        group_id = str(group.group_id)
        if group_id in seen_groups:
            raise ValueError(f"duplicate observation group: {group_id}")
        seen_groups.add(group_id)
        left = getattr(group, "left_plant_id", None)
        right = getattr(group, "right_plant_id", None)
        if not left or not right:
            raise ValueError(f"observation group {group_id} must contain two plants")
        if left == right:
            raise ValueError(f"observation group {group_id} must contain distinct plants")
        scheduled_plant_ids.extend((str(left), str(right)))
        result.append(
            ObservationStop(
                group_id=group_id,
                route_side=group_id.split("-", 1)[0],
                left_plant_id=str(left),
                right_plant_id=str(right),
                route_segment=getattr(group, "route_segment", None),
                approximate_along_track_m=getattr(group, "approximate_along_track_m", None),
                slowdown_before_m=getattr(group, "slowdown_before_m", None),
                trigger_distance_m=getattr(group, "trigger_distance_m", None),
            )
        )
    if set(scheduled_plant_ids) != set(configured_plant_ids) or len(scheduled_plant_ids) != len(set(scheduled_plant_ids)):
        raise ValueError("each configured plant must appear exactly once in the observation schedule")
    return result


def handle_tag_result(
    expected: int | None, detections: list[TagDetection], retries: int
) -> TagDecision:
    """Convert one detection attempt into a retry or explicit identity decision.

    ``retries`` is the number of additional attempts available. A missing or
    mismatched Tag is never considered a formal plant binding.
    """
    if retries < 0:
        raise ValueError("retries must be non-negative")
    result = match_expected_tag(detections, expected)
    if result["status"] == "matched":
        return TagDecision("matched", expected, expected, retries)
    detected = result.get("detected_tag_id")
    if retries > 0:
        return TagDecision("retry", expected, detected, retries - 1, result["status"])
    return TagDecision("unconfirmed", expected, detected, 0, result["status"])
