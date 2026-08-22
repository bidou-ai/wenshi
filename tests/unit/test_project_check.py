from pathlib import Path

import yaml

from wenshi_patrol.project_check import validate_project
import wenshi_patrol.project_check as project_check


ROOT = Path(__file__).resolve().parents[2]


def test_current_wenshi_project_passes_offline_preflight():
    assert validate_project(ROOT / "config" / "wenshi.yaml") == []


def test_preflight_reports_route_that_is_not_wens1_order(tmp_path: Path):
    config = tmp_path / "wenshi.yaml"
    config.write_text("route:\n  station_order: [LM2, LM3, LM4, LM1]\n", encoding="utf-8")
    errors = validate_project(config)
    assert any("正式路线" in error for error in errors)


def test_preflight_requires_home_safe_when_fixed_approach_is_enabled(tmp_path: Path):
    value = yaml.safe_load((ROOT / "config" / "wenshi.yaml").read_text(encoding="utf-8"))
    value["map"]["smap_file"] = str((ROOT / "map" / "wenshi.smap").resolve())
    value["jaka"]["viewpoints_file"] = str((ROOT / "config" / "viewpoints.json").resolve())
    value["fixed_approach"]["enabled"] = True
    config = tmp_path / "wenshi.yaml"
    config.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")

    errors = validate_project(config)

    assert any("home_safe" in error for error in errors)


def test_preflight_lists_all_disabled_target_demo_gates(tmp_path: Path):
    value = yaml.safe_load((ROOT / "config" / "wenshi.yaml").read_text(encoding="utf-8"))
    value["map"]["smap_file"] = str((ROOT / "map" / "wenshi.smap").resolve())
    value["jaka"]["viewpoints_file"] = str((ROOT / "config" / "viewpoints.json").resolve())
    value["patrol_target"]["enabled"] = True
    config = tmp_path / "wenshi.yaml"
    config.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")

    errors = validate_project(config)

    assert any("vision.enabled" in error for error in errors)
    assert any("模型" in error for error in errors)
    assert any("fixed_approach.enabled" in error for error in errors)
    assert any("倒车安全锁" in error for error in errors)


def test_preflight_rejects_target_runtime_without_ultralytics(tmp_path: Path, monkeypatch):
    value = yaml.safe_load((ROOT / "config" / "wenshi.yaml").read_text(encoding="utf-8"))
    value["map"]["smap_file"] = str((ROOT / "map" / "wenshi.smap").resolve())
    value["jaka"]["viewpoints_file"] = str((ROOT / "config" / "viewpoints.json").resolve())
    value["patrol_target"]["enabled"] = True
    value["vision"]["enabled"] = True
    model = tmp_path / "rice.pt"
    model.write_bytes(b"placeholder")
    value["vision"]["model_path"] = str(model)
    value["fixed_approach"]["enabled"] = True
    value["safety"]["reverse_motion_allowed"] = True
    value["safety"]["rear_radar_verified"] = True
    monkeypatch.setattr(project_check.importlib.util, "find_spec", lambda name: None if name == "ultralytics" else object())
    config = tmp_path / "wenshi.yaml"
    config.write_text(yaml.safe_dump(value, allow_unicode=True), encoding="utf-8")

    errors = validate_project(config)

    assert any("ultralytics" in error for error in errors)
