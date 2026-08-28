from pathlib import Path
import subprocess

import cv2
import numpy as np

from yubei.package_labeler import package_windows_labeler


def _session(tmp_path: Path) -> Path:
    session = tmp_path / "dataset_20260826_120000"
    (session / "images").mkdir(parents=True)
    (session / "labels").mkdir()
    (session / "ambiguous").mkdir()
    assert cv2.imwrite(str(session / "images" / "a.jpg"), np.zeros((120, 160, 3), dtype=np.uint8))
    (session / "manifest.json").write_text('{"images":[{"filename":"images/a.jpg"}]}\n', encoding="utf-8")
    return session


def test_windows_labeler_package_contains_runnable_labeling_app(tmp_path):
    output = package_windows_labeler(_session(tmp_path), tmp_path / "export")

    assert (output / "start_label_windows.bat").is_file()
    assert (output / "README_WINDOWS_LABELING.md").is_file()
    assert (output / "yubei" / "label_server.py").is_file()
    assert (output / "yubei" / "paths.py").is_file()
    assert (output / "yubei" / "label_ui" / "app.js").is_file()
    assert (output / "dataset" / "images" / "a.jpg").is_file()
    assert (output / "dataset" / "labels").is_dir()

    result = subprocess.run(
        ["python3", str(output / "yubei" / "label_server.py"), "--help"],
        cwd=output,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0
    assert "标注" in result.stdout
