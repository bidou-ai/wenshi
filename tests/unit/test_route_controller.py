from wenshi_patrol.route_controller import Wens1Route


def test_wens1_route_assembles_only_the_formal_station_order():
    route = Wens1Route({
        "LM1": (0.0, 0.0, 0.0),
        "LM2": (0.0, -1.0, 0.0),
        "LM3": (1.0, -1.0, 0.0),
        "LM4": (1.0, 0.0, 0.0),
    })
    assert route.order == ("LM1", "LM4", "LM3", "LM2")
    assert route.labels == ["LM1->LM4", "LM4->LM3", "LM3->LM2"]

