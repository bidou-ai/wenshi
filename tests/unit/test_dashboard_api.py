import json
from pathlib import Path

import pytest

from dashboard.admin import AdminActions
from dashboard.run_index import RunIndex


def test_admin_soft_delete_requires_pin(tmp_path):
    root = tmp_path / "runs"
    target = root / "run_1" / "targets" / "T0001"
    target.mkdir(parents=True)
    (root / "run_1" / "run.json").write_text(json.dumps({"run_id": "run_1", "status": "finished"}), encoding="utf-8")
    (target / "metadata.json").write_text("{}", encoding="utf-8")
    actions = AdminActions(root, "1234")
    assert actions.authenticate("wrong") is None
    token = actions.authenticate("1234")
    assert token
    result = actions.soft_delete("run_1", "T0001", token)
    assert result["ok"] is True
    assert not target.exists()
    assert (root / "run_1" / "trash" / "T0001").exists()


def test_admin_authentication_is_disabled_when_pin_is_empty(tmp_path):
    actions = AdminActions(tmp_path / "runs", "")

    assert actions.authenticate("") is None
    with pytest.raises(PermissionError, match="未配置"):
        actions.soft_delete("run_1", None, "")


def test_admin_soft_delete_run_moves_it_outside_the_run_itself(tmp_path):
    root = tmp_path / "runs"
    run = root / "run_1"
    run.mkdir(parents=True)
    (run / "run.json").write_text(
        json.dumps({"run_id": "run_1", "status": "finished"}),
        encoding="utf-8",
    )
    actions = AdminActions(root, "1234")
    token = actions.authenticate("1234")

    result = actions.soft_delete("run_1", None, token)

    trash = root / ".trash" / "run_1"
    assert result["trash"] == str(trash)
    assert not run.exists()
    assert (trash / "run.json").is_file()
    assert (trash / "admin_events.jsonl").is_file()


def test_admin_can_reset_dedupe_for_the_running_patrol(tmp_path):
    root = tmp_path / "runs"
    run = root / "run_1"
    run.mkdir(parents=True)
    (run / "run.json").write_text(
        json.dumps({"run_id": "run_1", "status": "running"}),
        encoding="utf-8",
    )
    actions = AdminActions(root, "1234")
    token = actions.authenticate("1234")

    result = actions.reset_dedupe("run_1", token)

    assert result["ok"] is True
    marker = json.loads((run / "dedupe_reset.json").read_text(encoding="utf-8"))
    assert marker["request_id"]
