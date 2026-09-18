from pathlib import Path
import json

import pytest


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


@pytest.mark.parametrize(
    ("field", "value"),
    (("errors", ["drive fault"]), ("fatals", ["fatal"]), ("brake", True)),
)
def test_live_station_pose_rejects_agv_alarm(field, value):
    from wenshi_patrol.height_test.cli import _live_station_pose

    class Status:
        def wait_for_status(self, **_kwargs): return True
        def get_status(self):
            return {
                "x": 1.25,
                "y": -0.5,
                "angle": 0.2,
                "is_stop": True,
                "emergency": False,
                "blocked": False,
                field: value,
            }

    with pytest.raises(RuntimeError, match="报警|刹车"):
        _live_station_pose(Status())


def test_full_route_connects_motion_only_after_safe_status_and_home_pose():
    from wenshi_patrol.height_test.cli import _connect_live_hardware

    calls = []

    class Arm:
        def connect(self): calls.append("arm_connect")
        def move_to_safe(self): calls.append("arm_safe"); return True

    class Agv:
        def connect_status(self): calls.append("agv_status_safe")
        def connect_motion(self): calls.append("agv_motion")

    _connect_live_hardware(Arm(), Agv(), require_motion=True)
    assert calls == ["agv_status_safe", "arm_connect", "arm_safe", "agv_motion"]


def test_full_route_does_not_connect_motion_when_agv_status_is_unsafe():
    from wenshi_patrol.height_test.cli import _connect_live_hardware

    class Arm:
        def connect(self): raise AssertionError("JAKA opened before AGV was verified")

    class Agv:
        def connect_status(self): raise RuntimeError("AGV is not safely stopped")
        def connect_motion(self): raise AssertionError("motion port opened")

    with pytest.raises(RuntimeError, match="safely stopped"):
        _connect_live_hardware(Arm(), Agv(), require_motion=True)


def test_manual_recovery_warning_is_printed_for_skipped_retract(capsys):
    from wenshi_patrol.height_test.cli import _warn_if_manual_arm_recovery_required

    assert _warn_if_manual_arm_recovery_required(("stop", "safe_retract_skipped")) is True
    assert "实体急停" in capsys.readouterr().err


def test_final_runner_stop_reports_late_manual_recovery_requirement(capsys):
    from wenshi_patrol.height_test.cli import _stop_runner_and_warn

    class Runner:
        def __init__(self): self._events = []
        def stop(self, reason): self._events.extend(("stop", "safe_retract_skipped"))

    assert _stop_runner_and_warn(Runner()) is True
    assert "实体急停" in capsys.readouterr().err


def test_final_runner_stop_does_not_repeat_an_existing_recovery_warning(capsys):
    from wenshi_patrol.height_test.cli import _stop_runner_and_warn

    class Runner:
        def __init__(self): self._events = ["safe_retract_skipped"]; self.stopped = False
        def stop(self, _reason): self.stopped = True

    runner = Runner()
    assert _stop_runner_and_warn(runner, already_warned=True) is True
    assert runner.stopped is True
    assert capsys.readouterr().err == ""


def test_interactive_report_records_manual_height_without_editing_json(tmp_path, monkeypatch):
    import json

    from wenshi_patrol.height_test.cli import _collect_manual_heights
    from wenshi_patrol.height_test.storage import HeightTestStore

    store = HeightTestStore.create(tmp_path)
    plant = store.path / "plants" / "A-01"
    plant.mkdir(parents=True)
    (plant / "results.json").write_text(
        json.dumps({"plant_id": "A-01", "candidate_value_m": 1.0, "quality": "ok"}),
        encoding="utf-8",
    )
    answers = iter(("operator-1", "1.02"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    assert _collect_manual_heights(store.path) == 1
    value = json.loads((plant / "manual.json").read_text(encoding="utf-8"))
    assert value["measured_height_m"] == 1.02
    assert value["operator"] == "operator-1"
