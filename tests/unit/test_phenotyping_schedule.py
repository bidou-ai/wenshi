from types import SimpleNamespace

import pytest


def _config(groups, plant_ids=None):
    plant_ids = plant_ids or tuple(
        plant_id
        for group in groups
        for plant_id in (group.left_plant_id, group.right_plant_id)
        if plant_id is not None
    )
    return SimpleNamespace(
        observation_groups=tuple(groups),
        plants=tuple(SimpleNamespace(plant_id=plant_id) for plant_id in plant_ids),
    )


def _group(group_id, left, right):
    return SimpleNamespace(
        group_id=group_id,
        left_plant_id=left,
        right_plant_id=right,
        route_segment="segment",
        approximate_along_track_m=1.0,
        slowdown_before_m=0.5,
        trigger_distance_m=0.2,
    )


def _complete_groups():
    return [
        _group(f"left-{index:02d}", f"A-{index:02d}", f"B-L-{index:02d}")
        for index in range(1, 9)
    ] + [
        _group(f"right-{index:02d}", f"B-R-{index:02d}", f"C-{index:02d}")
        for index in range(1, 9)
    ]


def _complete_32_plant_config():
    return _config(_complete_groups())


def test_schedule_has_eight_stops_per_route_side():
    from wenshi_patrol.phenotyping.schedule import build_observation_schedule

    config = _complete_32_plant_config()
    config.observation_groups = tuple(reversed(config.observation_groups))
    schedule = build_observation_schedule(config)

    assert [stop.group_id for stop in schedule] == [
        *(f"left-{index:02d}" for index in range(1, 9)),
        *(f"right-{index:02d}" for index in range(1, 9)),
    ]
    assert len({plant_id for stop in schedule for plant_id in stop.plant_ids}) == 32


def test_schedule_rejects_stops_without_exactly_two_distinct_plants():
    from wenshi_patrol.phenotyping.schedule import build_observation_schedule
    configured_ids = tuple(
        plant_id for group in _complete_groups() for plant_id in (group.left_plant_id, group.right_plant_id)
    )

    with pytest.raises(ValueError, match="two plants"):
        build_observation_schedule(
            _config(_complete_groups()[:-1] + [_group("right-08", "A-01", None)], configured_ids)
        )

    with pytest.raises(ValueError, match="distinct"):
        build_observation_schedule(
            _config(_complete_groups()[:-1] + [_group("right-08", "B-R-08", "B-R-08")], configured_ids)
        )


def test_schedule_requires_exactly_16_stops_and_each_configured_plant_once():
    from wenshi_patrol.phenotyping.schedule import build_observation_schedule
    configured_ids = tuple(
        plant_id for group in _complete_groups() for plant_id in (group.left_plant_id, group.right_plant_id)
    )

    with pytest.raises(ValueError, match="exactly 16"):
        build_observation_schedule(_config(_complete_groups()[:-1], configured_ids))

    groups = _complete_groups()
    configured_ids = tuple(
        plant_id for group in groups for plant_id in (group.left_plant_id, group.right_plant_id)
    )
    groups[-1] = _group("right-08", "B-R-08", "A-01")
    with pytest.raises(ValueError, match="exactly once"):
        build_observation_schedule(_config(groups, configured_ids))


def test_schedule_requires_the_standard_left_and_right_stop_identifiers():
    from wenshi_patrol.phenotyping.schedule import build_observation_schedule

    groups = _complete_groups()
    groups[-1] = _group("right-09", "B-R-08", "C-08")
    with pytest.raises(ValueError, match="expected stop IDs"):
        build_observation_schedule(_config(groups))


def test_schedule_rejects_unknown_plant_references():
    from wenshi_patrol.phenotyping.schedule import build_observation_schedule

    groups = _complete_groups()
    configured_ids = tuple(
        plant_id for group in groups for plant_id in (group.left_plant_id, group.right_plant_id)
    )
    groups[-1] = _group("right-08", "B-R-08", "unknown-plant")
    with pytest.raises(ValueError, match="exactly once"):
        build_observation_schedule(_config(groups, configured_ids))


def test_schedule_rejects_duplicate_observation_groups():
    from wenshi_patrol.phenotyping.schedule import build_observation_schedule

    groups = _complete_groups()
    groups[-1] = _group("right-07", "B-R-08", "C-08")
    with pytest.raises(ValueError, match="expected stop IDs"):
        build_observation_schedule(_config(groups))


def test_tag_decision_retries_mismatch_then_never_binds_formal_identity():
    from wenshi_patrol.phenotyping.schedule import TagDecision, handle_tag_result
    from wenshi_patrol.phenotyping.tag_adapter import TagDetection

    detection = TagDetection(9, [(0, 0), (1, 0), (1, 1), (0, 1)], 0.9)
    retry = handle_tag_result(7, [detection], retries=1)
    exhausted = handle_tag_result(7, [detection], retries=0)

    assert isinstance(retry, TagDecision)
    assert retry.status == "retry"
    assert retry.bindable is False
    assert exhausted.status == "unconfirmed"
    assert exhausted.bindable is False
    assert exhausted.detected_tag_id == 9


def test_missing_tag_is_never_bound_even_when_retry_is_exhausted():
    from wenshi_patrol.phenotyping.schedule import handle_tag_result

    decision = handle_tag_result(7, [], retries=0)

    assert decision.status == "unconfirmed"
    assert decision.bindable is False
    assert decision.detected_tag_id is None


def test_matching_tag_is_the_only_formal_binding_decision():
    from wenshi_patrol.phenotyping.schedule import handle_tag_result
    from wenshi_patrol.phenotyping.tag_adapter import TagDetection

    detection = TagDetection(7, [(0, 0), (1, 0), (1, 1), (0, 1)], 0.9)
    decision = handle_tag_result(7, [detection], retries=3)

    assert decision.status == "matched"
    assert decision.bindable is True
    assert decision.detected_tag_id == 7
