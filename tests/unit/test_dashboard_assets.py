from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_assets_exist_and_contain_results_labels():
    html = (ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
    assert "远景" in html and "近景" in html and "质量" in html and "路线段" in html
    script = (ROOT / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert "data-cx" in script and "bbox" in script and "管理员" in html
    assert (ROOT / "dashboard" / "static" / "app.js").is_file()
    assert (ROOT / "dashboard" / "static" / "style.css").is_file()
