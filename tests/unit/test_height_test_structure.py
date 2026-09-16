from pathlib import Path


def test_formal_inventory_documents_legacy_boundaries():
    text = Path("docs/技术/正式代码引用与整理报告.md").read_text(encoding="utf-8")
    assert "patrol_controller.py" in text
    assert "保留" in text or "兼容" in text


def test_height_test_entry_is_present_and_yubei_is_untouched_by_imports():
    assert Path("scripts/start_height_test.sh").is_file()
    source = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in Path("app").rglob("*.py"))
    assert "from yubei" not in source
