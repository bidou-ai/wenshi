import json
from pathlib import Path

import pytest

from yubei.paths import SessionPaths, load_json, save_json_atomic


def test_session_paths_create_one_session_tree(tmp_path: Path):
    session = SessionPaths.create(tmp_path)
    assert session.root.parent == tmp_path.resolve()
    assert session.images_dir.is_dir()
    assert session.labels_dir.is_dir()
    assert session.ambiguous_dir.is_dir()
    assert session.manifest_path.name == "manifest.json"


def test_session_paths_create_does_not_overwrite_existing_session(tmp_path: Path):
    first = SessionPaths.create(tmp_path, prefix="capture")
    second = SessionPaths.create(tmp_path, prefix="capture")
    assert first.root != second.root


def test_atomic_json_round_trip(tmp_path: Path):
    path = tmp_path / "nested" / "value.json"
    save_json_atomic(path, {"ok": True, "items": [1, 2]})
    assert load_json(path) == {"ok": True, "items": [1, 2]}
    assert not list(path.parent.glob(".*.tmp-*"))


def test_load_json_rejects_non_object(tmp_path: Path):
    path = tmp_path / "value.json"
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_json(path)
