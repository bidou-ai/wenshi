"""Read the vendor smap and create the ROS map/marker messages used by RViz."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def load_smap(path: str | Path) -> dict[str, Any]:
    with Path(path).expanduser().open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or "header" not in data:
        raise ValueError(f"无效的 smap: {path}")
    return data


def _xy_from_mapping(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    if "x" in value and "y" in value:
        return float(value["x"]), float(value["y"])
    nested = value.get("pos")
    if isinstance(nested, dict) and "x" in nested and "y" in nested:
        return float(nested["x"]), float(nested["y"])
    return None


def load_station_poses(path: str | Path) -> dict[str, tuple[float, float, float]]:
    data = load_smap(path)
    stations: dict[str, tuple[float, float, float]] = {}
    for item in data.get("advancedPointList", []):
        if item.get("className") != "LocationMark":
            continue
        name = str(item.get("instanceName", "")).strip()
        xy = _xy_from_mapping(item.get("pos"))
        if name and xy is not None:
            stations[name] = (xy[0], xy[1], float(item.get("dir", 0.0)))
    return stations


def load_route_edges(path: str | Path) -> list[tuple[str, str]]:
    data = load_smap(path)
    edges: list[tuple[str, str]] = []
    for item in data.get("advancedCurveList", []):
        start = (item.get("startPos") or {}).get("instanceName")
        end = (item.get("endPos") or {}).get("instanceName")
        if start and end:
            edges.append((str(start), str(end)))
    return edges


def _world_to_cell(
    x: float,
    y: float,
    min_x: float,
    min_y: float,
    resolution: float,
) -> tuple[int, int]:
    return (
        int(round((x - min_x) / resolution)),
        int(round((y - min_y) / resolution)),
    )


def _mark_point(grid: list[list[int]], x: int, y: int, value: int, radius: int = 1):
    height = len(grid)
    width = len(grid[0]) if height else 0
    for yy in range(y - radius, y + radius + 1):
        if not 0 <= yy < height:
            continue
        for xx in range(x - radius, x + radius + 1):
            if 0 <= xx < width:
                grid[yy][xx] = value


def _mark_line(grid: list[list[int]], start: tuple[int, int], end: tuple[int, int]):
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        _mark_point(grid, x0, y0, 100, radius=1)
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


def build_occupancy_fields(path: str | Path) -> dict[str, Any]:
    data = load_smap(path)
    header = data["header"]
    resolution = float(header["resolution"])
    min_x = float(header["minPos"]["x"])
    min_y = float(header["minPos"]["y"])
    max_x = float(header["maxPos"]["x"])
    max_y = float(header["maxPos"]["y"])
    width = max(1, int(math.ceil((max_x - min_x) / resolution)) + 1)
    height = max(1, int(math.ceil((max_y - min_y) / resolution)) + 1)
    grid = [[0 for _ in range(width)] for _ in range(height)]

    for point in data.get("normalPosList", []):
        xy = _xy_from_mapping(point)
        if xy is None:
            continue
        cell = _world_to_cell(
            xy[0],
            xy[1],
            min_x,
            min_y,
            resolution,
        )
        _mark_point(grid, cell[0], cell[1], 100, radius=1)

    for item in data.get("advancedLineList", []):
        line = item.get("line") or {}
        start = line.get("startPos")
        end = line.get("endPos")
        start_xy = _xy_from_mapping(start)
        end_xy = _xy_from_mapping(end)
        if start_xy is None or end_xy is None:
            continue
        start_cell = _world_to_cell(start_xy[0], start_xy[1], min_x, min_y, resolution)
        end_cell = _world_to_cell(end_xy[0], end_xy[1], min_x, min_y, resolution)
        _mark_line(grid, start_cell, end_cell)

    return {
        "resolution": resolution,
        "origin_x": min_x,
        "origin_y": min_y,
        "width": width,
        "height": height,
        "data": [value for row in grid for value in row],
    }


def make_occupancy_grid(path: str | Path, stamp, frame_id: str = "map"):
    from nav_msgs.msg import OccupancyGrid

    fields = build_occupancy_fields(path)
    message = OccupancyGrid()
    message.header.stamp = stamp
    message.header.frame_id = frame_id
    message.info.map_load_time = stamp
    message.info.resolution = fields["resolution"]
    message.info.width = fields["width"]
    message.info.height = fields["height"]
    message.info.origin.position.x = fields["origin_x"]
    message.info.origin.position.y = fields["origin_y"]
    message.info.origin.orientation.w = 1.0
    message.data = fields["data"]
    return message


def make_station_markers(path: str | Path, stamp, frame_id: str = "map"):
    from geometry_msgs.msg import Point
    from visualization_msgs.msg import Marker, MarkerArray

    stations = load_station_poses(path)
    edges = load_route_edges(path)
    result = MarkerArray()
    marker_id = 0

    for name in sorted(stations):
        x, y, _yaw = stations[name]
        sphere = Marker()
        sphere.header.frame_id = frame_id
        sphere.header.stamp = stamp
        sphere.ns = "stations"
        sphere.id = marker_id
        marker_id += 1
        sphere.type = Marker.SPHERE
        sphere.action = Marker.ADD
        sphere.pose.position.x = x
        sphere.pose.position.y = y
        sphere.pose.position.z = 0.08
        sphere.pose.orientation.w = 1.0
        sphere.scale.x = sphere.scale.y = sphere.scale.z = 0.16
        sphere.color.r = 0.15
        sphere.color.g = 0.75
        sphere.color.b = 0.25
        sphere.color.a = 1.0
        result.markers.append(sphere)

        label = Marker()
        label.header = sphere.header
        label.ns = "station_labels"
        label.id = marker_id
        marker_id += 1
        label.type = Marker.TEXT_VIEW_FACING
        label.action = Marker.ADD
        label.pose.position.x = x
        label.pose.position.y = y
        label.pose.position.z = 0.35
        label.pose.orientation.w = 1.0
        label.scale.z = 0.22
        label.color.r = label.color.g = label.color.b = label.color.a = 1.0
        label.text = name
        result.markers.append(label)

    lines = Marker()
    lines.header.frame_id = frame_id
    lines.header.stamp = stamp
    lines.ns = "route_edges"
    lines.id = marker_id
    lines.type = Marker.LINE_LIST
    lines.action = Marker.ADD
    lines.scale.x = 0.035
    lines.color.r = 0.2
    lines.color.g = 0.65
    lines.color.b = 1.0
    lines.color.a = 0.8
    unique_edges: set[tuple[str, str]] = set()
    for start, end in edges:
        key = tuple(sorted((start, end)))
        if key in unique_edges or start not in stations or end not in stations:
            continue
        unique_edges.add(key)
        for name in (start, end):
            point = Point()
            point.x, point.y, _ = stations[name]
            point.z = 0.02
            lines.points.append(point)
    result.markers.append(lines)
    return result
