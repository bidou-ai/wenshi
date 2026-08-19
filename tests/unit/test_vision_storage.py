from pathlib import Path

from wenshi_patrol.vision.storage import VisionRunStore


def test_vision_store_is_confined_to_current_run_directory(tmp_path: Path):
    run_dir = tmp_path / "runtime" / "run_20260819_120000"
    store = VisionRunStore(run_dir)
    color, depth, annotated = store.image_paths("20260819_120001", "detect")
    assert color.parent == run_dir / "vision" / "images"
    assert depth.parent == color.parent
    assert annotated.parent == color.parent
    assert store.record_path == run_dir / "vision" / "detections.jsonl"


def test_vision_store_rejects_non_run_directory(tmp_path: Path):
    try:
        VisionRunStore(tmp_path / "runtime" / "ad_hoc")
    except ValueError as exc:
        assert "run_" in str(exc)
    else:
        raise AssertionError("non-run directory was accepted")

