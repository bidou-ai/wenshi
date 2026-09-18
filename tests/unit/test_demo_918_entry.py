from pathlib import Path
import fcntl
import os
import shutil
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_918_entry_and_modules_are_independent_from_old_demo_paths():
    script = ROOT / "9.18.sh"
    runtime = ROOT / "app" / "wenshi_patrol" / "demo_918.py"
    setup = ROOT / "app" / "wenshi_patrol" / "demo_918_setup.py"
    assert script.is_file() and runtime.is_file() and setup.is_file()
    text = script.read_text(encoding="utf-8")
    assert "runtime/9.18" in text
    assert "runtime/demo" not in text
    assert "runtime/height_tests" in text  # forbidden-path guard only
    assert "wenshi.sh" not in text
    assert "wenshi-demo-hardware.lock" in text
    assert "wenshi_patrol.demo_918" in text
    assert "from .demo import" not in runtime.read_text(encoding="utf-8")
    assert "tags" not in setup.read_text(encoding="utf-8").lower()


def test_918_config_and_rviz_use_only_demo918_topics():
    config_path = ROOT / "config" / "9.18.yaml"
    rviz_path = ROOT / "config" / "9.18.rviz"
    assert config_path.is_file() and rviz_path.is_file()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "demo_918" in config
    assert "demo" not in config
    assert "plants" not in config
    assert "april_tag" not in config
    assert all(str(value).startswith("/demo918/") for value in config["topics"].values())
    rviz = rviz_path.read_text(encoding="utf-8")
    assert "/demo918/map" in rviz
    assert "/demo918/agv_pose" in rviz
    assert "/demo918/markers" in rviz
    assert "/demo918/camera/color" in rviz
    assert "/rice/" not in rviz


def test_918_dry_run_requires_its_own_setup(tmp_path):
    environment = dict(os.environ)
    environment["WENSHI_918_SETUP"] = str(tmp_path / "missing.json")

    result = subprocess.run(
        ["bash", "9.18.sh", "--dry-run", "--no-camera", "--no-rviz"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "./9.18.sh --setup --map" in result.stdout + result.stderr


def test_918_shell_rejects_invalid_map_and_setup_combinations(tmp_path):
    cases = (
        (["--map", str(tmp_path / "site.smap")], "只能与 --setup"),
        (["--setup", "--dry-run", "--no-camera"], "不能同时"),
        (["--setup", "--resume", str(tmp_path), "--map", str(tmp_path / "site.smap")], "不能同时"),
    )
    for arguments, message in cases:
        result = subprocess.run(
            ["bash", "9.18.sh", *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert message in result.stdout + result.stderr


def test_918_shell_rejects_old_demo_and_height_runtime_paths():
    for forbidden in ("runtime/demo/demo_setup.json", "runtime/height_tests/demo_setup.json"):
        result = subprocess.run(
            ["bash", "9.18.sh", "--setup-file", forbidden, "--dry-run"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert "9.18 独立目录" in result.stdout + result.stderr


def test_918_dry_run_accepts_dynamic_independent_setup(tmp_path):
    from test_demo_918_setup import complete_918_setup

    setup_path = tmp_path / "runtime" / "9.18" / "demo_setup.json"
    complete_918_setup(tmp_path, mode="shuttle", observation_count=2).publish(setup_path)
    environment = dict(os.environ)
    environment["WENSHI_918_SETUP"] = str(setup_path)
    environment["WENSHI_918_RUNTIME"] = str(setup_path.parent)

    result = subprocess.run(
        ["bash", "9.18.sh", "--dry-run", "--no-camera", "--no-rviz"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"setup={setup_path}" in result.stdout
    assert "route_mode=shuttle" in result.stdout
    assert "observations=2" in result.stdout
    assert "no hardware connection" in result.stdout
    assert "camera_bridge=disabled" in result.stdout
    assert "rviz=disabled" in result.stdout


def test_918_runtime_root_option_drives_default_setup_and_photos(tmp_path):
    from test_demo_918_setup import complete_918_setup

    runtime_root = tmp_path / "isolated-918"
    setup_path = runtime_root / "demo_setup.json"
    source_root = tmp_path / "runtime" / "9.18"
    complete_918_setup(tmp_path, mode="shuttle", observation_count=1).publish(
        source_root / "demo_setup.json"
    )
    shutil.copytree(source_root, runtime_root)

    result = subprocess.run(
        [
            "bash",
            "9.18.sh",
            "--runtime-root",
            str(runtime_root),
            "--dry-run",
            "--no-camera",
            "--no-rviz",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"setup={setup_path}" in result.stdout
    assert f"photos={runtime_root / 'photos'}" in result.stdout


def test_918_setup_uses_same_hardware_lock_as_wenshi(tmp_path):
    from test_demo_918_setup import _write_map

    lock_path = tmp_path / "wenshi-demo-hardware.lock"
    environment = dict(os.environ)
    environment["WENSHI_918_LOCK"] = str(lock_path)
    environment["WENSHI_918_RUNTIME"] = str(tmp_path / "runtime" / "9.18")
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(
            ["bash", "9.18.sh", "--setup", "--map", str(_write_map(tmp_path / "site.smap")), "--no-camera"],
            cwd=ROOT,
            env=environment,
            input="",
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 2
    assert "另一个 Wenshi Demo" in result.stdout + result.stderr
