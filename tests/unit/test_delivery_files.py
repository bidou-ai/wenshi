from pathlib import Path
import os
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_single_formal_startup_and_preflight_scripts_exist():
    start = ROOT / "scripts" / "start_wenshi.sh"
    preflight = ROOT / "scripts" / "check_environment.sh"
    hardware = ROOT / "scripts" / "check_hardware_links.sh"
    assert start.is_file()
    assert preflight.is_file()
    assert hardware.is_file()
    assert "patrol_controller" in start.read_text(encoding="utf-8")
    assert "wenshi.yaml" in preflight.read_text(encoding="utf-8")


def test_formal_launcher_help_is_safe_in_a_clean_shell():
    start = ROOT / "scripts" / "start_wenshi.sh"
    environment = dict(os.environ)
    environment.pop("AMENT_TRACE_SETUP_FILES", None)

    result = subprocess.run(
        ["bash", str(start), "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0
    assert "用法" in result.stdout


def test_formal_launcher_forces_hardware_check_before_runtime():
    text = (ROOT / "scripts" / "start_wenshi.sh").read_text(encoding="utf-8")

    hardware = text.index("check_hardware_links.sh")
    camera = text.index("wenshi_patrol.camera_bridge")
    controller = text.index("wenshi_patrol.patrol_controller")
    assert hardware < camera < controller
    assert "%N" in text


def test_chinese_handover_documents_and_windows_server_exist():
    for name in ("ARCHITECTURE.md", "OPERATIONS.md", "SAFETY.md", "VISION_STATUS.md"):
        assert (ROOT / "docs" / name).is_file()
    assert (ROOT / "windows" / "windows_realsense_server.py").is_file()
    assert (ROOT / "windows" / "start_camera_server.bat").is_file()


def test_yubei_has_one_file_launcher():
    launcher = ROOT / "yubei" / "start_yubei.sh"
    assert launcher.is_file()
    assert launcher.stat().st_mode & 0o111
    assert "check_all.py" in launcher.read_text(encoding="utf-8")
    assert "camera-check" in launcher.read_text(encoding="utf-8")
    assert "audit" in launcher.read_text(encoding="utf-8")


def test_yubei_launcher_help_works_outside_project_directory():
    launcher = ROOT / "yubei" / "start_yubei.sh"

    result = subprocess.run(
        ["bash", str(launcher), "--help"],
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0
    assert "预备工具" in result.stdout


def test_yubei_launcher_opens_label_browser_and_finds_prepared_dataset():
    text = (ROOT / "yubei" / "start_yubei.sh").read_text(encoding="utf-8")

    assert "--open-browser" in text
    assert "latest_prepared" in text
    assert "publish-viewpoints" in text
    assert "publish-model" in text


def test_github_sync_guide_and_offline_ci_exist():
    guide = ROOT / "docs" / "GITHUB_SYNC.md"
    workflow = ROOT / ".github" / "workflows" / "tests.yml"
    assert guide.is_file()
    assert workflow.is_file()
    workflow_text = workflow.read_text(encoding="utf-8")
    assert "python -m pytest -q" in workflow_text
    assert "PYTHONPATH" in workflow_text
