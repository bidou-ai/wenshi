from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_manual_mentions_required_runtime_operations():
    text = (ROOT / "docs" / "USER_MANUAL.md").read_text(encoding="utf-8")
    for item in ("yubei", "runtime/runs", "events.jsonl", "camera.log", "agv.csv", "jaka.csv", "LM1 -> LM4 -> LM3 -> LM2", "J5跟随", "管理员 PIN", "--preview"):
        assert item in text


def test_pending_file_contains_deferred_topics():
    text = (ROOT / "liuyi666.md").read_text(encoding="utf-8")
    for item in ("三块", "手眼标定", "交叠", "flower", "GPU", "跨运行"):
        assert item in text
