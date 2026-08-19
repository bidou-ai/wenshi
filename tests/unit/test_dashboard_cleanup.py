import json
from pathlib import Path

import pytest

from dashboard.cleanup import execute_cleanup, preview_cleanup


def _run(root: Path, run_id: str, status: str):
    run = root / run_id
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"run_id": run_id, "status": status}), encoding="utf-8")
    (run / "data.bin").write_bytes(b"123")
    return run


def test_preview_is_non_mutating(tmp_path):
    root = tmp_path / "runs"
    run = _run(root, "run_old", "finished")
    plan = preview_cleanup(root, ["run_old"])
    assert plan.total_files == 2
    assert run.exists()


def test_cleanup_refuses_active_run(tmp_path):
    root = tmp_path / "runs"
    _run(root, "run_live", "running")
    with pytest.raises(ValueError, match="运行"):
        preview_cleanup(root, ["run_live"])


def test_execute_requires_exact_confirmation(tmp_path):
    root = tmp_path / "runs"
    run = _run(root, "run_old", "finished")
    plan = preview_cleanup(root, ["run_old"])
    with pytest.raises(ValueError, match="确认"):
        execute_cleanup(plan, "wrong")
    result = execute_cleanup(plan, "run_old")
    assert result.removed_runs == ["run_old"]
    assert not run.exists()
