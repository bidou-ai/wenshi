from pathlib import Path

from wenshi_patrol.config import load_config, resolve_config_path


def test_relative_paths_resolve_from_wenshi_config(tmp_path: Path):
    config_file = tmp_path / "config" / "wenshi.yaml"
    config_file.parent.mkdir()
    config_file.write_text("map:\n  smap_file: ../map/wenshi.smap\n", encoding="utf-8")
    config = load_config(config_file)
    assert resolve_config_path(config, config["map"]["smap_file"]) == (
        tmp_path / "map" / "wenshi.smap"
    )

