import json
from pathlib import Path

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
