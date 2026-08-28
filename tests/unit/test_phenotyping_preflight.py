from pathlib import Path
import subprocess


def placeholder_config():
    import yaml

    return yaml.safe_load(Path(__file__).parents[2].joinpath("config/wenshi.yaml").read_text(encoding="utf-8"))


def test_preflight_rejects_incomplete_formal_configuration_without_hardware_probe():
    from wenshi_patrol.phenotyping.preflight import phenotyping_preflight

    config = placeholder_config()
    report = phenotyping_preflight(config, Path("/tmp/runtime"))

    assert report.ok is False
    assert any("Tag" in error for error in report.errors)
    assert report.hardware_probe_called is False


def test_preflight_does_not_call_hardware_probe_when_config_is_incomplete():
    from wenshi_patrol.phenotyping.preflight import phenotyping_preflight

    called = []
    config = placeholder_config()
    report = phenotyping_preflight(config, Path("/tmp/runtime"), hardware_probe=lambda: called.append(True))

    assert report.ok is False
    assert called == []


def test_preflight_accepts_complete_disabled_configuration_for_simulation():
    from wenshi_patrol.phenotyping.preflight import phenotyping_preflight

    config = placeholder_config()
    config["phenotyping"]["enabled"] = False
    for tag_id, plant in enumerate(config["plants"]):
        plant["tag_id"] = tag_id
        plant["slot_top_to_water_m"] = 0.1
    config["april_tag"].update({"physical_size_m": 0.08, "detector_backend": "fixture", "mounting_orientation": "upward"})
    for group in config["observation_groups"]:
        group.update({"route_segment": "LM1->LM4", "approximate_along_track_m": 1.0, "slowdown_before_m": 0.5, "trigger_distance_m": 0.2})

    report = phenotyping_preflight(config, Path("/tmp/runtime"))

    assert report.ok is True
    assert report.formal_ready is False


def test_phenotype_entry_rejects_placeholder_config_before_hardware_check():
    root = Path(__file__).parents[2]

    result = subprocess.run(
        [str(root / "scripts" / "start_wenshi.sh"), "phenotype", "--check"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert "表型任务配置未完成" in result.stderr
    assert "AGV" not in result.stdout


def test_phenotype_entry_dispatches_to_the_dedicated_controller_not_rice_controller():
    script = Path(__file__).parents[2].joinpath("scripts", "start_wenshi.sh").read_text(encoding="utf-8")

    assert "wenshi_patrol.phenotype_controller" in script
