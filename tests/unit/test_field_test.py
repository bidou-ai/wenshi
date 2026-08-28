import json
from pathlib import Path

from wenshi_patrol.map_utils import load_station_poses
from yubei.field_test import (
    build_closed_route,
    nearest_route_segment,
    route_segments_until_station,
    blocked_recovery_state,
    route_attachment,
    RouteRunner,
    validate_viewpoints,
    resolve_console_input,
)


ROOT = Path(__file__).resolve().parents[2]


def test_closed_route_contains_the_map_closing_segment():
    stations = load_station_poses(ROOT / "map" / "wenshi.smap")
    segments = build_closed_route(stations, ["LM1", "LM4", "LM3", "LM2"])

    assert [(item.start_name, item.end_name) for item in segments] == [
        ("LM1", "LM4"),
        ("LM4", "LM3"),
        ("LM3", "LM2"),
        ("LM2", "LM1"),
    ]


def test_nearest_route_segment_reports_cross_track_distance():
    stations = load_station_poses(ROOT / "map" / "wenshi.smap")
    segments = build_closed_route(stations, ["LM1", "LM4", "LM3", "LM2"])

    index, cross_track, distance = nearest_route_segment(
        {"x": 3.313, "y": 0.20, "angle": 0.0}, segments
    )

    assert index == 0
    assert round(abs(cross_track), 3) == 0.103
    assert round(distance, 3) == 0.103


def test_route_attach_from_lm3_lm2_reaches_lm1_without_reverse():
    stations = load_station_poses(ROOT / "map" / "wenshi.smap")
    segments = build_closed_route(stations, ["LM1", "LM4", "LM3", "LM2"])

    selected = route_segments_until_station(segments, attach_index=2, station_name="LM1")

    assert [(item.start_name, item.end_name) for item in selected] == [
        ("LM3", "LM2"),
        ("LM2", "LM1"),
    ]


def test_route_attach_near_lm1_needs_no_extra_loop():
    stations = load_station_poses(ROOT / "map" / "wenshi.smap")
    segments = build_closed_route(stations, ["LM1", "LM4", "LM3", "LM2"])

    index, _cross_track, distance = nearest_route_segment(
        {"x": stations["LM1"][0] + 0.02, "y": stations["LM1"][1] + 0.01, "angle": 0.0},
        segments,
    )

    assert index == 0
    assert distance < 0.10


def test_route_attachment_snaps_to_nearby_station_heading():
    stations = load_station_poses(ROOT / "map" / "wenshi.smap")
    segments = build_closed_route(stations, ["LM1", "LM4", "LM3", "LM2"])

    # This is the field-test status observed just after reaching LM4.  A
    # nearest-line tie must not send the robot back over LM1->LM4.
    index, station = route_attachment(
        {"x": 3.2356, "y": -0.0339, "angle": -1.5334},
        stations,
        ["LM1", "LM4", "LM3", "LM2"],
        segments,
        station_snap_m=0.25,
    )

    assert index == 1
    assert station == "LM4"


def test_blocked_recovery_waits_then_resumes_without_emergency():
    action, clear_since = blocked_recovery_state(
        {"blocked": True, "emergency": False}, None, now=10.0, clear_s=2.0
    )
    assert action == "pause"
    assert clear_since is None

    action, clear_since = blocked_recovery_state(
        {"blocked": False, "emergency": False}, None, now=11.0, clear_s=2.0
    )
    assert action == "waiting"
    assert clear_since == 11.0

    action, _ = blocked_recovery_state(
        {"blocked": False, "emergency": False}, clear_since, now=13.1, clear_s=2.0
    )
    assert action == "resume"


def test_blocked_recovery_aborts_emergency():
    action, _ = blocked_recovery_state(
        {"blocked": False, "emergency": True}, None, now=0.0, clear_s=2.0
    )
    assert action == "abort"


def test_field_route_speed_uses_dedicated_test_speed():
    config = {
        "agv": {"ip": "127.0.0.1", "status_port": 19204, "motion_port": 19205},
        "control": {"test_speed_mps": 0.05},
        "field_test": {"route_speed_mps": 0.10},
        "safety": {"hard_cross_track_m": 0.25},
        "map": {"smap_file": "../map/wenshi.smap"},
        "route": {"station_order": ["LM1", "LM4", "LM3", "LM2"]},
        "_config_dir": str(ROOT / "config"),
    }
    runner = RouteRunner(config, lambda _message: None)
    assert runner.speed == 0.10


def test_validate_viewpoints_requires_all_eight_and_six_joints(tmp_path):
    names = ("home_safe", "camera", "camera_left", "camera_right", "left_pre", "left_photo", "right_pre", "right_photo")
    value = {name: {"joint": [0, 0, 0, 0, 0, 0]} for name in names[:-1]}
    path = tmp_path / "viewpoints.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    errors = validate_viewpoints(path)

    assert any("right_photo" in item for item in errors)


def test_field_session_starts_route_test_without_blocking_console(monkeypatch, tmp_path):
    from yubei.field_test import FieldTestSession

    session = FieldTestSession.__new__(FieldTestSession)
    session.route = type("Route", (), {})()
    session.route.stop_event = __import__("threading").Event()
    session.route.run_one_loop = lambda: {"ok": True, "error": ""}
    session.event = lambda *_args, **_kwargs: None
    session._route_thread = None
    session._route_result = None

    assert session.start_route_test() is True
    session._route_thread.join(timeout=1.0)
    assert session._route_result["ok"] is True


def test_field_session_starts_arm_test_without_blocking_console(monkeypatch):
    from yubei import field_test as field_test_module

    class FakeTester:
        def __init__(self, *_args, **_kwargs):
            self.client = type("Client", (), {"stop": lambda _self: None})()

        def run(self, _path, input_stream, _output_stream):
            assert input_stream.readline() == "\n"
            return {"ok": True, "completed": ["home_safe"], "error": ""}

    session = field_test_module.FieldTestSession.__new__(field_test_module.FieldTestSession)
    session.config = {}
    session.log = lambda *_args, **_kwargs: None
    session.event = lambda *_args, **_kwargs: None
    session.staged_viewpoints = "viewpoints.json"
    session._arm_thread = None
    session._arm_result = None
    session._arm_commands = __import__("queue").Queue()
    monkeypatch.setattr(field_test_module, "ArmPointTester", FakeTester)

    assert session.start_arm_test(__import__("sys").stdout) is True
    session._arm_commands.put("\n")
    session._arm_thread.join(timeout=1.0)
    assert session._arm_result["ok"] is True


def test_field_teach_retries_the_same_point_after_snapshot_failure(monkeypatch, tmp_path):
    from yubei import field_test as field_test_module

    class FlakyClient:
        calls = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def read_snapshot(self):
            FlakyClient.calls += 1
            if FlakyClient.calls == 1:
                raise TimeoutError("JAKA response timeout")
            return [float(FlakyClient.calls)] * 6, [0.0] * 6

    class FakePreview:
        def start(self):
            pass

        def snapshot(self, _path):
            return True

    class FakeTeachingSession:
        def save(self, _name, joint, tcp):
            assert len(joint) == 6

    session = field_test_module.FieldTestSession.__new__(field_test_module.FieldTestSession)
    session.config = {"jaka": {"ip": "127.0.0.1", "port": 10001}}
    session.preview = FakePreview()
    session.run_dir = tmp_path
    session.teach_session = FakeTeachingSession()
    session.event = lambda *_args, **_kwargs: None

    monkeypatch.setattr(field_test_module, "TeachingClient", FlakyClient)
    output = __import__("io").StringIO()
    saved = session.teach(__import__("io").StringIO("\n" * 9), output)

    assert saved == len(field_test_module.VIEWPOINT_NAMES)
    assert "保存失败" in output.getvalue()


def test_field_teach_records_point_name_and_continues_after_first_save(monkeypatch, tmp_path):
    from yubei import field_test as field_test_module

    class SnapshotClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def read_snapshot(self):
            return [1.0] * 6, [0.0] * 6

    class FakePreview:
        def start(self):
            return None

        def snapshot(self, _path):
            return True

    class FakeTeachingSession:
        def save(self, _name, _joint, _tcp):
            return None

    session = field_test_module.FieldTestSession.__new__(field_test_module.FieldTestSession)
    session.config = {"jaka": {"ip": "127.0.0.1", "port": 10001}}
    session.preview = FakePreview()
    session.run_dir = tmp_path
    session.teach_session = FakeTeachingSession()
    session.events = tmp_path / "events.jsonl"
    monkeypatch.setattr(field_test_module, "TeachingClient", SnapshotClient)

    output = __import__("io").StringIO()
    saved = session.teach(__import__("io").StringIO("\nq\n"), output)

    assert saved == 1
    event = json.loads(session.events.read_text(encoding="utf-8"))
    assert event["event"] == "teach_saved"
    assert event["name"] == "home_safe"
    assert event["time"]
    assert "请人工移动到 camera" in output.getvalue()


def test_console_input_uses_controlling_tty_when_stdin_is_not_a_tty():
    class RedirectedInput:
        def isatty(self):
            return False

    tty = object()
    opened = []

    def open_tty(path, mode, encoding=None):
        opened.append((path, mode, encoding))
        return tty

    stream, owned = resolve_console_input(RedirectedInput(), open_tty)

    assert stream is tty
    assert owned is tty
    assert opened == [("/dev/tty", "r", "utf-8")]


def test_console_input_keeps_terminal_stdin():
    class TerminalInput:
        def isatty(self):
            return True

    stream = TerminalInput()
    selected, owned = resolve_console_input(stream, lambda *_args, **_kwargs: AssertionError())

    assert selected is stream
    assert owned is None
