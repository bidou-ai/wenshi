"""Automatic panicle candidates and operator review operations.

This module deliberately has no image, model, or hardware dependency.  It
owns the review data contract so a UI or an offline detector can use the same
operations and audit trail.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict
import math
from typing import Any, Iterable


STATUSES = ("confirmed", "suspected", "occluded", "duplicate", "deleted")
VIEWS = ("left", "center", "right")


@dataclass(frozen=True)
class PanicleInstance:
    id: str
    view: str
    bbox: list[float]
    status: str = "suspected"
    confidence: float | None = None
    depth: float | None = None
    group_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["candidate_id"] = value.pop("id")
        value["panicle_group_id"] = value.pop("group_id")
        value["score"] = value.pop("confidence")
        return value


@dataclass
class PanicleGroup:
    group_id: str
    instances: list[PanicleInstance]
    requires_manual_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "instances": [item.to_dict() for item in self.instances],
            "requires_manual_review": self.requires_manual_review,
        }


def _validate_bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError("bbox must contain four coordinates")
    try:
        result = [float(item) for item in value]
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox coordinates must be numeric") from exc
    if not all(math.isfinite(item) for item in result):
        raise ValueError("bbox coordinates must be finite")
    if result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("bbox must have positive width and height")
    return result


def _validate_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be an object")
    result = deepcopy(candidate)
    candidate_id = result.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id is required")
    if result.get("view") not in VIEWS:
        raise ValueError("view must be left, center, or right")
    result["bbox"] = _validate_bbox(result.get("bbox"))
    status = result.get("status", "suspected")
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    result["status"] = status
    if "score" in result and result["score"] is not None:
        result["score"] = _finite_measurement(result["score"], "score")
    if "depth" in result and result["depth"] is not None:
        result["depth"] = _finite_measurement(result["depth"], "depth")
    if result.get("panicle_group_id") is not None and not isinstance(result["panicle_group_id"], str):
        raise ValueError("panicle_group_id must be a string or null")
    return result


class PanicleReview:
    """Validated candidate collection with an append-only operation history."""

    def __init__(self, candidates: Iterable[dict[str, Any]], history: Iterable[dict[str, Any]] | None = None):
        self.candidates = [_validate_candidate(item) for item in candidates]
        ids = [item["candidate_id"] for item in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_id values must be unique")
        self.history = deepcopy(list(history or []))

    def get(self, candidate_id: str) -> dict[str, Any]:
        for candidate in self.candidates:
            if candidate["candidate_id"] == candidate_id:
                return candidate
        raise ValueError(f"unknown candidate_id: {candidate_id}")

    def to_dict(self) -> dict[str, Any]:
        return {"candidates": deepcopy(self.candidates), "history": deepcopy(self.history)}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PanicleReview":
        if not isinstance(value, dict):
            raise ValueError("review must be an object")
        return cls(value.get("candidates", []), value.get("history", []))

    def _record(self, operation: str, **details: Any) -> None:
        self.history.append({"operation": operation, **deepcopy(details)})


def build_candidates(plant_id: str, detections_by_view: dict[str, Iterable[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Convert detector output into the stable candidate review schema."""
    if not isinstance(plant_id, str) or not plant_id:
        raise ValueError("plant_id is required")
    if not isinstance(detections_by_view, dict):
        raise ValueError("detections_by_view must be an object")
    result = []
    for view, detections in detections_by_view.items():
        if view not in VIEWS:
            raise ValueError(f"invalid view: {view!r}")
        for index, detection in enumerate(detections, start=1):
            if not isinstance(detection, dict):
                raise ValueError("detection must be an object")
            candidate = dict(detection)
            candidate.update({"candidate_id": f"{view}-{index:03d}", "plant_id": plant_id, "view": view})
            result.append(_validate_candidate(candidate))
    return result


def merge_panicles(review: PanicleReview, candidate_ids: Iterable[str], group_id: str | None = None) -> str:
    ids = list(candidate_ids)
    if len(ids) < 2:
        raise ValueError("merge requires at least two candidates")
    for candidate_id in ids:
        review.get(candidate_id)
    if len(set(ids)) != len(ids):
        raise ValueError("candidate_ids must be unique")
    group_id = group_id or f"panicle-{len(_group_ids(review)) + 1:03d}"
    if not isinstance(group_id, str) or not group_id:
        raise ValueError("group_id is required")
    for candidate_id in ids:
        review.get(candidate_id)["panicle_group_id"] = group_id
    review._record("merge", candidate_ids=ids, group_id=group_id)
    return group_id


def split_panicle(review: PanicleReview, group_id: str, partitions: Iterable[Iterable[str]]) -> list[str]:
    if not isinstance(group_id, str) or not group_id:
        raise ValueError("group_id is required")
    parts = [list(partition) for partition in partitions]
    if len(parts) < 2 or any(not partition for partition in parts):
        raise ValueError("split requires at least two non-empty partitions")
    all_ids = [candidate_id for partition in parts for candidate_id in partition]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("split candidate_ids must be unique")
    existing_ids = {
        candidate["candidate_id"] for candidate in review.candidates
        if candidate.get("panicle_group_id") == group_id
    }
    if set(all_ids) != existing_ids:
        raise ValueError("split partitions must cover every member of the original group exactly once")
    for candidate_id in all_ids:
        candidate = review.get(candidate_id)
        if candidate.get("panicle_group_id") != group_id:
            raise ValueError("candidate does not belong to group")
    new_groups = [f"{group_id}-{index:02d}" for index in range(1, len(parts) + 1)]
    for new_group, partition in zip(new_groups, parts):
        for candidate_id in partition:
            review.get(candidate_id)["panicle_group_id"] = new_group
    review._record("split", group_id=group_id, partitions=parts, new_group_ids=new_groups)
    return new_groups


def add_panicle(review: PanicleReview, view: str, bbox: Iterable[float], *, source: str = "operator", candidate_id: str | None = None) -> str:
    if view not in VIEWS:
        raise ValueError(f"invalid view: {view!r}")
    candidate_id = candidate_id or _next_manual_id(review)
    candidate = _validate_candidate({
        "candidate_id": candidate_id,
        "view": view,
        "bbox": list(bbox),
        "score": None,
        "status": "confirmed",
        "source": source,
        "panicle_group_id": None,
    })
    if any(item["candidate_id"] == candidate_id for item in review.candidates):
        raise ValueError(f"candidate_id already exists: {candidate_id}")
    review.candidates.append(candidate)
    review._record("add", candidate_id=candidate_id)
    return candidate_id


def apply_review_operation(review: PanicleReview, operation: str, *, candidate_ids: Iterable[str], status: str | None = None) -> None:
    ids = list(candidate_ids)
    if not ids:
        raise ValueError("candidate_ids cannot be empty")
    for candidate_id in ids:
        review.get(candidate_id)
    if operation == "unmerge":
        for candidate_id in ids:
            review.get(candidate_id)["panicle_group_id"] = None
    elif operation == "status":
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        for candidate_id in ids:
            review.get(candidate_id)["status"] = status
    elif operation == "delete":
        for candidate_id in ids:
            review.get(candidate_id)["status"] = "deleted"
    else:
        raise ValueError(f"unsupported review operation: {operation!r}")
    review._record(operation, candidate_ids=ids, **({"status": status} if operation == "status" else {}))


def summarize_panicles(review: PanicleReview, *, automatic_count: int | None = None, reviewed_count: int | None = None) -> dict[str, Any]:
    automatic_evidence = _candidate_group_evidence(review, confirmed_only=False)
    reviewed_evidence = _candidate_group_evidence(review, confirmed_only=True)
    expected_automatic_count = len(automatic_evidence)
    expected_reviewed_count = len(reviewed_evidence)
    if automatic_count is not None and automatic_count != expected_automatic_count:
        raise ValueError("automatic_count must match active candidate evidence")
    if reviewed_count is not None and reviewed_count != expected_reviewed_count:
        raise ValueError("reviewed_count must match confirmed candidate evidence")
    automatic_count = expected_automatic_count
    reviewed_count = expected_reviewed_count
    counts = {f"{status}_count": sum(item["status"] == status for item in review.candidates) for status in STATUSES}
    return {
        "automatic_count": automatic_count,
        "reviewed_count": reviewed_count,
        "difference": reviewed_count - automatic_count,
        "candidate_count": len(review.candidates),
        "active_candidate_count": sum(item["status"] != "deleted" for item in review.candidates),
        **counts,
        "group_count": len(_group_ids(review)),
        "automatic_count_evidence": {"source": "active_candidate_groups", "candidate_ids": automatic_evidence},
        "reviewed_count_evidence": {"source": "confirmed_candidate_groups", "candidate_ids": reviewed_evidence},
    }


def merge_cross_view_instances(
    instances: Iterable[PanicleInstance],
    reference_transform: Any = None,
    tolerance: float = 0.0,
) -> list[dict[str, Any]]:
    """Merge only candidates projected into a calibrated common 3-D frame."""
    if not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be a non-negative finite number")
    normalized = [_validate_candidate(item.to_dict() if isinstance(item, PanicleInstance) else item) for item in instances]
    groups: list[PanicleGroup] = []
    for item in normalized:
        instance = PanicleInstance(
            id=item["candidate_id"], view=item["view"], bbox=item["bbox"], status=item["status"],
            confidence=item.get("score"), depth=item.get("depth"), group_id=item.get("panicle_group_id"),
        )
        position = _reference_position(reference_transform, instance)
        match = None
        if item["status"] != "deleted" and position is not None:
            for group in groups:
                if any(member.view == item["view"] for member in group.instances):
                    continue
                reference = group.instances[0]
                other = _reference_position(reference_transform, reference)
                if other is not None and math.dist(position, other) <= tolerance:
                    match = group
                    match.requires_manual_review = False
                    break
        if match is None:
            match = PanicleGroup(f"panicle-{len(groups) + 1:03d}", [])
            groups.append(match)
        match.instances.append(PanicleInstance(
            id=item["candidate_id"],
            view=item["view"],
            bbox=item["bbox"],
            status=item["status"],
            confidence=item.get("score"),
            depth=item.get("depth"),
            group_id=match.group_id,
        ))
    return groups


def apply_panicle_edit(groups: Iterable[dict[str, Any]], action: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply a UI-shaped edit to serializable review groups."""
    result = deepcopy(list(groups))
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if action == "add":
        candidate = _validate_candidate({**payload, "candidate_id": payload.get("id"), "status": payload.get("status", "confirmed")})
        result.append({"group_id": f"panicle-{len(result) + 1:03d}", "instances": [candidate]})
    elif action in ("delete", "status"):
        ids = set(payload.get("instance_ids", []))
        if not ids:
            raise ValueError("instance_ids cannot be empty")
        new_status = "deleted" if action == "delete" else payload.get("status")
        if new_status not in STATUSES:
            raise ValueError("invalid status")
        for group in result:
            for item in group.get("instances", []):
                if item.get("candidate_id", item.get("id")) in ids:
                    item["status"] = new_status
    elif action == "merge":
        ids = list(payload.get("group_ids", []))
        if len(ids) < 2:
            raise ValueError("merge requires at least two groups")
        selected = [group for group in result if group.get("group_id") in ids]
        if len(selected) != len(ids):
            raise ValueError("unknown group_id")
        first = selected[0]
        for group in selected[1:]:
            first["instances"].extend(group["instances"])
        result = [group for group in result if group in (first,) or group.get("group_id") not in ids]
    elif action == "split":
        group_id = payload.get("group_id")
        partitions = payload.get("partitions")
        target = next((group for group in result if group.get("group_id") == group_id), None)
        if target is None or not isinstance(partitions, list) or len(partitions) < 2:
            raise ValueError("split requires an existing group and partitions")
        by_id = {item.get("candidate_id", item.get("id")): item for item in target["instances"]}
        partition_ids = [item_id for partition in partitions for item_id in partition]
        if len(partition_ids) != len(set(partition_ids)):
            raise ValueError("split candidate_ids must be unique")
        if set(partition_ids) != set(by_id):
            if any(item_id not in by_id for item_id in partition_ids):
                raise ValueError("split contains unknown group member")
            raise ValueError("split partitions must cover every member of the original group exactly once")
        result.remove(target)
        for index, partition in enumerate(partitions, start=1):
            result.append({"group_id": f"{group_id}-{index:02d}", "instances": [by_id[item_id] for item_id in partition]})
    else:
        raise ValueError(f"unsupported panicle edit: {action!r}")
    return result


def summarize_effective_panicles(groups: Iterable[dict[str, Any]], *, automatic_count: int | None = None) -> dict[str, Any]:
    groups = list(groups)
    expected_automatic_count = sum(
        any(item.get("status") != "deleted" and item.get("source", "automatic") != "operator" for item in group.get("instances", []))
        for group in groups
    )
    if automatic_count is not None and automatic_count != expected_automatic_count:
        raise ValueError("automatic_count must match active group evidence")
    automatic_count = expected_automatic_count
    active = [group for group in groups if any(item.get("status") != "deleted" for item in group.get("instances", []))]
    counts = {status: 0 for status in STATUSES}
    for group in active:
        statuses = {item.get("status", "suspected") for item in group.get("instances", [])}
        status = "confirmed" if "confirmed" in statuses else next((item for item in STATUSES if item in statuses), "suspected")
        counts[status] += 1
    return {
        "automatic_count": automatic_count,
        "reviewed_count": counts["confirmed"],
        "difference": counts["confirmed"] - automatic_count,
        "group_count": len(groups),
        "automatic_count_evidence": {"source": "active_groups", "group_ids": [group.get("group_id") for group in groups if any(item.get("status") != "deleted" and item.get("source", "automatic") != "operator" for item in group.get("instances", []))]},
        **{f"{status}_group_count": counts[status] for status in STATUSES},
    }


def _group_ids(review: PanicleReview) -> set[str]:
    return {item["panicle_group_id"] for item in review.candidates if item.get("panicle_group_id") and item["status"] != "deleted"}


def _next_manual_id(review: PanicleReview) -> str:
    used = {item["candidate_id"] for item in review.candidates}
    index = 1
    while f"manual-{index:03d}" in used:
        index += 1
    return f"manual-{index:03d}"


def _finite_measurement(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _candidate_group_evidence(review: PanicleReview, *, confirmed_only: bool) -> list[str]:
    members: dict[str, list[dict[str, Any]]] = {}
    for candidate in review.candidates:
        if candidate["status"] == "deleted" or candidate.get("source", "automatic") == "operator":
            continue
        key = candidate.get("panicle_group_id") or f"candidate:{candidate['candidate_id']}"
        members.setdefault(key, []).append(candidate)
    evidence = []
    for group_members in members.values():
        if not confirmed_only or any(member["status"] == "confirmed" for member in group_members):
            evidence.append(group_members[0]["candidate_id"])
    return evidence


def _reference_position(reference_transform: Any, instance: PanicleInstance) -> tuple[float, float, float] | None:
    if not callable(reference_transform):
        return None
    try:
        values = tuple(float(value) for value in reference_transform(instance))
    except (TypeError, ValueError):
        return None
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        return None
    return values
