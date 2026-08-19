def test_route_policy_requires_exact_wens1_order():
    from wenshi_patrol.control.route_policy import ROUTE_ORDER, validate_route

    assert ROUTE_ORDER == ("LM1", "LM4", "LM3", "LM2")
    assert validate_route(ROUTE_ORDER) == ROUTE_ORDER


def test_route_policy_rejects_reverse_or_partial_routes():
    from wenshi_patrol.control.route_policy import RoutePolicyError, validate_route

    for route in (("LM2", "LM3", "LM4", "LM1"), ("LM1", "LM3")):
        try:
            validate_route(route)
        except RoutePolicyError:
            pass
        else:
            raise AssertionError("invalid route was accepted")

