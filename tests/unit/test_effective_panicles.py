import pytest

from wenshi_patrol.phenotyping.effective_panicles import (
    PanicleInstance,
    PanicleReview,
    add_panicle,
    apply_review_operation,
    apply_panicle_edit,
    build_candidates,
    merge_cross_view_instances,
    merge_panicles,
    split_panicle,
    summarize_effective_panicles,
    summarize_panicles,
)


def _candidate(candidate_id, view, x, y, status="suspected", group=None):
    return {
        "candidate_id": candidate_id,
        "view": view,
        "bbox": [x, y, x + 10, y + 20],
        "score": 0.8,
        "status": status,
        **({"panicle_group_id": group} if group else {}),
    }


def test_build_candidates_normalizes_auto_detections_and_assigns_stable_ids():
    result = build_candidates(
        "A-01",
        {
            "left": [{"bbox": [1, 2, 11, 22], "score": 0.91}],
            "center": [{"bbox": [3, 4, 13, 24], "score": 0.72, "status": "confirmed"}],
        },
    )

    assert [item["candidate_id"] for item in result] == ["left-001", "center-001"]
    assert result[0]["status"] == "suspected"
    assert result[1]["status"] == "confirmed"
    assert result[0]["plant_id"] == "A-01"


def test_review_rejects_unknown_status_and_malformed_bbox():
    with pytest.raises(ValueError, match="status"):
        PanicleReview([_candidate("left-001", "left", 1, 2, status="unknown")])
    with pytest.raises(ValueError, match="bbox"):
        PanicleReview([{"candidate_id": "left-001", "view": "left", "bbox": [1, 2]}])


def test_merge_marks_same_panicle_across_views_and_preserves_provenance():
    review = PanicleReview([
        _candidate("left-001", "left", 1, 2),
        _candidate("center-001", "center", 3, 4),
        _candidate("right-001", "right", 5, 6),
    ])

    merged = merge_panicles(review, ["left-001", "center-001"], group_id="panicle-001")

    assert merged == "panicle-001"
    assert review.get("left-001")["panicle_group_id"] == "panicle-001"
    assert review.get("center-001")["panicle_group_id"] == "panicle-001"
    assert review.get("right-001").get("panicle_group_id") is None
    assert review.history[-1]["operation"] == "merge"


def test_unmerge_clears_group_without_deleting_candidates():
    review = PanicleReview([
        _candidate("left-001", "left", 1, 2, group="panicle-001"),
        _candidate("center-001", "center", 3, 4, group="panicle-001"),
    ])

    apply_review_operation(review, "unmerge", candidate_ids=["left-001", "center-001"])

    assert all(review.get(item)["panicle_group_id"] is None for item in ("left-001", "center-001"))
    assert len(review.candidates) == 2


def test_split_creates_distinct_groups_for_a_bad_merge():
    review = PanicleReview([
        _candidate("left-001", "left", 1, 2, group="panicle-001"),
        _candidate("center-001", "center", 3, 4, group="panicle-001"),
    ])

    groups = split_panicle(review, "panicle-001", [["left-001"], ["center-001"]])

    assert groups == ["panicle-001-01", "panicle-001-02"]
    assert review.get("left-001")["panicle_group_id"] != review.get("center-001")["panicle_group_id"]


def test_split_rejects_partitions_that_omit_a_member_of_the_original_group():
    review = PanicleReview([
        _candidate("left-001", "left", 1, 2, group="panicle-001"),
        _candidate("center-001", "center", 3, 4, group="panicle-001"),
        _candidate("right-001", "right", 5, 6, group="panicle-001"),
    ])

    with pytest.raises(ValueError, match="cover"):
        split_panicle(review, "panicle-001", [["left-001"], ["center-001"]])

    assert {item["panicle_group_id"] for item in review.candidates} == {"panicle-001"}


def test_manual_add_delete_and_status_changes_are_audited():
    review = PanicleReview([])

    added = add_panicle(review, "right", [10, 20, 30, 50], source="operator")
    apply_review_operation(review, "status", candidate_ids=[added], status="occluded")
    apply_review_operation(review, "delete", candidate_ids=[added])

    assert review.get(added)["status"] == "deleted"
    assert [item["operation"] for item in review.history] == ["add", "status", "delete"]


def test_summary_derives_counts_from_candidate_evidence_and_rejects_inconsistent_inputs():
    review = PanicleReview([
        _candidate("left-001", "left", 1, 2, status="confirmed", group="p-001"),
        _candidate("center-001", "center", 3, 4, status="duplicate", group="p-001"),
        _candidate("right-001", "right", 5, 6, status="suspected"),
        _candidate("right-002", "right", 7, 8, status="occluded"),
        _candidate("right-003", "right", 9, 10, status="deleted"),
    ])

    with pytest.raises(ValueError, match="automatic_count"):
        summarize_panicles(review, automatic_count=99, reviewed_count=1)

    stats = summarize_panicles(review, automatic_count=3, reviewed_count=1)

    assert stats == {
        "automatic_count": 3,
        "reviewed_count": 1,
        "difference": -2,
        "candidate_count": 5,
        "active_candidate_count": 4,
        "confirmed_count": 1,
        "suspected_count": 1,
        "occluded_count": 1,
        "duplicate_count": 1,
        "deleted_count": 1,
        "group_count": 1,
        "automatic_count_evidence": {"source": "active_candidate_groups", "candidate_ids": ["left-001", "right-001", "right-002"]},
        "reviewed_count_evidence": {"source": "confirmed_candidate_groups", "candidate_ids": ["left-001"]},
    }


def test_review_can_round_trip_json_schema():
    review = PanicleReview([_candidate("left-001", "left", 1, 2)])
    payload = review.to_dict()
    restored = PanicleReview.from_dict(payload)

    assert restored.to_dict() == payload


def test_cross_view_instances_without_a_calibrated_transform_remain_unmerged_for_manual_review():
    instances = [
        PanicleInstance("l1", "left", [10, 10, 20, 30], "suspected", 0.9, 1.0),
        PanicleInstance("c1", "center", [11, 11, 21, 31], "suspected", 0.8, 1.1),
        PanicleInstance("r1", "right", [100, 100, 110, 120], "suspected", 0.9, 1.0),
    ]

    groups = merge_cross_view_instances(instances, reference_transform=None, tolerance=3.0)

    assert [[item.id for item in group.instances] for group in groups] == [["l1"], ["c1"], ["r1"]]
    assert all(group.requires_manual_review for group in groups)


def test_cross_view_instances_merge_only_when_a_transform_returns_matching_finite_3d_points():
    instances = [
        PanicleInstance("l1", "left", [10, 10, 20, 30], "suspected", 0.9, 1.0),
        PanicleInstance("c1", "center", [100, 100, 110, 120], "suspected", 0.8, 1.1),
    ]

    groups = merge_cross_view_instances(
        instances,
        reference_transform=lambda instance: (0.2, 0.1, instance.depth),
        tolerance=0.2,
    )

    assert [[item.id for item in group.instances] for group in groups] == [["l1", "c1"]]
    assert groups[0].requires_manual_review is False


@pytest.mark.parametrize("field,value", [("bbox", [0, 0, float("nan"), 1]), ("score", float("inf")), ("depth", float("nan"))])
def test_review_rejects_non_finite_candidate_measurements(field, value):
    candidate = _candidate("left-001", "left", 1, 2)
    candidate[field] = value

    with pytest.raises(ValueError, match="finite"):
        PanicleReview([candidate])


def test_planned_edit_api_supports_add_delete_merge_and_split():
    groups = [
        {"group_id": "p-001", "instances": [{"id": "l1", "view": "left", "bbox": [1, 1, 5, 8], "status": "confirmed"}]}
    ]

    groups = apply_panicle_edit(groups, "add", {"id": "c1", "view": "center", "bbox": [2, 2, 6, 9]})
    groups = apply_panicle_edit(groups, "merge", {"group_ids": ["p-001", "panicle-002"]})
    groups = apply_panicle_edit(groups, "delete", {"instance_ids": ["c1"]})

    assert groups[0]["instances"][1]["status"] == "deleted"
    assert groups[0]["group_id"] == "p-001"


def test_planned_split_rejects_missing_or_unknown_group_members():
    groups = [{
        "group_id": "p-001",
        "instances": [
            {"id": "l1", "view": "left", "bbox": [1, 1, 5, 8]},
            {"id": "c1", "view": "center", "bbox": [2, 2, 6, 9]},
            {"id": "r1", "view": "right", "bbox": [3, 3, 7, 10]},
        ],
    }]

    with pytest.raises(ValueError, match="cover"):
        apply_panicle_edit(groups, "split", {"group_id": "p-001", "partitions": [["l1"], ["c1"]]})
    with pytest.raises(ValueError, match="unknown"):
        apply_panicle_edit(groups, "split", {"group_id": "p-001", "partitions": [["l1"], ["c1", "unknown"]]})


def test_planned_summary_uses_confirmed_groups_and_keeps_auto_difference():
    groups = [{
        "group_id": "p-001",
        "instances": [{"id": "l1", "view": "left", "bbox": [1, 1, 5, 8], "status": "confirmed"}],
    }, {
        "group_id": "p-002",
        "instances": [{"id": "c1", "view": "center", "bbox": [2, 2, 6, 9], "status": "suspected"}],
    }]

    summary = summarize_effective_panicles(groups, automatic_count=2)

    assert summary == {
        "automatic_count": 2,
        "reviewed_count": 1,
        "difference": -1,
        "group_count": 2,
        "confirmed_group_count": 1,
        "suspected_group_count": 1,
        "occluded_group_count": 0,
        "duplicate_group_count": 0,
        "deleted_group_count": 0,
        "automatic_count_evidence": {"source": "active_groups", "group_ids": ["p-001", "p-002"]},
    }
