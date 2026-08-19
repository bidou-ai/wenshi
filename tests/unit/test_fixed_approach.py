from wenshi_patrol.fixed_approach import plan_teach_return, validate_side_arm_path


def test_fixed_path_accepts_taught_right_sequence():
    viewpoints = {
        "camera_right": {"joint": [0.0] * 6},
        "right_pre": {"joint": [20.0] * 6},
        "right_photo": {"joint": [40.0] * 6},
    }
    assert validate_side_arm_path(viewpoints, "right", 120.0) == []
    assert plan_teach_return(viewpoints, "right", [30.0] * 6, 2.0) == [
        "right_pre",
        "camera_right",
    ]


def test_fixed_path_rejects_joint_pose_outside_taught_corridor():
    viewpoints = {
        "camera_right": {"joint": [0.0] * 6},
        "right_pre": {"joint": [20.0] * 6},
        "right_photo": {"joint": [40.0] * 6},
    }
    try:
        plan_teach_return(viewpoints, "right", [10, 10, 10, 10, 10, 50], 2.0)
    except ValueError as exc:
        assert "不在右侧示教回撤通道" in str(exc)
    else:
        raise AssertionError("unsafe pose was accepted")

