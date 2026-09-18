from pathlib import Path
import fcntl
import os
import subprocess


def test_wenshi_shell_has_dry_run_and_demo_commands():
    path = Path("wenshi.sh")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "--dry-run" in text
    assert "camera_bridge" in text
    assert "rviz2" in text
    assert "runtime/demo" in text


def test_wenshi_dry_run_requires_its_own_demo_setup(tmp_path):
    environment = dict(os.environ)
    environment["WENSHI_DEMO_SETUP"] = str(tmp_path / "missing-demo-setup.json")
    result = subprocess.run(
        ["bash", "wenshi.sh", "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "./wenshi.sh --setup" in output
    assert "height_tests" not in output


def test_wenshi_rejects_setup_and_dry_run_together():
    result = subprocess.run(
        ["bash", "wenshi.sh", "--setup", "--dry-run", "--no-camera"],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "不能同时" in result.stdout + result.stderr


def test_wenshi_setup_resume_rejects_missing_evidence_directory(tmp_path):
    environment = dict(os.environ)
    environment["WENSHI_DEMO_LOCK"] = str(tmp_path / "wenshi-demo.lock")
    missing = tmp_path / "runtime" / "demo" / "setup_missing"

    result = subprocess.run(
        ["bash", "wenshi.sh", "--setup", "--resume", str(missing), "--no-camera"],
        input="",
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "恢复目录不存在" in output
    assert "Traceback" not in output


def test_wenshi_rejects_skip_c_tags_without_setup():
    result = subprocess.run(
        ["bash", "wenshi.sh", "--skip-c-tags"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "只能与 --setup" in result.stdout + result.stderr


def test_wenshi_rejects_paths_inside_formal_height_test_runtime():
    result = subprocess.run(
        [
            "bash",
            "wenshi.sh",
            "--setup",
            "--no-camera",
            "--setup-file",
            "runtime/height_tests/demo_setup.json",
        ],
        input="",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "正式检测目录" in result.stdout + result.stderr


def test_wenshi_rejects_second_hardware_instance(tmp_path):
    lock_path = tmp_path / "wenshi-demo.lock"
    environment = dict(os.environ)
    environment["WENSHI_DEMO_LOCK"] = str(lock_path)
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            ["bash", "wenshi.sh", "--setup", "--no-camera"],
            input="",
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    assert result.returncode == 2
    assert "另一个 Wenshi Demo" in result.stdout + result.stderr


def test_wenshi_dry_run_accepts_independent_demo_setup(tmp_path):
    from test_demo_setup import _complete_session

    setup_path = tmp_path / "runtime" / "demo" / "demo_setup.json"
    _complete_session(tmp_path).publish(setup_path)
    environment = dict(os.environ)
    environment["WENSHI_DEMO_SETUP"] = str(setup_path)

    result = subprocess.run(
        ["bash", "wenshi.sh", "--dry-run", "--no-camera", "--no-rviz"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"setup={setup_path}" in result.stdout
    assert "no hardware connection" in result.stdout
