import json
import io
from pathlib import Path

import pytest

import yubei.teach_viewpoints as teach_viewpoints
from yubei.teach_protocol import TeachingClient
from yubei.teach_viewpoints import TeachingSession, VIEWPOINT_NAMES
from yubei.viewpoint_verify import publish_viewpoints, verify_viewpoints


def _viewpoints(include_home=True):
    value = {}
    for index, name in enumerate(VIEWPOINT_NAMES):
        if name == "home_safe" and not include_home:
            continue
        value[name] = {"joint": [float(index)] * 6, "tcp": None}
    return value


def test_verify_requires_all_eight_points(tmp_path):
    path = tmp_path / "viewpoints.json"
    path.write_text(json.dumps(_viewpoints(False)), encoding="utf-8")
    report = verify_viewpoints(path)
    assert report.ok is False
    assert any("home_safe" in item for item in report.errors)


def test_teaching_session_saves_six_joints(tmp_path):
    path = tmp_path / "staged.json"
    session = TeachingSession(path)
    saved = session.save("home_safe", [1, 2, 3, 4, 5, 6], None)
    assert saved["joint"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert json.loads(path.read_text(encoding="utf-8"))["home_safe"]["joint"][0] == 1.0


def test_capture_all_viewpoints_uses_one_read_only_session(tmp_path):
    class FakeClient:
        def __init__(self):
            self.reads = 0

        def read_joint(self):
            self.reads += 1
            return [float(self.reads)] * 6

        def read_tcp(self):
            return [0.0] * 6

    output = io.StringIO()
    session = TeachingSession(tmp_path / "staged.json")

    saved = teach_viewpoints.capture_all_viewpoints(
        FakeClient(),
        session,
        io.StringIO("\n" * len(VIEWPOINT_NAMES)),
        output,
    )

    assert saved == len(VIEWPOINT_NAMES)
    value = json.loads(session.path.read_text(encoding="utf-8"))
    assert set(VIEWPOINT_NAMES).issubset(value)
    assert "不会发送运动命令" in output.getvalue()


def test_teaching_client_accepts_fragmented_json_response():
    class FragmentedSocket:
        def __init__(self):
            self.chunks = [b'{"jointPos', b'ition":[1,2,3,4,5,6]}\n']
            self.sent = []

        def sendall(self, payload):
            self.sent.append(payload)

        def recv(self, _size):
            return self.chunks.pop(0)

    client = TeachingClient("127.0.0.1")
    client.sock = FragmentedSocket()

    assert client.read_joint() == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


def test_verify_rejects_large_adjacent_joint_step(tmp_path):
    path = tmp_path / "viewpoints.json"
    values = _viewpoints()
    values["camera_left"]["joint"][0] = 999
    path.write_text(json.dumps(values), encoding="utf-8")
    report = verify_viewpoints(path, max_joint_step_deg=120)
    assert report.ok is False
    assert any("超过" in item for item in report.errors)


def test_verify_reports_non_numeric_joint_value(tmp_path):
    path = tmp_path / "viewpoints.json"
    values = _viewpoints()
    values["camera"]["joint"][0] = "not-a-number"
    path.write_text(json.dumps(values), encoding="utf-8")

    report = verify_viewpoints(path)

    assert report.ok is False
    assert any("关节值" in item for item in report.errors)


def test_publish_makes_backup_before_replace(tmp_path):
    formal = tmp_path / "formal.json"
    formal.write_text(json.dumps({"old": True}), encoding="utf-8")
    staged = tmp_path / "staged.json"
    staged.write_text(json.dumps(_viewpoints()), encoding="utf-8")
    backup = publish_viewpoints(staged, formal, tmp_path / "backups")
    assert backup.is_file()
    assert json.loads(formal.read_text(encoding="utf-8"))["home_safe"]
    assert list((tmp_path / "backups").glob("formal.json.*"))
