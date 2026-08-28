"""Validation and loading for the hardware-independent phenotype configuration."""

from __future__ import annotations

import math
from typing import Any

from .schema import (
    AprilTagConfig, ArmPostures, CalibrationEvidence, ObservationGroup, PhenotypingConfig, PlantSpec,
)


EXPECTED_PLANT_COUNT = 32
EXPECTED_GROUP_COUNT = 16
EXPECTED_TRAITS = ("plant_height", "effective_panicle_count")


def _section(value: dict[str, Any], name: str) -> dict[str, Any]:
    section = value.get(name, {})
    return section if isinstance(section, dict) else {}


def _list(value: dict[str, Any], name: str) -> list[Any]:
    items = value.get(name, [])
    return items if isinstance(items, list) else []


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _validate_number(value: Any, label: str, minimum: float, maximum: float, errors: list[str]) -> None:
    parsed = _finite_number(value)
    if parsed is None or not minimum <= parsed <= maximum:
        errors.append(f"{label} 必须是 {minimum} 到 {maximum} 之间的有限数值")


def _safe_integer(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if value == parsed or (isinstance(value, str) and value.strip() == str(parsed)) else default


def _validate_integer(value: Any, label: str, minimum: int, maximum: int, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        errors.append(f"{label} 必须是 {minimum} 到 {maximum} 之间的整数")


def validate_phenotyping_config(value: dict[str, Any]) -> list[str]:
    """Return diagnostics without connecting to or importing any hardware."""
    if not isinstance(value, dict):
        return ["表型配置必须是对象"]

    phenotype = _section(value, "phenotyping")
    tag = _section(value, "april_tag")
    plants = _list(value, "plants")
    groups = _list(value, "observation_groups")
    errors: list[str] = []

    if len(plants) != EXPECTED_PLANT_COUNT:
        errors.append(f"表型配置必须包含 {EXPECTED_PLANT_COUNT} 株，当前为 {len(plants)} 株")
    plant_specs = [PlantSpec.from_dict(item) for item in plants if isinstance(item, dict)]
    plant_ids = [plant.plant_id for plant in plant_specs]
    if len(set(plant_ids)) != len(plant_ids):
        errors.append("株号必须唯一，发现重复株号")
    if any(not plant.plant_id for plant in plant_specs):
        errors.append("株号不能为空")

    tag_ids = [plant.tag_id for plant in plant_specs if plant.tag_id is not None]
    if len(set(tag_ids)) != len(tag_ids):
        errors.append("Tag ID 不得重复")

    if len(groups) != EXPECTED_GROUP_COUNT:
        errors.append(f"表型配置必须包含 {EXPECTED_GROUP_COUNT} 个停车观测组，当前为 {len(groups)} 个")
    group_specs = [ObservationGroup.from_dict(item) for item in groups if isinstance(item, dict)]
    group_ids = [group.group_id for group in group_specs]
    if len(set(group_ids)) != len(group_ids):
        errors.append("观测组 ID 必须唯一")
    plant_id_set = set(plant_ids)
    grouped_plant_ids: list[str] = []
    raw_groups = {str(item.get("id", item.get("group_id", ""))): item for item in groups if isinstance(item, dict)}
    for group in group_specs:
        if not group.left_plant_id or not group.right_plant_id:
            errors.append(f"停车观测组 {group.group_id} 必须包含两个植株")
        elif group.left_plant_id == group.right_plant_id:
            errors.append(f"停车观测组 {group.group_id} 的两株不能相同")
        for plant_id in (group.left_plant_id, group.right_plant_id):
            if plant_id and plant_id not in plant_id_set:
                errors.append(f"观测组 {group.group_id} 引用了未知株号 {plant_id}")
            if plant_id:
                grouped_plant_ids.append(plant_id)
        raw_group = raw_groups.get(group.group_id, {})
        if not _text_or_none(raw_group.get("route_segment")):
            errors.append(f"停车观测组 {group.group_id} 的停车位置标定不完整")
        _validate_number(raw_group.get("approximate_along_track_m"), f"停车观测组 {group.group_id} 的路线距离", 0.0, 1000.0, errors)
        _validate_number(raw_group.get("slowdown_before_m"), f"停车观测组 {group.group_id} 的减速距离", 0.0, 100.0, errors)
        _validate_number(raw_group.get("trigger_distance_m"), f"停车观测组 {group.group_id} 的触发距离", 0.01, 100.0, errors)

    for plant_id in plant_ids:
        if grouped_plant_ids.count(plant_id) != 1:
            errors.append(f"株号 {plant_id} 必须被停车观测组恰好一次覆盖")

    if _text_or_none(tag.get("family")) != "tag25h7":
        errors.append("AprilTag family 必须是 tag25h7")
    if _missing(tag.get("physical_size_m")):
        errors.append("Tag 实际尺寸尚未确认")
    else:
        _validate_number(tag.get("physical_size_m"), "Tag 实际尺寸", 0.01, 0.30, errors)
    if _missing(tag.get("detector_backend")):
        errors.append("Tag 检测后端尚未确认")
    if _missing(tag.get("mounting_orientation")):
        errors.append("Tag 安装朝向尚未确认")
    elif _text_or_none(tag.get("mounting_orientation")) not in {"upward", "side"}:
        errors.append("Tag 安装朝向必须是 upward 或 side")
    raw_plants = {str(item.get("plant_id", "")): item for item in plants if isinstance(item, dict)}
    for plant in plant_specs:
        if plant.tag_id is None:
            errors.append(f"株号 {plant.plant_id} 尚未完成 Tag 映射")
        elif plant.tag_id < 0:
            errors.append(f"株号 {plant.plant_id} 的 Tag ID 必须是非负整数")
        raw_plant = raw_plants.get(plant.plant_id, {})
        if _missing(raw_plant.get("slot_top_to_water_m")):
            errors.append(f"株号 {plant.plant_id} 尚未标定卡槽到水面高度")
        else:
            _validate_number(raw_plant.get("slot_top_to_water_m"), f"株号 {plant.plant_id} 的卡槽到水面高度", 0.0, 2.0, errors)

    traits = phenotype.get("traits", [])
    if tuple(traits) != EXPECTED_TRAITS:
        errors.append("表型 traits 必须为株高和有效穗数")
    if phenotype.get("processing_mode", "offline") not in ("offline", "online"):
        errors.append("processing_mode 必须是 offline 或 online")
    _validate_integer(phenotype.get("capture_burst_count", 3), "capture_burst_count", 1, 20, errors)
    _validate_integer(phenotype.get("capture_burst_max", 5), "capture_burst_max", 1, 20, errors)
    _validate_integer(phenotype.get("max_retries_per_view", 3), "max_retries_per_view", 0, 10, errors)
    if _safe_integer(phenotype.get("capture_burst_count", 3), 3) > _safe_integer(phenotype.get("capture_burst_max", 5), 5):
        errors.append("capture_burst_count 不能大于 capture_burst_max")
    enabled = bool(phenotype.get("enabled", False))
    if enabled:
        postures = _section(phenotype, "arm_postures")
        if any(not _text_or_none(postures.get(view)) for view in ("left", "center", "right")):
            errors.append("机械臂三视角采集姿态不完整")
        camera = _section(phenotype, "camera_calibration")
        if camera.get("calibrated") is not True or not _text_or_none(camera.get("evidence")):
            errors.append("相机标定状态或证据不完整")
        water = _section(phenotype, "water_compensation")
        if water.get("calibrated") is not True or not _text_or_none(water.get("evidence")):
            errors.append("水位补偿状态或证据不完整")
    if enabled and errors:
        errors.append("表型正式任务因配置未完成而禁止启动")
    return errors


def load_phenotyping_config(config: dict[str, Any]) -> PhenotypingConfig:
    """Parse configuration while retaining diagnostics for preflight callers."""
    phenotype = _section(config, "phenotyping")
    tag = _section(config, "april_tag")
    plants = tuple(PlantSpec.from_dict(item) for item in _list(config, "plants") if isinstance(item, dict))
    groups = tuple(ObservationGroup.from_dict(item) for item in _list(config, "observation_groups") if isinstance(item, dict))
    april_tag = AprilTagConfig(
        family=str(tag.get("family", "tag25h7")),
        physical_size_m=_float_or_none(tag.get("physical_size_m")),
        detector_backend=_text_or_none(tag.get("detector_backend")),
        mounting_orientation=_text_or_none(tag.get("mounting_orientation")),
    )
    return PhenotypingConfig(
        enabled=bool(phenotype.get("enabled", False)),
        traits=tuple(str(item) for item in phenotype.get("traits", EXPECTED_TRAITS)),
        processing_mode=str(phenotype.get("processing_mode", "offline")),
        capture_burst_count=_safe_integer(phenotype.get("capture_burst_count", 3), 3),
        capture_burst_max=_safe_integer(phenotype.get("capture_burst_max", 5), 5),
        max_retries_per_view=_safe_integer(phenotype.get("max_retries_per_view", 3), 3),
        plants=plants,
        observation_groups=groups,
        april_tag=april_tag,
        arm_postures=ArmPostures.from_dict(phenotype.get("arm_postures")),
        camera_calibration=CalibrationEvidence.from_dict(phenotype.get("camera_calibration")),
        water_compensation=CalibrationEvidence.from_dict(phenotype.get("water_compensation")),
        validation_errors=tuple(validate_phenotyping_config(config)),
    )


def _text_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _float_or_none(value: Any) -> float | None:
    return _finite_number(value)
