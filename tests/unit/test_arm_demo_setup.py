import json
import hashlib
import math
from pathlib import Path

import numpy as np
import pytest


POSES = (
    "home_safe",
    "left_clearance",
    "rice1_pre",
    "rice1_center",
    "rice1_scan_a",
    "rice1_scan_b",
    "rice2_pre",
    "rice2_center",
    "rice2_scan_a",
    "rice2_scan_b",
)


def _refresh_sha(payload):
    unsigned = {key: value for key, value in payload.items() if key != "sha256"}
    encoded = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload["sha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _session(tmp_path: Path):
    from wenshi_patrol.arm_demo_setup import ArmDemoSetupSession

    runtime = tmp_path / "runtime" / "arm_demo"
    return ArmDemoSetupSession(runtime / "setup_20260919_120000", runtime)


def _record_all(session, *, image=None):
    image = np.full((8, 10, 3), 128, dtype=np.uint8) if image is None else image
    for index, name in enumerate(POSES):
        base = [float(index)] * 6
        tcp_x = {
            "rice1_center": 100.0,
            "rice1_scan_a": 150.0,
            "rice1_scan_b": 180.0,
            "rice2_center": 500.0,
            "rice2_scan_a": 550.0,
            "rice2_scan_b": 580.0,
        }.get(name, 0.0)
        tcp = [tcp_x, 0.0, 300.0, 0.0, 0.0, 0.0]
        session.record_pose(name, [base, base[:], base[:]], tcp, image)


def test_arm_setup_publishes_complete_schema_and_loads(tmp_path):
    from wenshi_patrol.arm_demo_setup import POSE_NAMES, load_arm_demo_setup

    assert POSE_NAMES == POSES
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    destination = session.publish()

    assert destination == session.runtime_root / "demo_setup.json"
    loaded = load_arm_demo_setup(destination)
    assert loaded["source"] == str(destination.resolve())
    assert loaded["raw"]["kind"] == "wenshi_arm_panicle_demo"
    assert list(loaded["poses"]) == list(POSES)
    assert loaded["poses"]["rice1_center"]["joint"] == [3.0] * 6
    assert (session.root / "poses" / "home_safe.jpg").is_file()


def test_arm_setup_rejects_joint_sample_spread_over_point_two_degrees(tmp_path):
    session = _session(tmp_path)
    samples = [[0.0] * 6, [0.21, 0, 0, 0, 0, 0], [0.0] * 6]
    with pytest.raises(ValueError, match="0.20"):
        session.record_pose("home_safe", samples, [0.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))


def test_arm_setup_requires_pose_order_and_rejects_duplicates(tmp_path):
    session = _session(tmp_path)
    image = np.ones((2, 2, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="顺序"):
        session.record_pose("rice1_pre", [[0.0] * 6] * 3, [0.0] * 6, image)
    session.record_pose("home_safe", [[0.0] * 6] * 3, [0.0] * 6, image)
    with pytest.raises(ValueError, match="顺序|重复"):
        session.record_pose("home_safe", [[0.0] * 6] * 3, [0.0] * 6, image)


def test_arm_setup_rejects_missing_pose_on_publish(tmp_path):
    session = _session(tmp_path)
    session.record_pose("home_safe", [[0.0] * 6] * 3, [0.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="完整|缺少"):
        session.publish()


def test_arm_setup_resume_restores_draft(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    session.record_pose("home_safe", [[1.0] * 6] * 3, [1.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    resumed = type(session).resume(session.root, session.runtime_root)
    assert resumed.operator == "operator-1"
    assert list(resumed.poses) == ["home_safe"]
    assert resumed.poses["home_safe"]["joint"] == [1.0] * 6


def test_arm_setup_resume_rejects_joint_sample_spread_over_point_two_degrees(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    session.record_pose("home_safe", [[1.0] * 6] * 3, [1.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    draft = json.loads(session.draft_path.read_text(encoding="utf-8"))
    draft["poses"]["home_safe"]["joint_samples"][1][0] = 1.21
    session.draft_path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="0.20"):
        type(session).resume(session.root, session.runtime_root)


def test_arm_setup_resume_rejects_joint_that_differs_from_sample_mean(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    session.record_pose("home_safe", [[1.0] * 6] * 3, [1.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    draft = json.loads(session.draft_path.read_text(encoding="utf-8"))
    draft["poses"]["home_safe"]["joint"][0] = 1.0000001
    session.draft_path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="平均"):
        type(session).resume(session.root, session.runtime_root)


def test_arm_setup_resume_requires_canonical_own_pose_photo_path(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    draft = json.loads(session.draft_path.read_text(encoding="utf-8"))
    draft["poses"]["left_clearance"]["photo"] = draft["poses"]["home_safe"]["photo"]
    session.draft_path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="照片|路径"):
        type(session).resume(session.root, session.runtime_root)


@pytest.mark.parametrize("updated_at", [None, "", "not-an-iso-timestamp"])
def test_arm_setup_resume_requires_parseable_updated_at(tmp_path, updated_at):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    session.record_pose("home_safe", [[1.0] * 6] * 3, [1.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    draft = json.loads(session.draft_path.read_text(encoding="utf-8"))
    draft["updated_at"] = updated_at
    session.draft_path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="时间"):
        type(session).resume(session.root, session.runtime_root)


@pytest.mark.parametrize(
    "photo_value, corrupt_photo",
    [
        ("", False),
        ("../../outside.jpg", False),
        ("poses/missing.jpg", False),
        ("poses/home_safe.jpg", True),
    ],
)
def test_arm_setup_resume_revalidates_photo_reference(tmp_path, photo_value, corrupt_photo):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    session.record_pose("home_safe", [[1.0] * 6] * 3, [1.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    if corrupt_photo:
        (session.root / photo_value).write_bytes(b"not-an-image")
    draft = json.loads(session.draft_path.read_text(encoding="utf-8"))
    draft["poses"]["home_safe"]["photo"] = photo_value
    session.draft_path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="照片|目录|runtime|读取"):
        type(session).resume(session.root, session.runtime_root)


def test_arm_setup_atomic_draft_does_not_follow_preexisting_tmp_symlink(tmp_path):
    session = _session(tmp_path)
    sentinel = session.runtime_root / "sentinel.json"
    sentinel.write_text("sentinel", encoding="utf-8")
    fixed_tmp = session.draft_path.with_name(session.draft_path.name + ".tmp")
    fixed_tmp.symlink_to(sentinel)
    session.set_operator("operator-1")
    assert sentinel.read_text(encoding="utf-8") == "sentinel"
    assert json.loads(session.draft_path.read_text(encoding="utf-8"))["operator"] == "operator-1"


def test_arm_setup_photo_write_does_not_follow_preexisting_tmp_symlink(tmp_path):
    session = _session(tmp_path)
    sentinel = tmp_path / "sentinel.jpg"
    sentinel.write_bytes(b"sentinel")
    poses_dir = session.root / "poses"
    poses_dir.mkdir(parents=True)
    (poses_dir / "home_safe.tmp.jpg").symlink_to(sentinel)
    session.record_pose("home_safe", [[0.0] * 6] * 3, [0.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    assert sentinel.read_bytes() == b"sentinel"


def test_arm_setup_rejects_integer_overflow_as_invalid_vector(tmp_path):
    session = _session(tmp_path)
    with pytest.raises(ValueError, match="有限"):
        session.record_pose(
            "home_safe",
            [[10**1000] + [0.0] * 5] * 3,
            [0.0] * 6,
            np.ones((2, 2, 3), dtype=np.uint8),
        )


def test_arm_setup_resume_rejects_empty_operator(tmp_path):
    session = _session(tmp_path)
    session.record_pose("home_safe", [[0.0] * 6] * 3, [0.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    draft = json.loads(session.draft_path.read_text(encoding="utf-8"))
    draft["operator"] = ""
    session.draft_path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="操作者"):
        type(session).resume(session.root, session.runtime_root)


def test_arm_setup_resume_rejects_non_mapping_pose_record(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    session.record_pose("home_safe", [[0.0] * 6] * 3, [0.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    draft = json.loads(session.draft_path.read_text(encoding="utf-8"))
    draft["poses"]["home_safe"] = []
    session.draft_path.write_text(json.dumps(draft), encoding="utf-8")
    with pytest.raises(ValueError, match="记录"):
        type(session).resume(session.root, session.runtime_root)


def test_arm_setup_loader_rejects_empty_operator(tmp_path):
    from wenshi_patrol.arm_demo_setup import load_arm_demo_setup

    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    destination = session.publish()
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["operator"] = ""
    _refresh_sha(payload)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="操作者"):
        load_arm_demo_setup(destination)


def test_arm_setup_publish_replaces_destination_symlink_without_touching_target(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    sentinel = session.runtime_root / "sentinel.json"
    sentinel.write_text("sentinel", encoding="utf-8")
    destination = session.runtime_root / "demo_setup.json"
    destination.symlink_to(sentinel)
    with pytest.raises(ValueError, match="符号链接|目录|runtime"):
        session.publish()
    assert sentinel.read_text(encoding="utf-8") == "sentinel"
    assert destination.is_symlink()


def test_arm_setup_accepts_field_taught_scans_within_200mm_and_30deg(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    session.poses["rice1_scan_a"]["tcp"][0] = 265.3
    session.poses["rice1_scan_b"]["joint"][0] = 31.4
    for sample in session.poses["rice1_scan_b"]["joint_samples"]:
        sample[0] = 31.4

    assert session.publish() == session.runtime_root / "demo_setup.json"


def test_arm_setup_rejects_scan_tcp_beyond_center_by_200mm(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    session.poses["rice1_scan_a"]["tcp"][0] = 400.1
    with pytest.raises(ValueError, match="200"):
        session.publish()


def test_arm_setup_rejects_scan_joint_beyond_center_by_30deg(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    session.poses["rice2_scan_b"]["joint"][0] = 40.1
    for sample in session.poses["rice2_scan_b"]["joint_samples"]:
        sample[0] = 40.1
    with pytest.raises(ValueError, match="30"):
        session.publish()


def test_arm_setup_publish_revalidates_joint_sample_spread(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    session.poses["home_safe"]["joint_samples"][1][0] = 0.25
    with pytest.raises(ValueError, match="0.20"):
        session.publish()


def test_arm_setup_publish_rejects_joint_that_differs_from_sample_mean(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    session.poses["home_safe"]["joint"][0] = 0.0000001
    with pytest.raises(ValueError, match="平均"):
        session.publish()


def test_arm_setup_publish_rejects_photo_from_another_setup(tmp_path):
    from wenshi_patrol.arm_demo_setup import ArmDemoSetupSession

    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    other_root = session.runtime_root / "setup_other"
    other = ArmDemoSetupSession(other_root, session.runtime_root)
    other.record_pose("home_safe", [[0.0] * 6] * 3, [0.0] * 6, np.ones((2, 2, 3), dtype=np.uint8))
    session.poses["home_safe"]["photo"] = "setup_other/poses/home_safe.jpg"
    with pytest.raises(ValueError, match="证据|照片|setup"):
        session.publish()


def test_arm_setup_publish_requires_canonical_own_pose_photo_path(tmp_path):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    session.poses["left_clearance"]["photo"] = session.poses["home_safe"]["photo"]
    with pytest.raises(ValueError, match="照片|路径"):
        session.publish()


def test_arm_setup_loader_rejects_tampered_publish(tmp_path):
    from wenshi_patrol.arm_demo_setup import load_arm_demo_setup

    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    destination = session.publish()
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["poses"]["home_safe"]["joint"][0] = 99.0
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="完整性|篡改|SHA"):
        load_arm_demo_setup(destination)


def test_arm_setup_loader_rejects_joint_that_differs_from_sample_mean(tmp_path):
    from wenshi_patrol.arm_demo_setup import load_arm_demo_setup

    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    destination = session.publish()
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["poses"]["home_safe"]["joint"][0] = 0.0000001
    _refresh_sha(payload)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="平均"):
        load_arm_demo_setup(destination)


def test_arm_setup_loader_requires_canonical_own_pose_photo_path(tmp_path):
    from wenshi_patrol.arm_demo_setup import load_arm_demo_setup

    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    destination = session.publish()
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["poses"]["left_clearance"]["photo"] = payload["poses"]["home_safe"]["photo"]
    _refresh_sha(payload)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="照片|路径"):
        load_arm_demo_setup(destination)


def test_arm_setup_loader_requires_all_photos_from_one_setup_directory(tmp_path):
    from wenshi_patrol.arm_demo_setup import ArmDemoSetupSession, load_arm_demo_setup

    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    destination = session.publish()
    other = ArmDemoSetupSession(session.runtime_root / "setup_other", session.runtime_root)
    other.set_operator("operator-2")
    _record_all(other)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["poses"]["left_clearance"]["photo"] = other.poses["left_clearance"]["photo"]
    _refresh_sha(payload)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="setup|照片|路径"):
        load_arm_demo_setup(destination)


@pytest.mark.parametrize("published_at", [None, "", "not-an-iso-timestamp"])
def test_arm_setup_loader_requires_parseable_published_at(tmp_path, published_at):
    from wenshi_patrol.arm_demo_setup import load_arm_demo_setup

    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    destination = session.publish()
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["published_at"] = published_at
    _refresh_sha(payload)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="时间"):
        load_arm_demo_setup(destination)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_arm_setup_rejects_non_finite_values(tmp_path, value):
    session = _session(tmp_path)
    session.set_operator("operator-1")
    with pytest.raises(ValueError, match="有限|非有限"):
        session.record_pose(
            "home_safe",
            [[value] + [0.0] * 5] * 3,
            [0.0] * 6,
            np.ones((2, 2, 3), dtype=np.uint8),
        )


def test_arm_setup_loader_rejects_photo_outside_runtime(tmp_path):
    from wenshi_patrol.arm_demo_setup import load_arm_demo_setup

    session = _session(tmp_path)
    session.set_operator("operator-1")
    _record_all(session)
    destination = session.publish()
    payload = json.loads(destination.read_text(encoding="utf-8"))
    payload["poses"]["home_safe"]["photo"] = "../../outside.jpg"
    _refresh_sha(payload)
    destination.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="目录|runtime"):
        load_arm_demo_setup(destination)
