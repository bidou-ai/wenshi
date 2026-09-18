"""Independent field setup data for the expert demonstration."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np


DEMO_SETUP_KIND = "wenshi_expert_demo"
DEMO_SETUP_DRAFT_KIND = "wenshi_expert_demo_draft"
DRAFT_FILENAME = "setup_draft.json"
A_STATIONS = tuple(f"A-{index:02d}" for index in range(1, 9))
B_LEFT_STATIONS = tuple(f"B-L-{index:02d}" for index in range(1, 9))
B_RIGHT_STATIONS = tuple(f"B-R-{index:02d}" for index in range(1, 9))
RECORDED_STATIONS = A_STATIONS + B_RIGHT_STATIONS
OBSERVATION_GROUPS = A_STATIONS + B_LEFT_STATIONS + B_RIGHT_STATIONS
LEGACY_STATIONS = tuple(
    [f"left-{index:02d}" for index in range(1, 9)]
    + [f"right-{index:02d}" for index in range(1, 9)]
)
DEMO_PLANTS = tuple(
    [(f"A-{index:02d}", index, True) for index in range(1, 9)]
    + [(f"B-L-{index:02d}", 17 - index, True) for index in range(1, 9)]
    + [(f"B-R-{index:02d}", 16 + index, True) for index in range(1, 9)]
    + [(f"C-{index:02d}", 33 - index, False) for index in range(1, 9)]
)
VIEWPOINT_NAMES = ("home_safe", "left", "right")
LEGACY_VIEWPOINT_NAMES = VIEWPOINT_NAMES + ("center",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_angle(value: float) -> float:
    return math.atan2(math.sin(float(value)), math.cos(float(value)))


def _finite_pose(value: Any, label: str) -> tuple[float, float, float]:
    target = value.get("pose", value) if isinstance(value, dict) else value
    if not isinstance(target, dict):
        raise ValueError(f"{label} 缺少 pose")
    try:
        pose = tuple(float(target[name]) for name in ("x", "y", "angle"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须包含 x/y/angle") from exc
    if not all(math.isfinite(item) for item in pose):
        raise ValueError(f"{label} 包含非有限坐标")
    return pose


def _finite_joint(value: Any, label: str) -> list[float]:
    target = value.get("joint") if isinstance(value, dict) else None
    if not isinstance(target, (list, tuple)) or len(target) != 6:
        raise ValueError(f"{label} 必须包含 6 个关节角")
    joint = [float(item) for item in target]
    if not all(math.isfinite(item) for item in joint):
        raise ValueError(f"{label} 包含非有限关节角")
    return joint


class DemoSetupSession:
    """Collect and atomically publish evidence used only by ``wenshi.sh``."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.operator = ""
        self.calibration_board: dict[str, Any] = {"tag_id": 0}
        self.tags: dict[str, dict[str, Any]] = {}
        self.stations: dict[str, dict[str, Any]] = {}
        self.viewpoints: dict[str, dict[str, Any]] = {}

    @property
    def draft_path(self) -> Path:
        return self.root / DRAFT_FILENAME

    def set_operator(self, operator: str) -> None:
        self.operator = str(operator).strip()
        self.checkpoint()

    def _draft_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": DEMO_SETUP_DRAFT_KIND,
            "updated_at": _now(),
            "operator": self.operator,
            "calibration_board": dict(self.calibration_board),
            "tags": {name: dict(record) for name, record in self.tags.items()},
            "stations": {name: dict(record) for name, record in self.stations.items()},
            "viewpoints": {name: dict(record) for name, record in self.viewpoints.items()},
        }

    def checkpoint(self) -> None:
        temporary = self.draft_path.with_suffix(self.draft_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._draft_payload(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.draft_path)

    def _existing_photo(self, relative: str, label: str) -> str:
        value = Path(str(relative))
        if value.is_absolute():
            raise ValueError(f"{label} 照片必须位于恢复目录内")
        path = (self.root / value).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"{label} 照片不在恢复目录内") from exc
        if not path.is_file():
            raise ValueError(f"{label} 照片不存在: {path}")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise ValueError(f"无法读取 {label} 照片: {path}")
        return value.as_posix()

    @staticmethod
    def _recorded_at(path: Path) -> str:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()

    def _recover_known_photos(self) -> None:
        board_relative = Path("reference") / "tag-0-calibration-board.jpg"
        board_path = self.root / board_relative
        if not self.calibration_board.get("photo") and board_path.exists():
            photo = self._existing_photo(board_relative.as_posix(), "Tag 0 标定板")
            self.calibration_board = {
                "tag_id": 0,
                "photo": photo,
                "recorded_at": self._recorded_at(board_path),
            }
        for plant_id, tag_id, observed in DEMO_PLANTS:
            if plant_id in self.tags:
                continue
            relative = Path("tags") / f"{plant_id}.jpg"
            path = self.root / relative
            if not path.exists():
                continue
            photo = self._existing_photo(relative.as_posix(), plant_id)
            self.tags[plant_id] = {
                "plant_id": plant_id,
                "tag_id": tag_id,
                "observed": observed,
                "photo": photo,
                "recorded_at": self._recorded_at(path),
            }

    def _load_draft(self) -> None:
        try:
            payload = json.loads(self.draft_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取 Demo setup 草稿 {self.draft_path}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("kind") != DEMO_SETUP_DRAFT_KIND:
            raise ValueError(f"不是有效的 Demo setup 草稿: {self.draft_path}")
        if payload.get("schema_version") != 1:
            raise ValueError("Demo setup 草稿版本不受支持")
        operator = payload.get("operator", "")
        if not isinstance(operator, str):
            raise ValueError("Demo setup 草稿的操作员字段无效")
        self.operator = operator.strip()

        board = payload.get("calibration_board", {"tag_id": 0})
        if not isinstance(board, dict) or board.get("tag_id") != 0:
            raise ValueError("Demo setup 草稿的 Tag 0 记录无效")
        if board.get("photo"):
            board = dict(board)
            board["photo"] = self._existing_photo(str(board["photo"]), "Tag 0 标定板")
        self.calibration_board = dict(board)

        expected_tags = {name: (tag_id, observed) for name, tag_id, observed in DEMO_PLANTS}
        tags = payload.get("tags", {})
        if not isinstance(tags, dict) or not set(tags).issubset(expected_tags):
            raise ValueError("Demo setup 草稿包含未知 Tag 记录")
        for plant_id, record in tags.items():
            tag_id, observed = expected_tags[plant_id]
            if (
                not isinstance(record, dict)
                or record.get("plant_id") != plant_id
                or record.get("tag_id") != tag_id
                or record.get("observed") is not observed
            ):
                raise ValueError(f"Demo setup 草稿的 Tag 记录无效: {plant_id}")
            value = dict(record)
            value["photo"] = self._existing_photo(str(value.get("photo", "")), f"Tag {plant_id}")
            self.tags[plant_id] = value

        stations = payload.get("stations", {})
        allowed_stations = set(OBSERVATION_GROUPS) | set(LEGACY_STATIONS)
        if not isinstance(stations, dict) or not set(stations).issubset(allowed_stations):
            raise ValueError("Demo setup 草稿包含未知停车点")
        for group_id, record in stations.items():
            if not isinstance(record, dict) or record.get("pose_source") not in {"agv_status", "map_mirror"}:
                raise ValueError(f"Demo setup 草稿的停车点记录无效: {group_id}")
            x, y, angle = _finite_pose(record, f"停车点 {group_id}")
            value = dict(record)
            value["pose"] = {"x": x, "y": y, "angle": angle}
            value["photo"] = self._existing_photo(str(value.get("photo", "")), f"停车点 {group_id}")
            self.stations[group_id] = value

        viewpoints = payload.get("viewpoints", {})
        if not isinstance(viewpoints, dict) or not set(viewpoints).issubset(LEGACY_VIEWPOINT_NAMES):
            raise ValueError("Demo setup 草稿包含未知机械臂姿态")
        for name, record in viewpoints.items():
            joint = _finite_joint(record, f"姿态 {name}")
            value = dict(record)
            value["joint"] = joint
            if "tcp" in value:
                tcp = [float(item) for item in value["tcp"]]
                if len(tcp) != 6 or not all(math.isfinite(item) for item in tcp):
                    raise ValueError(f"姿态 {name} 的 TCP 必须包含 6 个有限数值")
                value["tcp"] = tcp
            self.viewpoints[name] = value

    @classmethod
    def resume(cls, root: Path) -> "DemoSetupSession":
        source = Path(root).expanduser().resolve()
        if not source.is_dir():
            raise ValueError(f"Demo setup 恢复目录不存在: {source}")
        session = cls(source)
        if session.draft_path.exists():
            session._load_draft()
        elif any((source / "stations").glob("*.jpg")):
            raise ValueError("恢复目录已有停车点照片但缺少 setup_draft.json，无法安全恢复 AGV 位姿")
        session._recover_known_photos()
        session.checkpoint()
        return session

    def next_missing_tag(self) -> tuple[str, int, bool] | None:
        for plant in DEMO_PLANTS:
            if plant[0] not in self.tags:
                return plant
        return None

    def migrate_and_mirror_stations(self, *, top_route_y: float, bottom_route_y: float) -> None:
        top_y = float(top_route_y)
        bottom_y = float(bottom_route_y)
        if not math.isfinite(top_y) or not math.isfinite(bottom_y) or math.isclose(top_y, bottom_y):
            raise ValueError("Demo 停车点镜像需要两个不同的有限路线 y 坐标")
        changed = False
        for index in range(1, 9):
            for legacy, current in (
                (f"left-{index:02d}", f"A-{index:02d}"),
                (f"right-{index:02d}", f"B-R-{index:02d}"),
            ):
                if legacy not in self.stations:
                    continue
                if current in self.stations:
                    raise ValueError(f"停车点迁移冲突: {legacy} 与 {current} 同时存在")
                record = dict(self.stations.pop(legacy))
                record["group_id"] = current
                record["migrated_from"] = legacy
                self.stations[current] = record
                changed = True

        axis_y = (top_y + bottom_y) / 2.0
        for index in range(1, 9):
            source_id = f"B-R-{index:02d}"
            target_id = f"B-L-{index:02d}"
            source = self.stations.get(source_id)
            if source is None:
                continue
            source_x, source_y, source_angle = _finite_pose(source, f"停车点 {source_id}")
            expected_pose = {
                "x": source_x,
                "y": 2.0 * axis_y - source_y,
                "angle": _normalize_angle(source_angle + math.pi),
            }
            existing = self.stations.get(target_id)
            if existing is not None:
                self._validate_mirror(target_id, existing)
                current_pose = _finite_pose(existing, f"停车点 {target_id}")
                if any(
                    not math.isclose(current, expected, rel_tol=0.0, abs_tol=1e-6)
                    for current, expected in zip(current_pose, expected_pose.values())
                ):
                    raise ValueError(f"{target_id} 与当前地图镜像轴不一致，拒绝静默修改")
                continue
            self.stations[target_id] = {
                "group_id": target_id,
                "pose_source": "map_mirror",
                "pose": expected_pose,
                "photo": source.get("photo"),
                "recorded_at": _now(),
                "derived_from": source_id,
                "mirror_axis_y": axis_y,
            }
            changed = True
        if changed:
            self.checkpoint()

    def _validate_mirror(self, group_id: str, station: Mapping[str, Any]) -> None:
        if group_id not in B_LEFT_STATIONS or station.get("pose_source") != "map_mirror":
            raise ValueError(f"停车点 {group_id} 不是有效的 B-L 镜像点")
        source_id = f"B-R-{group_id[-2:]}"
        if station.get("derived_from") != source_id or source_id not in self.stations:
            raise ValueError(f"停车点 {group_id} 缺少 B-R 镜像来源")
        try:
            axis_y = float(station["mirror_axis_y"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"停车点 {group_id} 缺少有效镜像轴") from exc
        if not math.isfinite(axis_y):
            raise ValueError(f"停车点 {group_id} 缺少有效镜像轴")
        x, y, angle = _finite_pose(station, f"停车点 {group_id}")
        source_x, source_y, source_angle = _finite_pose(self.stations[source_id], f"停车点 {source_id}")
        expected = (source_x, 2.0 * axis_y - source_y, _normalize_angle(source_angle + math.pi))
        if any(not math.isclose(current, target, rel_tol=0.0, abs_tol=1e-6) for current, target in zip((x, y, angle), expected)):
            raise ValueError(f"停车点 {group_id} 的镜像坐标与 {source_id} 不一致")

    def _write_photo(self, relative: Path, photo: np.ndarray) -> str:
        if not isinstance(photo, np.ndarray) or photo.size == 0:
            raise ValueError("现场照片不能为空")
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), photo, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"照片写入失败: {path}")
        return str(relative)

    def record_calibration_board(self, photo: np.ndarray) -> None:
        self.calibration_board = {
            "tag_id": 0,
            "photo": self._write_photo(Path("reference") / "tag-0-calibration-board.jpg", photo),
            "recorded_at": _now(),
        }
        self.checkpoint()

    def record_tag(self, plant_id: str, tag_id: int, *, photo: np.ndarray, observed: bool | None = None) -> None:
        expected = {name: (value, enabled) for name, value, enabled in DEMO_PLANTS}.get(str(plant_id))
        if expected is None:
            raise ValueError(f"未知 Demo 植株: {plant_id}")
        expected_id, expected_observed = expected
        if str(plant_id) in self.tags:
            raise ValueError(f"重复登记 Tag: {plant_id}")
        if int(tag_id) != expected_id:
            raise ValueError(f"{plant_id} 的现场 Tag 应为 {expected_id}，收到 {tag_id}")
        if any(int(record["tag_id"]) == int(tag_id) for record in self.tags.values()):
            raise ValueError(f"重复 Tag ID: {tag_id}")
        enabled = expected_observed if observed is None else bool(observed)
        if enabled != expected_observed:
            raise ValueError(f"{plant_id} 的 Demo 观察范围标记不正确")
        self.tags[str(plant_id)] = {
            "plant_id": str(plant_id),
            "tag_id": int(tag_id),
            "observed": enabled,
            "photo": self._write_photo(Path("tags") / f"{plant_id}.jpg", photo),
            "recorded_at": _now(),
        }
        self.checkpoint()

    def record_station(
        self,
        group_id: str,
        pose: Mapping[str, Any],
        *,
        photo: np.ndarray,
        source: str,
        note: str = "",
    ) -> None:
        group_id = str(group_id)
        if group_id not in set(RECORDED_STATIONS) | set(LEGACY_STATIONS):
            raise ValueError(f"未知 Demo 停车点: {group_id}")
        if group_id in self.stations:
            raise ValueError(f"重复登记停车点: {group_id}")
        x, y, angle = _finite_pose(dict(pose), f"停车点 {group_id}")
        source = str(source).strip().lower()
        if source != "agv_status":
            raise ValueError("Demo 停车点只接受 AGV 实时位姿")
        record: dict[str, Any] = {
            "group_id": group_id,
            "pose_source": source,
            "pose": {"x": x, "y": y, "angle": angle},
            "photo": self._write_photo(Path("stations") / f"{group_id}.jpg", photo),
            "recorded_at": _now(),
        }
        if str(note).strip():
            record["note"] = str(note).strip()
        self.stations[group_id] = record
        self.checkpoint()

    def record_viewpoint(
        self,
        name: str,
        joint: list[float] | tuple[float, ...],
        tcp: list[float] | tuple[float, ...] | None = None,
    ) -> None:
        if name not in VIEWPOINT_NAMES:
            raise ValueError("Demo 姿态必须是 home_safe、left 或 right")
        record: dict[str, Any] = {"joint": _finite_joint({"joint": joint}, f"姿态 {name}"), "recorded_at": _now()}
        if tcp is not None:
            values = [float(item) for item in tcp]
            if len(values) != 6 or not all(math.isfinite(item) for item in values):
                raise ValueError(f"姿态 {name} 的 TCP 必须包含 6 个有限数值")
            record["tcp"] = values
        self.viewpoints[name] = record
        self.checkpoint()

    def _validate(self) -> None:
        if self.calibration_board.get("tag_id") != 0 or not self.calibration_board.get("photo"):
            raise ValueError("Demo setup 缺少 Tag 0 标定板照片")
        expected_tags = {plant_id for plant_id, _tag_id, _observed in DEMO_PLANTS}
        required_tags = {plant_id for plant_id, _tag_id, observed in DEMO_PLANTS if observed}
        if not set(self.tags).issubset(expected_tags):
            raise ValueError("Demo setup 包含未知 Tag 登记")
        if not required_tags.issubset(self.tags):
            missing = sorted(required_tags - set(self.tags))
            raise ValueError("Demo setup 缺少展示所需的 1-24 Tag 登记: " + ", ".join(missing))
        if set(self.stations) != set(OBSERVATION_GROUPS):
            missing = sorted(set(OBSERVATION_GROUPS) - set(self.stations))
            raise ValueError("Demo setup 必须包含 24 个独立水稻停车点: " + ", ".join(missing))
        for group_id, station in self.stations.items():
            if group_id in B_LEFT_STATIONS:
                self._validate_mirror(group_id, station)
            elif station.get("pose_source") != "agv_status":
                raise ValueError(f"停车点 {group_id} 不是 AGV 实时位姿")
            if not station.get("photo"):
                raise ValueError(f"停车点 {group_id} 缺少照片")
        if not set(VIEWPOINT_NAMES).issubset(self.viewpoints) or not set(self.viewpoints).issubset(LEGACY_VIEWPOINT_NAMES):
            missing = sorted(set(VIEWPOINT_NAMES) - set(self.viewpoints))
            raise ValueError("Demo setup 缺少机械臂姿态: " + ", ".join(missing))

    def publish(self, destination: Path) -> None:
        self._validate()
        destination = Path(destination).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)

        def published(record: Mapping[str, Any]) -> dict[str, Any]:
            value = dict(record)
            if value.get("photo"):
                value["photo"] = os.path.relpath(self.root / str(value["photo"]), destination.parent)
            return value

        payload = {
            "schema_version": 1,
            "kind": DEMO_SETUP_KIND,
            "published_at": _now(),
            "operator": self.operator,
            "tag_family": "tag25h7",
            "tag_size_m": 0.09,
            "calibration_board": published(self.calibration_board),
            "tags": {plant_id: published(record) for plant_id, record in self.tags.items()},
            "stations": {group_id: published(record) for group_id, record in self.stations.items()},
            "viewpoints": {name: dict(self.viewpoints[name]) for name in VIEWPOINT_NAMES},
        }
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(destination)


def load_demo_setup(path: str | Path) -> dict[str, Any]:
    """Load only the independently published expert-demo setup schema."""
    source = Path(path).expanduser().resolve()
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取独立 Demo setup {source}: {exc}") from exc
    if not isinstance(value, dict) or value.get("kind") != DEMO_SETUP_KIND:
        raise ValueError("不是独立 Demo setup；请运行 ./wenshi.sh --setup")
    if value.get("schema_version") != 1:
        raise ValueError("独立 Demo setup schema_version 必须为 1；请重新运行 ./wenshi.sh --setup")
    if value.get("tag_family") != "tag25h7":
        raise ValueError("独立 Demo setup 的 Tag family 必须是 tag25h7")
    try:
        tag_size_m = float(value.get("tag_size_m"))
    except (TypeError, ValueError) as exc:
        raise ValueError("独立 Demo setup 缺少有效 Tag 尺寸") from exc
    if not math.isclose(tag_size_m, 0.09, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("独立 Demo setup 的 Tag 外框尺寸必须是 0.09m")

    def require_photo(record: Any, label: str) -> None:
        if not isinstance(record, dict):
            raise ValueError(f"独立 Demo setup 的 {label} 记录格式无效")
        photo = record.get("photo")
        if not isinstance(photo, str) or not photo.strip():
            raise ValueError(f"独立 Demo setup 的 {label} 缺少照片")
        evidence = (source.parent / photo).resolve()
        try:
            evidence.relative_to(source.parent)
        except ValueError as exc:
            raise ValueError(f"独立 Demo setup 的 {label} 照片不在 Demo 目录内") from exc
        if not evidence.is_file():
            raise ValueError(f"独立 Demo setup 的 {label} 照片不存在: {evidence}")

    stations = value.get("stations")
    if not isinstance(stations, dict) or set(stations) != set(OBSERVATION_GROUPS):
        missing = sorted(set(OBSERVATION_GROUPS) - set(stations or {}))
        raise ValueError("独立 Demo setup 必须恰好包含 24 个独立水稻停车点: " + ", ".join(missing))
    malformed = [group_id for group_id in OBSERVATION_GROUPS if not isinstance(stations[group_id], dict)]
    if malformed:
        raise ValueError("独立 Demo setup 的停车点记录格式无效: " + ", ".join(malformed))
    manual = [
        group_id
        for group_id in A_STATIONS + B_RIGHT_STATIONS
        if stations[group_id].get("pose_source") != "agv_status"
    ]
    if manual:
        raise ValueError("独立 Demo 只接受 AGV 实时记录的停车点: " + ", ".join(manual))
    mirror_session = DemoSetupSession.__new__(DemoSetupSession)
    mirror_session.stations = stations
    for group_id in B_LEFT_STATIONS:
        mirror_session._validate_mirror(group_id, stations[group_id])
    normalized_stations = {
        group_id: _finite_pose(stations[group_id], f"停车点 {group_id}")
        for group_id in OBSERVATION_GROUPS
    }
    for group_id in OBSERVATION_GROUPS:
        require_photo(stations[group_id], f"停车点 {group_id}")

    expected_tags = {plant_id: (tag_id, observed) for plant_id, tag_id, observed in DEMO_PLANTS}
    required_tags = {plant_id for plant_id, (_tag_id, observed) in expected_tags.items() if observed}
    tags = value.get("tags")
    if not isinstance(tags, dict) or not set(tags).issubset(expected_tags) or not required_tags.issubset(tags):
        raise ValueError("独立 Demo setup 必须包含展示所需的 1-24 Tag 登记；C 排可延期")
    for plant_id, record in tags.items():
        tag_id, observed = expected_tags[plant_id]
        if not isinstance(record, dict) or record.get("tag_id") != tag_id or record.get("observed") is not observed:
            raise ValueError(f"独立 Demo setup 的 Tag 登记不正确: {plant_id}")
        require_photo(record, f"Tag {plant_id}")
    board = value.get("calibration_board")
    if not isinstance(board, dict) or board.get("tag_id") != 0:
        raise ValueError("独立 Demo setup 缺少 Tag 0 标定板登记")
    require_photo(board, "Tag 0 标定板")

    viewpoints = value.get("viewpoints")
    if (
        not isinstance(viewpoints, dict)
        or not set(VIEWPOINT_NAMES).issubset(viewpoints)
        or not set(viewpoints).issubset(LEGACY_VIEWPOINT_NAMES)
    ):
        raise ValueError("独立 Demo setup 缺少 home_safe/left/right 姿态")
    normalized_viewpoints = {name: viewpoints[name] for name in VIEWPOINT_NAMES}
    aliases = {"left": "camera_left", "right": "camera_right"}
    for alias, name in aliases.items():
        _finite_joint(viewpoints[alias], f"姿态 {alias}")
        normalized_viewpoints[name] = viewpoints[alias]
    _finite_joint(viewpoints["home_safe"], "姿态 home_safe")
    return {
        "source": str(source),
        "stations": normalized_stations,
        "viewpoints": normalized_viewpoints,
        "raw": value,
    }


__all__ = [
    "A_STATIONS",
    "B_LEFT_STATIONS",
    "B_RIGHT_STATIONS",
    "DEMO_PLANTS",
    "DEMO_SETUP_KIND",
    "OBSERVATION_GROUPS",
    "RECORDED_STATIONS",
    "VIEWPOINT_NAMES",
    "DemoSetupSession",
    "load_demo_setup",
]
