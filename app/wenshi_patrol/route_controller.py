"""Wens1 路线装配层，不连接设备也不发送运动命令。"""

from __future__ import annotations

from typing import Any

from .control.route_math import Segment, make_segments
from .control.route_policy import ROUTE_ORDER, validate_route


class Wens1Route:
    """将地图站点和固定路线顺序组装成可供控制器消费的路线。"""

    def __init__(self, stations: dict[str, tuple[float, float, float]], loop: bool = False):
        validate_route(ROUTE_ORDER)
        self.stations = stations
        self.loop = bool(loop)
        self.order = ROUTE_ORDER
        self.segments: list[Segment] = make_segments(
            stations, list(self.order), loop=self.loop
        )

    @property
    def labels(self) -> list[str]:
        return [f"{segment.start_name}->{segment.end_name}" for segment in self.segments]

    def segment(self, index: int) -> Segment:
        return self.segments[index]

