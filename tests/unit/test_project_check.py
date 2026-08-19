from pathlib import Path

from wenshi_patrol.project_check import validate_project


ROOT = Path(__file__).resolve().parents[2]


def test_current_wenshi_project_passes_offline_preflight():
    assert validate_project(ROOT / "config" / "wenshi.yaml") == []


def test_preflight_reports_route_that_is_not_wens1_order(tmp_path: Path):
    config = tmp_path / "wenshi.yaml"
    config.write_text("route:\n  station_order: [LM2, LM3, LM4, LM1]\n", encoding="utf-8")
    errors = validate_project(config)
    assert any("正式路线" in error for error in errors)

