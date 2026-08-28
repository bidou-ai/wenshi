from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_manual_mentions_required_runtime_operations():
    text = (ROOT / "docs" / "操作" / "操作手册.md").read_text(encoding="utf-8")
    for item in ("runtime/runs", "三视角", "Tag", "start_wenshi.sh", "start_field_test.sh", "start_dashboard.sh", "RGB-D", "人工复核", "急停"):
        assert item in text


def test_field_checklist_names_real_demo_blockers_and_single_launchers():
    text = (ROOT / "docs" / "操作" / "现场验收清单.md").read_text(encoding="utf-8")
    for item in ("PYTHONPATH=app:. python3 -m pytest -q", "phenotyping.enabled", "32 个 Tag", "left-01", "right-08", "三视角", "补采", "不覆盖 32 株表型验收"):
        assert item in text


def test_pending_file_contains_deferred_topics():
    text = (ROOT / "docs" / "归档" / "待决问题与旧研究笔记.md").read_text(encoding="utf-8")
    for item in ("三块", "手眼标定", "交叠", "flower", "GPU", "跨运行"):
        assert item in text
