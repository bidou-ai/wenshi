"""不连接现场设备的项目配置、地图和示教点预检。"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

from .config import ConfigError, load_config, load_viewpoints, resolve_config_path
from .controller_math import reverse_motion_allowed
from .control.route_math import make_segments
from .control.route_policy import RoutePolicyError, validate_route
from .fixed_approach import validate_home_safe, validate_side_arm_path
from .map_utils import load_station_poses


def validate_project(config_path: str | Path) -> list[str]:
    errors: list[str] = []
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        return [str(exc)]

    route = config.get("route", {}).get("station_order", [])
    try:
        validate_route(route)
    except RoutePolicyError as exc:
        errors.append(str(exc))

    try:
        map_path = resolve_config_path(config, str(config.get("map", {}).get("smap_file", "")))
        stations = load_station_poses(map_path)
        make_segments(stations, [str(name) for name in route], loop=False)
    except (ConfigError, OSError, ValueError) as exc:
        errors.append(f"地图或路线无效: {exc}")

    try:
        viewpoints = load_viewpoints(config)
        maximum = float(config.get("fixed_approach", {}).get("max_joint_step_deg", 120.0))
        for side in ("right", "left"):
            errors.extend(validate_side_arm_path(viewpoints, side, maximum))
        if bool(config.get("fixed_approach", {}).get("enabled", False)) or bool(
            config.get("patrol_target", {}).get("enabled", False)
        ):
            errors.extend(validate_home_safe(viewpoints))
    except (ConfigError, OSError, ValueError) as exc:
        errors.append(f"示教点无效: {exc}")

    if bool(config.get("patrol_target", {}).get("enabled", False)):
        vision = config.get("vision", {})
        if not bool(vision.get("enabled", False)):
            errors.append("patrol_target 已启用，但 vision.enabled 未启用")
        model_value = str(vision.get("model_path", "")).strip()
        if not model_value:
            errors.append("patrol_target 已启用，但未配置 rice 模型")
        else:
            try:
                model_path = resolve_config_path(config, model_value)
                if not model_path.is_file():
                    errors.append(f"patrol_target 模型文件不存在: {model_path}")
                elif importlib.util.find_spec("ultralytics") is None:
                    errors.append("patrol_target 已启用，但当前 Python 环境未安装 ultralytics")
            except ConfigError as exc:
                errors.append(f"patrol_target 模型路径无效: {exc}")
        if not bool(config.get("fixed_approach", {}).get("enabled", False)):
            errors.append("patrol_target 已启用，但 fixed_approach.enabled 未启用")
        if not reverse_motion_allowed(config.get("safety", {})):
            errors.append("patrol_target 已启用，但目标对齐倒车安全锁未放行")

    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="Wenshi 离线项目预检")
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args(argv)
    errors = validate_project(arguments.config)
    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        raise SystemExit(1)
    print("[OK] 配置、地图、路线和示教点预检通过")


if __name__ == "__main__":
    main()
