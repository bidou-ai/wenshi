"""Validated side-specific teaching paths for dynamic patrol targets."""

from __future__ import annotations

from typing import Any


SIDES = ("right", "left")
HOME_SAFE_POSE = "home_safe"
SIDE_POSES = {
    "right": ("camera_right", "right_pre", "right_photo"),
    "left": ("camera_left", "left_pre", "left_photo"),
}


def validate_home_safe(viewpoints: dict[str, Any]) -> list[str]:
    pose = viewpoints.get(HOME_SAFE_POSE)
    joint = pose.get("joint") if isinstance(pose, dict) else None
    if not isinstance(joint, list) or len(joint) != 6:
        return ["缺少或无效示教点 home_safe"]
    return []


def bounded_composition_delta(side: str, pixel_error: tuple[float, float], limits: dict[str, float]) -> dict[int, float]:
    if side not in SIDES:
        raise ValueError(f"unknown side: {side}")
    horizontal, vertical = (float(pixel_error[0]), float(pixel_error[1]))
    j4_limit = abs(float(limits.get("j4_deg", 2.0)))
    j5_limit = abs(float(limits.get("j5_deg", 3.0)))
    j6_limit = abs(float(limits.get("j6_deg", 1.0)))
    horizontal = max(-1.0, min(1.0, horizontal))
    vertical = max(-1.0, min(1.0, vertical))
    return {3: max(-j4_limit, min(j4_limit, vertical * j4_limit)), 4: max(-j5_limit, min(j5_limit, horizontal * j5_limit)), 5: max(-j6_limit, min(j6_limit, horizontal * j6_limit * 0.5))}


def _joint_pose(viewpoints: dict[str, Any], name: str) -> list[float] | None:
    pose = viewpoints.get(name)
    if not isinstance(pose, dict):
        return None
    joint = pose.get("joint")
    if not isinstance(joint, list) or len(joint) != 6:
        return None
    return [float(value) for value in joint]


def validate_side_arm_path(
    viewpoints: dict[str, Any],
    side: str,
    max_joint_step_deg: float,
) -> list[str]:
    if side not in SIDE_POSES:
        return [f"未知目标侧: {side}"]
    errors: list[str] = []
    names = SIDE_POSES[side]
    joints = [_joint_pose(viewpoints, name) for name in names]
    for name, joint in zip(names, joints):
        if joint is None:
            errors.append(f"缺少示教点 {name}")
    if not all(joint is not None for joint in joints):
        return errors
    for start_name, end_name, start, end in zip(names, names[1:], joints, joints[1:]):
        deltas = [abs(float(end[i]) - float(start[i])) for i in range(6)]
        worst = max(range(6), key=deltas.__getitem__)
        if deltas[worst] > float(max_joint_step_deg):
            errors.append(
                f"{start_name}->{end_name} 的 J{worst + 1} 变化 {deltas[worst]:.1f}deg "
                f"超过限制 {float(max_joint_step_deg):.1f}deg"
            )
    return errors


def _joint_segment_error(
    current: list[float],
    start: list[float],
    end: list[float],
) -> float:
    delta = [float(end[i]) - float(start[i]) for i in range(6)]
    offset = [float(current[i]) - float(start[i]) for i in range(6)]
    length_squared = sum(value * value for value in delta)
    if length_squared <= 1e-9:
        return max(abs(value) for value in offset)
    progress = sum(offset[i] * delta[i] for i in range(6)) / length_squared
    progress = max(0.0, min(1.0, progress))
    projected = [float(start[i]) + progress * delta[i] for i in range(6)]
    return max(abs(float(current[i]) - projected[i]) for i in range(6))


def plan_teach_return(
    viewpoints: dict[str, Any],
    side: str,
    current_joint: list[float],
    corridor_deg: float,
) -> list[str]:
    """Choose a retraction suffix without sending the arm outward again."""
    if side not in SIDE_POSES:
        raise ValueError(f"未知目标侧: {side}")
    if len(current_joint) != 6:
        raise ValueError("当前机械臂关节角不是 6 个")
    entry_name, pre_name, photo_name = SIDE_POSES[side]
    entry = _joint_pose(viewpoints, entry_name)
    pre = _joint_pose(viewpoints, pre_name)
    photo = _joint_pose(viewpoints, photo_name)
    missing = [
        name
        for name, joint in ((entry_name, entry), (pre_name, pre), (photo_name, photo))
        if joint is None
    ]
    if missing:
        raise ValueError(f"缺少示教点: {', '.join(missing)}")

    pre_to_entry_error = _joint_segment_error(current_joint, pre, entry)
    if pre_to_entry_error <= float(corridor_deg):
        return [entry_name]

    photo_to_pre_error = _joint_segment_error(current_joint, photo, pre)
    if photo_to_pre_error <= float(corridor_deg):
        return [pre_name, entry_name]

    side_label = "右侧" if side == "right" else "左侧"
    raise ValueError(
        f"当前机械臂不在{side_label}示教回撤通道内: "
        f"photo->pre偏差={photo_to_pre_error:.1f}deg, "
        f"pre->camera偏差={pre_to_entry_error:.1f}deg, "
        f"限制={float(corridor_deg):.1f}deg"
    )


def plan_home_return(
    viewpoints: dict[str, Any],
    current_joint: list[float],
    corridor_deg: float,
    patrol_tolerance_deg: float,
    nearby_home_tolerance_deg: float,
    center_name: str = "camera",
) -> list[str]:
    """Return through a taught side path, or directly from a patrol pose."""
    if len(current_joint) != 6:
        raise ValueError("当前机械臂关节角不是 6 个")
    center = _joint_pose(viewpoints, center_name)
    left = _joint_pose(viewpoints, SIDE_POSES["left"][0])
    right = _joint_pose(viewpoints, SIDE_POSES["right"][0])
    if center is None or left is None or right is None:
        raise ValueError("缺少 camera/camera_left/camera_right 巡视姿态")

    non_j5_error = max(
        abs(float(current_joint[index]) - center[index])
        for index in (0, 1, 2, 3, 5)
    )
    lower = min(left[4], right[4]) - float(patrol_tolerance_deg)
    upper = max(left[4], right[4]) + float(patrol_tolerance_deg)
    if non_j5_error <= float(patrol_tolerance_deg) and lower <= float(current_joint[4]) <= upper:
        return [center_name]

    candidates: list[tuple[float, list[str]]] = []
    failures: list[str] = []
    for side in SIDES:
        try:
            plan = plan_teach_return(viewpoints, side, current_joint, corridor_deg)
        except ValueError as exc:
            failures.append(str(exc))
            continue
        first = _joint_pose(viewpoints, plan[0])
        cost = max(abs(float(current_joint[i]) - first[i]) for i in range(6))
        candidates.append((cost, plan + [center_name]))
    if candidates:
        return min(candidates, key=lambda candidate: candidate[0])[1]

    home_error = max(abs(float(current_joint[i]) - center[i]) for i in range(6))
    if home_error <= float(nearby_home_tolerance_deg):
        return [center_name]
    raise ValueError(
        "当前机械臂姿态无法安全规划 goto home: "
        f"到{center_name}最大关节差={home_error:.1f}deg，"
        f"近端限制={float(nearby_home_tolerance_deg):.1f}deg；"
        + "；".join(failures)
    )
