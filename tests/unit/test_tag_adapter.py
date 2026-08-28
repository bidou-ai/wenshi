import json
from pathlib import Path

import numpy as np
import pytest

from wenshi_patrol.phenotyping.tag_adapter import (
    BackendUnavailableError,
    DuplicateTagError,
    TagDetector,
    TagDetection,
    match_expected_tag,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "tags" / "tag25h7_fixture.json"


def _fixture_backend(image, family):
    assert family == "tag25h7"
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["detections"]


def test_fixture_backend_decodes_deterministic_tag25h7_detection():
    detector = TagDetector(
        family="tag25h7",
        backend="fixture",
        physical_size_m=None,
        backend_factory=_fixture_backend,
        mounting_orientation="upward",
    )

    detections = detector.detect(np.zeros((32, 32, 3), dtype=np.uint8))

    assert detections == [
        TagDetection(
            tag_id=7,
            corners=[(10.0, 11.0), (20.0, 11.0), (20.0, 21.0), (10.0, 21.0)],
            score=0.97,
            pose={"mounting_orientation": "upward"},
        )
    ]


def test_detector_rejects_unknown_family_without_opencv_dictionary_assumption():
    with pytest.raises(ValueError, match="tag25h7"):
        TagDetector("tag36h11", "fixture", None, backend_factory=_fixture_backend)


def test_detector_rejects_duplicate_tag_ids_as_ambiguous():
    def duplicate_backend(_image, _family):
        return [
            {"tag_id": 7, "corners": [[0, 0], [1, 0], [1, 1], [0, 1]], "score": 0.9},
            {"tag_id": 7, "corners": [[2, 2], [3, 2], [3, 3], [2, 3]], "score": 0.8},
        ]

    detector = TagDetector("tag25h7", "fixture", None, backend_factory=duplicate_backend)
    with pytest.raises(DuplicateTagError, match="7"):
        detector.detect(np.zeros((8, 8, 3), dtype=np.uint8))


def test_match_expected_tag_reports_match_mismatch_and_missing():
    detection = TagDetection(7, [(0, 0), (1, 0), (1, 1), (0, 1)], 0.9, None)

    assert match_expected_tag([detection], 7)["status"] == "matched"
    mismatch = match_expected_tag([detection], 8)
    assert mismatch["status"] == "mismatched"
    assert mismatch["detected_tag_id"] == 7
    assert match_expected_tag([], 7) == {"status": "missing", "expected_tag_id": 7}


def test_match_expected_tag_marks_multiple_different_ids_as_ambiguous():
    expected = TagDetection(7, [(0, 0), (1, 0), (1, 1), (0, 1)], 0.9, None)
    adjacent = TagDetection(8, [(2, 0), (3, 0), (3, 1), (2, 1)], 0.8, None)

    result = match_expected_tag([expected, adjacent], 7)

    assert result == {
        "status": "ambiguous",
        "expected_tag_id": 7,
        "detected_tag_ids": [7, 8],
    }


def test_mounting_orientation_is_preserved_for_upward_and_side_mounts():
    def backend(_image, _family):
        return [{"tag_id": 3, "corners": [[0, 0], [1, 0], [1, 1], [0, 1]], "score": 1.0}]

    for orientation in ("upward", "side"):
        detector = TagDetector(
            "tag25h7", "fixture", 0.08, backend_factory=backend,
            mounting_orientation=orientation,
        )
        pose = detector.detect(np.zeros((4, 4, 3), dtype=np.uint8))[0].pose
        assert pose == {
            "mounting_orientation": orientation,
            "physical_size_m": 0.08,
        }


def test_unavailable_backend_has_actionable_diagnostic():
    detector = TagDetector("tag25h7", "missing-backend", None)
    with pytest.raises(BackendUnavailableError, match="missing-backend"):
        detector.detect(np.zeros((4, 4, 3), dtype=np.uint8))
