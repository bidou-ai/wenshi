from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_label_ui_has_manifest_driven_class_controls_and_fast_annotation_controls():
    html = (ROOT / "yubei" / "label_ui" / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "yubei" / "label_ui" / "app.js").read_text(encoding="utf-8")
    assert 'id="class-buttons"' in html
    assert 'id="save-next"' in html
    assert 'id="exclude"' in html
    assert 'id="set-selected-class"' in html
    assert 'id="zoom"' in html
    assert "/api/dataset" in app
    assert "renderClassButtons" in app
    assert "duplicate_of" in app
    assert "drag.mode" in app
    assert "state.zoom" in app
