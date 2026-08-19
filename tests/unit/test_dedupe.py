from wenshi_patrol.vision.dedupe import DedupeRegistry, TargetKey


def key(position, side="left", segment="LM1->LM4"):
    return TargetKey(segment, side, float(position))


def test_selected_target_is_blocked_for_two_hours():
    registry = DedupeRegistry(ttl_s=7200, suppression_radius_m=0.30)
    registry.mark_selected(key(1.0), now=100.0)
    assert registry.can_process(key(1.05), now=1000.0).allowed is False
    assert registry.can_process(key(1.05), now=7301.0).allowed is True


def test_surrounding_target_is_only_deferred_in_current_loop():
    registry = DedupeRegistry(ttl_s=7200, suppression_radius_m=0.30)
    registry.mark_selected(key(1.0), now=100.0, loop_id=1)
    assert registry.can_process(key(1.2), now=101.0, loop_id=1).reason == "current_loop_suppressed"
    assert registry.can_process(key(1.2), now=101.0, loop_id=2).allowed is True


def test_different_side_is_not_suppressed_by_same_route_position():
    registry = DedupeRegistry(suppression_radius_m=0.30)
    registry.mark_selected(key(1.0, "left"), now=100.0, loop_id=1)
    assert registry.can_process(key(1.0, "right"), now=101.0, loop_id=1).allowed is True
