import json
from pathlib import Path

import pytest

from dashboard.run_index import MediaResolver, RunIndex


def _fixture(tmp_path: Path):
    root = tmp_path / "runs"
    run = root / "run_20260819_120000"
    target = run / "targets" / "T0001"
    target.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": run.name, "status": "finished"}), encoding="utf-8")
    (target / "metadata.json").write_text(json.dumps({"target_id": "T0001", "status": "near_captured", "far": {"file": "far.jpg"}, "near": {"file": "near.jpg"}}), encoding="utf-8")
    (target / "far.jpg").write_bytes(b"far")
    return root, run


def test_run_index_lists_newest_runs_and_targets(tmp_path):
    root, run = _fixture(tmp_path)
    index = RunIndex(root)
    assert index.list_runs()[0]["run_id"] == run.name
    assert index.load_target(run.name, "T0001")["target_id"] == "T0001"


def test_media_resolver_rejects_path_escape(tmp_path):
    root, run = _fixture(tmp_path)
    resolver = MediaResolver(root)
    assert resolver.resolve(run.name, "T0001", "far.jpg").name == "far.jpg"
    with pytest.raises(ValueError):
        resolver.resolve(run.name, "T0001", "../../secret")
