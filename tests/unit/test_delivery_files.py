from pathlib import Path


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


def test_chinese_handover_documents_and_windows_server_exist():
    for name in ("ARCHITECTURE.md", "OPERATIONS.md", "SAFETY.md", "VISION_STATUS.md"):
        assert (ROOT / "docs" / name).is_file()
    assert (ROOT / "windows" / "windows_realsense_server.py").is_file()


def test_github_sync_guide_and_offline_ci_exist():
    guide = ROOT / "docs" / "GITHUB_SYNC.md"
    workflow = ROOT / ".github" / "workflows" / "tests.yml"
    assert guide.is_file()
    assert workflow.is_file()
    workflow_text = workflow.read_text(encoding="utf-8")
    assert "python -m pytest -q" in workflow_text
    assert "PYTHONPATH" in workflow_text
