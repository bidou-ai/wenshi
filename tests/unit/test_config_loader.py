from pathlib import Path

import pytest


def test_load_config_rejects_enabled_vision_without_model(tmp_path: Path):
    from wenshi_patrol.config import ConfigError, load_config

    config = tmp_path / "invalid.yaml"
    config.write_text(
        """
vision:\n  enabled: true\n  model_path: ''\n""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="模型"):
        load_config(config)


def test_load_config_disables_reverse_and_route_vision_by_default(tmp_path: Path):
    from wenshi_patrol.config import load_config

    config = tmp_path / "minimal.yaml"
    config.write_text("{}\n", encoding="utf-8")

    loaded = load_config(config)

    assert loaded["safety"]["reverse_motion_allowed"] is False
    assert loaded["camera"]["required_for_route"] is False
    assert loaded["vision"]["enabled"] is False
