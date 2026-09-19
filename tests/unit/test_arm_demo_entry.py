"""Black-box checks for the standalone fixed-base arm demonstration entry."""

from __future__ import annotations

from pathlib import Path
import os
import subprocess

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "arm_demo.sh"
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


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "app")
    return environment


def _complete_setup(tmp_path: Path) -> tuple[Path, Path]:
    from wenshi_patrol.arm_demo_setup import ArmDemoSetupSession

    runtime = tmp_path / "runtime" / "arm_demo"
    session = ArmDemoSetupSession(runtime / "setup_fixture", runtime)
    session.set_operator("entry-test")
    image = np.full((8, 10, 3), 128, dtype=np.uint8)
    for index, name in enumerate(POSES):
        tcp_x = {
            "rice1_center": 100.0,
            "rice1_scan_a": 150.0,
            "rice1_scan_b": 180.0,
            "rice2_center": 500.0,
            "rice2_scan_a": 550.0,
            "rice2_scan_b": 580.0,
        }.get(name, 0.0)
        joint = [float(index)] * 6
        session.record_pose(name, [joint, joint[:], joint[:]], [tcp_x, 0, 300, 0, 0, 0], image)
    return runtime, session.publish()


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(LAUNCHER), *args],
        cwd=ROOT,
        env=env or _environment(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_arm_demo_launcher_is_executable_and_isolated_from_other_demos():
    assert LAUNCHER.is_file()
    assert LAUNCHER.stat().st_mode & 0o111
    text = LAUNCHER.read_text(encoding="utf-8")
    assert "runtime/arm_demo" in text
    assert "wenshi-demo-hardware.lock" in text
    assert "exec python3 -m wenshi_patrol.arm_demo" in text
    for forbidden in ("agv", "ros", "rviz", "camera_bridge"):
        assert forbidden not in text.lower()


def test_arm_demo_config_only_declares_arm_demo_dependencies():
    config = ROOT / "config" / "arm_demo.yaml"
    assert config.is_file()
    text = config.read_text(encoding="utf-8")
    for forbidden in ("agv:", "map:", "route:", "topics:"):
        assert forbidden not in text
    assert "../yubei/training/panicle/panicle_20260904_134044/weights/best.pt" in text
    assert "motion_enable: false" in text


def test_arm_demo_dry_run_validates_fixture_without_hardware_connection(tmp_path):
    runtime, setup = _complete_setup(tmp_path)

    result = _run(
        "--runtime-root", str(runtime), "--setup-file", str(setup), "--dry-run"
    )

    assert result.returncode == 0, result.stderr
    assert "no hardware connection" in result.stdout


def test_arm_demo_launcher_rejects_missing_setup_and_conflicting_arguments(tmp_path):
    missing = tmp_path / "runtime" / "arm_demo" / "demo_setup.json"

    missing_result = _run("--setup-file", str(missing), "--dry-run")
    conflict_result = _run("--dry-run", "--test-once")
    resume_result = _run("--resume", str(tmp_path / "setup"))

    assert missing_result.returncode == 2
    assert conflict_result.returncode == 2
    assert resume_result.returncode == 2


def test_arm_demo_launcher_rejects_legacy_runtime_path(tmp_path):
    legacy_runtime = ROOT / "runtime" / "demo"

    result = _run("--runtime-root", str(legacy_runtime), "--dry-run")

    assert result.returncode == 2


def test_arm_demo_dry_run_does_not_take_hardware_lock(tmp_path):
    runtime, setup = _complete_setup(tmp_path)
    lock = tmp_path / "hardware.lock"
    environment = _environment()
    environment["TMPDIR"] = str(tmp_path)
    command = [
        "flock",
        "-n",
        str(tmp_path / "wenshi-demo-hardware.lock"),
        "bash",
        str(LAUNCHER),
        "--runtime-root",
        str(runtime),
        "--setup-file",
        str(setup),
        "--dry-run",
    ]

    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_arm_demo_launcher_reports_hardware_lock_conflict_before_connecting(tmp_path):
    runtime, setup = _complete_setup(tmp_path)
    lock = tmp_path / "wenshi-demo-hardware.lock"
    environment = _environment()
    environment["TMPDIR"] = str(tmp_path)
    held = subprocess.Popen(["flock", "-n", str(lock), "sleep", "5"])
    try:
        result = _run(
            "--runtime-root", str(runtime), "--setup-file", str(setup), env=environment
        )
    finally:
        held.terminate()
        held.wait(timeout=5)

    assert result.returncode == 2
    assert "占用" in result.stderr
