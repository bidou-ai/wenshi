import json
from pathlib import Path

from yubei.daily_check import run_daily_check


def _write_dataset(root: Path, dataset_type: str, name: str, *, valid: bool) -> None:
    session = root / "yubei" / "data" / dataset_type / name
    (session / "images").mkdir(parents=True)
    (session / "labels").mkdir()
    (session / "images" / "0001.jpg").write_bytes(b"not decoded")
    (session / "labels" / "0001.json").write_text(
        json.dumps({"status": "labelled" if valid else "broken"}),
        encoding="utf-8",
    )
    (session / "manifest.json").write_text(
        json.dumps(
            {
                "dataset_type": dataset_type,
                "classes": {"rice_plant": 0}
                if dataset_type == "plant"
                else {"panicle": 0},
            }
        ),
        encoding="utf-8",
    )


def test_daily_check_fails_when_an_older_session_is_invalid(tmp_path, capsys):
    _write_dataset(tmp_path, "plant", "dataset_20260901_old", valid=False)
    _write_dataset(tmp_path, "plant", "dataset_20260908_new", valid=True)

    result = run_daily_check(tmp_path)

    output = capsys.readouterr().out
    assert result != 0
    assert "dataset_20260901_old" in output
    assert "dataset_20260908_new" in output


def test_daily_check_fails_for_empty_dataset_session(tmp_path):
    session = tmp_path / "yubei" / "data" / "plant" / "dataset_empty"
    (session / "images").mkdir(parents=True)
    (session / "labels").mkdir()
    (session / "manifest.json").write_text(
        json.dumps({"dataset_type": "plant", "classes": {"rice_plant": 0}}),
        encoding="utf-8",
    )

    assert run_daily_check(tmp_path) != 0
