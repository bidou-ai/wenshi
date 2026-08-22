"""Wenshi 项目配置加载、默认安全策略与路径解析。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """配置文件缺失或违反安全约束。"""


DEFAULTS: dict[str, dict[str, Any]] = {
    "camera": {"required_for_route": False},
    "vision": {
        "enabled": False, "motion_enable": False, "model_path": "", "target_class_names": ["rice"],
        "stability_window": 5, "stability_min_hits": 3, "station_safety_band_m": 0.50,
        "dedupe_ttl_s": 7200.0, "neighbor_suppression_radius_m": 0.30,
        "target_reverse_speed_mps": 0.05, "target_reverse_limit_m": 0.60,
    },
    "safety": {"reverse_motion_allowed": False, "allow_unverified_reverse": False},
    "patrol_target": {"enabled": False},
}


def _merge_defaults(data: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(DEFAULTS)
    for section, values in data.items():
        if isinstance(values, dict) and isinstance(result.get(section), dict):
            result[section].update(values)
        else:
            result[section] = values
    return result


def load_config(path: str | Path) -> dict[str, Any]:
    """读取 YAML，并始终以配置文件所在目录解析相对路径。"""
    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件: {config_path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"配置文件不是 YAML 对象: {config_path}")

    data = _merge_defaults(raw)
    vision = data["vision"]
    if bool(vision["enabled"]) and not str(vision.get("model_path", "")).strip():
        raise ConfigError("视觉识别已启用，但未配置模型路径")
    if bool(vision.get("motion_enable", False)):
        raise ConfigError("vision.motion_enable 必须保持 false；视觉模块没有运动权限")

    data["_config_path"] = str(config_path)
    data["_config_dir"] = str(config_path.parent)
    return data


def resolve_config_path(config: dict[str, Any], value: str | Path) -> Path:
    """从当前项目配置目录解析非空路径。"""
    text = str(value).strip()
    if not text:
        raise ConfigError("配置路径不能为空")
    path = Path(text).expanduser()
    if not path.is_absolute():
        directory = config.get("_config_dir")
        if not directory:
            raise ConfigError("配置缺少 _config_dir")
        path = Path(directory) / path
    return path.resolve()


def load_viewpoints(config: dict[str, Any]) -> dict[str, Any]:
    import json

    value = str(config.get("jaka", {}).get("viewpoints_file", ""))
    path = resolve_config_path(config, value)
    try:
        with path.open("r", encoding="utf-8") as stream:
            viewpoints = json.load(stream)
    except OSError as exc:
        raise ConfigError(f"无法读取示教文件: {path}: {exc}") from exc
    if not isinstance(viewpoints, dict):
        raise ConfigError(f"示教文件不是 JSON 对象: {path}")
    return viewpoints


def require_joint_pose(viewpoints: dict[str, Any], name: str) -> list[float]:
    pose = viewpoints.get(name)
    if not isinstance(pose, dict):
        raise ConfigError(f"缺少示教点: {name}")
    joint = pose.get("joint")
    if not isinstance(joint, list) or len(joint) != 6:
        raise ConfigError(f"示教点 {name} 必须包含 6 个关节角")
    return [float(value) for value in joint]
