from pathlib import Path
import json


def test_height_test_entry_lists_all_modes():
    text = Path("scripts/start_height_test.sh").read_text(encoding="utf-8")
    assert all(item in text or item in Path("app/wenshi_patrol/height_test/cli.py").read_text(encoding="utf-8") for item in ("setup", "arm-only", "full-route", "replay", "report"))


def test_replay_is_hardware_free(tmp_path, monkeypatch):
    from wenshi_patrol.height_test import cli
    run = tmp_path / "run_20260916_000000"
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"run_id": run.name}), encoding="utf-8")
    monkeypatch.setattr(cli, "JakaClient", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("hardware opened")))
    assert cli.main(["replay", str(run)]) in (0, 2)


def test_motion_commands_require_explicit_confirmation(monkeypatch):
    from wenshi_patrol.height_test import cli
    assert cli.main(["arm-only"]) == 2


def test_live_station_pose_requires_fresh_stopped_agv():
    from wenshi_patrol.height_test.cli import _live_station_pose

    class Status:
        def wait_for_status(self, **_kwargs):
            return True

        def get_status(self):
            return {"x": 1.25, "y": -0.5, "angle": 0.2, "is_stop": False, "emergency": False}

    try:
        _live_station_pose(Status())
    except RuntimeError as exc:
        assert "停稳" in str(exc)
    else:
        raise AssertionError("moving AGV pose was accepted")


def test_live_station_pose_reads_current_agv_pose():
    from wenshi_patrol.height_test.cli import _live_station_pose

    class Status:
        def wait_for_status(self, **_kwargs):
            return True

        def get_status(self):
            return {"x": 1.25, "y": -0.5, "angle": 0.2, "is_stop": True, "emergency": False}

    assert _live_station_pose(Status()) == {"x": 1.25, "y": -0.5, "angle": 0.2}
