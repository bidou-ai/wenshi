from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_manual_mentions_required_runtime_operations():
    text = (ROOT / "docs" / "USER_MANUAL.md").read_text(encoding="utf-8")
    for item in ("yubei", "runtime/runs", "events.jsonl", "camera.log", "console.log", "agv.csv", "jaka.csv", "LM1 -> LM4 -> LM3 -> LM2", "J5跟随", "管理员 PIN", "WENSHI_ADMIN_PIN", "start_yubei.sh", "start_dashboard.sh", "start_camera_server.bat", "127.0.0.1"):
        assert item in text


def test_field_checklist_names_real_demo_blockers_and_single_launchers():
    text = (ROOT / "docs" / "FIELD_TEST_CHECKLIST.md").read_text(encoding="utf-8")
    for item in (
        "./scripts/start_wenshi.sh",
        "./scripts/start_dashboard.sh",
        "./yubei/start_yubei.sh",
        "start_camera_server.bat",
        "vision.enabled",
        "home_safe",
        "target_reverse_limit_m",
        "rear_radar_verified",
        "不得启用完整视觉抵近",
    ):
        assert item in text


def test_pending_file_contains_deferred_topics():
    text = (ROOT / "liuyi666.md").read_text(encoding="utf-8")
    for item in ("三块", "手眼标定", "交叠", "flower", "GPU", "跨运行"):
        assert item in text
