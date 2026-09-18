"""Independent, Tag-free setup data for the 9.18 expert demonstration."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

import cv2
import numpy as np

from .map_utils import load_smap


SETUP_KIND = "wenshi_918_expert_demo"
DRAFT_KIND = "wenshi_918_expert_demo_draft"
DRAFT_FILENAME = "setup_draft.json"
VIEWPOINT_NAMES = ("home_safe", "left", "right")
ROUTE_MODES = ("shuttle", "loop")
MIN_ANCHOR_DISTANCE_M = 0.20
DEFAULT_MAX_OBSERVATION_OFFSET_M = 0.25


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: Path, root: Path, label: str) -> Path:
    resolved = Path(path).expanduser().resolve()
    try:
        resolved.relative_to(Path(root).expanduser().resolve())
    except ValueError as exc:
        raise ValueError(f"{label} 不在 9.18 运行目录内: {resolved}") from exc
    return resolved


def _finite_pose(value: Any, label: str) -> tuple[float, float, float]:
    target = value.get("pose", value) if isinstance(value, dict) else value
    if not isinstance(target, dict):
        raise ValueError(f"{label} 缺少 pose")
    try:
        pose = tuple(float(target[name]) for name in ("x", "y", "angle"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须包含有限的 x/y/angle") from exc
    if not all(math.isfinite(item) for item in pose):
        raise ValueError(f"{label} 包含非有限位姿")
    return pose


def _finite_joint(value: Any, label: str) -> list[float]:
    target = value.get("joint") if isinstance(value, dict) else value
    if not isinstance(target, (list, tuple)) or len(target) != 6:
        raise ValueError(f"{label} 必须包含 6 个关节角")
    try:
        result = [float(item) for item in target]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 包含无效关节角") from exc
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{label} 包含非有限关节角")
    return result


def validate_918_map(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        value = load_smap(source)
        header = value["header"]
        minimum = header["minPos"]
        maximum = header["maxPos"]
        fields = {
            "map_name": str(header.get("mapName", source.stem)),
            "resolution": float(header["resolution"]),
            "min_x": float(minimum["x"]),
            "min_y": float(minimum["y"]),
            "max_x": float(maximum["x"]),
            "max_y": float(maximum["y"]),
        }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"9.18 地图不是有效的 smap: {source}: {exc}") from exc
    numeric = [fields[name] for name in ("resolution", "min_x", "min_y", "max_x", "max_y")]
    if not all(math.isfinite(item) for item in numeric):
        raise ValueError("9.18 地图范围或分辨率包含非有限数值")
    if fields["resolution"] <= 0.0:
        raise ValueError("9.18 地图分辨率必须大于 0")
    if fields["max_x"] <= fields["min_x"] or fields["max_y"] <= fields["min_y"]:
        raise ValueError("9.18 地图范围无效")
    return fields


def _point_segment_distance(
    point: tuple[float, float, float],
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
    ratio = max(0.0, min(1.0, ratio))
    projected_x = start[0] + ratio * dx
    projected_y = start[1] + ratio * dy
    return math.hypot(point[0] - projected_x, point[1] - projected_y)


def _route_pairs(order: Sequence[str], mode: str) -> list[tuple[str, str]]:
    pairs = list(zip(order, order[1:]))
    if mode == "loop":
        pairs.append((order[-1], order[0]))
    return pairs


def _validate_geometry(
    anchors: Mapping[str, Any],
    order: Sequence[str],
    mode: str,
    observations: Mapping[str, Any],
    max_observation_offset_m: float,
) -> None:
    if mode not in ROUTE_MODES:
        raise ValueError("9.18 路线模式必须是 shuttle 或 loop")
    if len(order) < 2 or len(order) != len(set(order)) or set(order) != set(anchors):
        raise ValueError("9.18 路线必须包含至少两个唯一锚点，且 order 与 anchors 一致")
    normalized = {name: _finite_pose(anchors[name], f"路线锚点 {name}") for name in order}
    pairs = _route_pairs(order, mode)
    for start_name, end_name in pairs:
        start = normalized[start_name]
        end = normalized[end_name]
        distance = math.hypot(end[0] - start[0], end[1] - start[1])
        if distance < MIN_ANCHOR_DISTANCE_M:
            raise ValueError(
                f"路线锚点 {start_name}->{end_name} 距离 {distance:.3f}m 过短，"
                f"必须至少 {MIN_ANCHOR_DISTANCE_M:.2f}m"
            )
    limit = float(max_observation_offset_m)
    if not math.isfinite(limit) or limit <= 0.0:
        raise ValueError("观察点路线偏移上限无效")
    for name, record in observations.items():
        pose = _finite_pose(record, f"观察点 {name}")
        distance = min(
            _point_segment_distance(pose, normalized[start_name], normalized[end_name])
            for start_name, end_name in pairs
        )
        if distance > limit:
            raise ValueError(f"观察点 {name} 偏离示教路线 {distance:.3f}m，超过 {limit:.3f}m")


class Setup918Session:
    """Collect and atomically publish setup evidence used only by ``9.18.sh``."""

    def __init__(self, root: Path, runtime_root: Path):
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.root = _inside(Path(root), self.runtime_root, "9.18 setup 证据目录")
        self.root.mkdir(parents=True, exist_ok=True)
        self.operator = ""
        self.map_record: dict[str, Any] = {}
        self.route_mode = ""
        self.anchor_count = 0
        self.anchors: dict[str, dict[str, Any]] = {}
        self.observation_count = 0
        self.observations: dict[str, dict[str, Any]] = {}
        self.viewpoints: dict[str, dict[str, Any]] = {}
        self.max_observation_offset_m = DEFAULT_MAX_OBSERVATION_OFFSET_M

    @property
    def draft_path(self) -> Path:
        return self.root / DRAFT_FILENAME

    def _draft_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": DRAFT_KIND,
            "updated_at": _now(),
            "operator": self.operator,
            "map": dict(self.map_record),
            "route": {
                "mode": self.route_mode,
                "anchor_count": self.anchor_count,
                "order": list(self.anchors),
                "anchors": {name: dict(value) for name, value in self.anchors.items()},
                "max_observation_offset_m": self.max_observation_offset_m,
            },
            "observation_count": self.observation_count,
            "observations": {name: dict(value) for name, value in self.observations.items()},
            "viewpoints": {name: dict(value) for name, value in self.viewpoints.items()},
        }

    def checkpoint(self) -> None:
        temporary = self.draft_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._draft_payload(), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.draft_path)

    def set_operator(self, operator: str) -> None:
        self.operator = str(operator).strip()
        self.checkpoint()

    def import_map(self, source: Path) -> None:
        source = Path(source).expanduser().resolve()
        fields = validate_918_map(source)
        destination = self.root / "map.smap"
        temporary = destination.with_suffix(".smap.tmp")
        shutil.copyfile(source, temporary)
        temporary.replace(destination)
        self.map_record = {
            "evidence": destination.name,
            "sha256": _sha256(destination),
            "source_name": source.name,
            "header": fields,
            "imported_at": _now(),
        }
        self.checkpoint()

    def set_route(self, mode: str, count: int) -> None:
        mode = str(mode).strip().lower()
        count = int(count)
        if mode not in ROUTE_MODES:
            raise ValueError("9.18 路线模式必须是 shuttle 或 loop")
        if count < 2 or count > 99:
            raise ValueError("9.18 路线锚点数量必须在 2 到 99 之间")
        if self.anchors and (mode != self.route_mode or count != self.anchor_count):
            raise ValueError("已有路线锚点，不能改变路线模式或数量")
        self.route_mode = mode
        self.anchor_count = count
        self.checkpoint()

    def _write_photo(self, relative: Path, photo: np.ndarray) -> str:
        if not isinstance(photo, np.ndarray) or photo.size == 0:
            raise ValueError("9.18 setup 照片为空")
        output = _inside(self.root / relative, self.root, "9.18 setup 照片")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.stem + ".tmp" + output.suffix)
        if not cv2.imwrite(str(temporary), photo, [cv2.IMWRITE_JPEG_QUALITY, 95]):
            raise OSError(f"无法写入 9.18 setup 照片: {output}")
        temporary.replace(output)
        return output.relative_to(self.root).as_posix()

    def record_anchor(
        self,
        name: str,
        pose: Mapping[str, Any],
        *,
        photo: np.ndarray,
        source: str,
    ) -> None:
        if not self.route_mode or self.anchor_count < 2:
            raise ValueError("请先设置 9.18 路线模式和锚点数量")
        expected = f"R-{len(self.anchors) + 1:02d}"
        name = str(name)
        if name != expected or len(self.anchors) >= self.anchor_count:
            raise ValueError(f"下一个 9.18 路线锚点必须是 {expected}")
        if str(source).strip().lower() != "agv_status":
            raise ValueError("9.18 路线锚点只接受 AGV 实时位姿")
        x, y, angle = _finite_pose(dict(pose), f"路线锚点 {name}")
        self.anchors[name] = {
            "name": name,
            "pose_source": "agv_status",
            "pose": {"x": x, "y": y, "angle": angle},
            "photo": self._write_photo(Path("anchors") / f"{name}.jpg", photo),
            "recorded_at": _now(),
        }
        self.checkpoint()

    def set_observation_count(self, count: int) -> None:
        count = int(count)
        if count < 1 or count > 99:
            raise ValueError("9.18 观察点数量必须在 1 到 99 之间")
        if self.observations and count != self.observation_count:
            raise ValueError("已有观察点，不能改变观察点数量")
        self.observation_count = count
        self.checkpoint()

    def record_observation(
        self,
        name: str,
        pose: Mapping[str, Any],
        *,
        arm_view: str,
        photo: np.ndarray,
        note: str = "",
    ) -> None:
        if self.observation_count < 1:
            raise ValueError("请先设置 9.18 观察点数量")
        expected = f"P-{len(self.observations) + 1:02d}"
        name = str(name)
        if name != expected or len(self.observations) >= self.observation_count:
            raise ValueError(f"下一个 9.18 观察点必须是 {expected}")
        arm_view = str(arm_view).strip().lower()
        if arm_view not in {"left", "right"}:
            raise ValueError("9.18 观察点 arm_view 必须是 left 或 right")
        x, y, angle = _finite_pose(dict(pose), f"观察点 {name}")
        record: dict[str, Any] = {
            "name": name,
            "pose_source": "agv_status",
            "pose": {"x": x, "y": y, "angle": angle},
            "arm_view": arm_view,
            "photo": self._write_photo(Path("observations") / f"{name}.jpg", photo),
            "recorded_at": _now(),
        }
        if str(note).strip():
            record["note"] = str(note).strip()
        self.observations[name] = record
        self.checkpoint()

    def record_viewpoint(
        self,
        name: str,
        joint: Sequence[float],
        tcp: Sequence[float] | None = None,
    ) -> None:
        name = str(name)
        if name not in VIEWPOINT_NAMES:
            raise ValueError("9.18 机械臂姿态必须是 home_safe、left 或 right")
        record: dict[str, Any] = {"joint": _finite_joint(joint, f"姿态 {name}"), "recorded_at": _now()}
        if tcp is not None:
            values = [float(item) for item in tcp]
            if len(values) != 6 or not all(math.isfinite(item) for item in values):
                raise ValueError(f"姿态 {name} 的 TCP 必须包含 6 个有限数值")
            record["tcp"] = values
        self.viewpoints[name] = record
        self.checkpoint()

    def copy_viewpoints(self, source: Path) -> None:
        source = Path(source).expanduser().resolve()
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取机械臂姿态来源 {source}: {exc}") from exc
        viewpoints = value.get("viewpoints") if isinstance(value, dict) else None
        if not isinstance(viewpoints, dict):
            raise ValueError("机械臂姿态来源缺少 viewpoints")
        digest = _sha256(source)
        copied: dict[str, dict[str, Any]] = {}
        for name in VIEWPOINT_NAMES:
            record = viewpoints.get(name)
            joint = _finite_joint(record, f"复制姿态 {name}")
            result: dict[str, Any] = {
                "joint": joint,
                "recorded_at": _now(),
                "copied_from_sha256": digest,
            }
            if isinstance(record, dict) and "tcp" in record:
                tcp = [float(item) for item in record["tcp"]]
                if len(tcp) == 6 and all(math.isfinite(item) for item in tcp):
                    result["tcp"] = tcp
            copied[name] = result
        self.viewpoints = copied
        self.checkpoint()

    def _existing_photo(self, relative: str, label: str) -> str:
        value = Path(str(relative))
        if value.is_absolute():
            raise ValueError(f"{label} 照片必须使用 setup 内相对路径")
        path = _inside(self.root / value, self.root, f"{label} 照片")
        if not path.is_file():
            raise ValueError(f"{label} 照片不存在: {path}")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise ValueError(f"{label} 照片无法读取: {path}")
        return value.as_posix()

    @classmethod
    def resume(cls, root: Path, runtime_root: Path) -> "Setup918Session":
        source = Path(root).expanduser().resolve()
        if not source.is_dir():
            raise ValueError(f"9.18 setup 恢复目录不存在: {source}")
        session = cls(source, runtime_root)
        if not session.draft_path.is_file():
            raise ValueError(f"9.18 setup 恢复目录缺少 {DRAFT_FILENAME}")
        try:
            payload = json.loads(session.draft_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取 9.18 setup 草稿: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("kind") != DRAFT_KIND or payload.get("schema_version") != 1:
            raise ValueError("不是有效的 9.18 setup 草稿")
        session.operator = str(payload.get("operator", "")).strip()
        map_record = payload.get("map", {})
        if map_record:
            if not isinstance(map_record, dict):
                raise ValueError("9.18 setup 草稿地图记录无效")
            evidence = _inside(session.root / str(map_record.get("evidence", "")), session.root, "草稿地图")
            validate_918_map(evidence)
            if _sha256(evidence) != map_record.get("sha256"):
                raise ValueError("9.18 setup 草稿地图 SHA-256 不匹配")
            session.map_record = dict(map_record)
        route = payload.get("route", {})
        if not isinstance(route, dict):
            raise ValueError("9.18 setup 草稿路线记录无效")
        session.route_mode = str(route.get("mode", ""))
        session.anchor_count = int(route.get("anchor_count", 0))
        session.max_observation_offset_m = float(
            route.get("max_observation_offset_m", DEFAULT_MAX_OBSERVATION_OFFSET_M)
        )
        anchors = route.get("anchors", {})
        if not isinstance(anchors, dict):
            raise ValueError("9.18 setup 草稿 anchors 无效")
        for name, record in anchors.items():
            if not isinstance(record, dict) or record.get("pose_source") != "agv_status":
                raise ValueError(f"9.18 setup 草稿路线锚点无效: {name}")
            _finite_pose(record, f"路线锚点 {name}")
            restored = dict(record)
            restored["photo"] = session._existing_photo(str(restored.get("photo", "")), f"路线锚点 {name}")
            session.anchors[str(name)] = restored
        session.observation_count = int(payload.get("observation_count", 0))
        observations = payload.get("observations", {})
        if not isinstance(observations, dict):
            raise ValueError("9.18 setup 草稿 observations 无效")
        for name, record in observations.items():
            if not isinstance(record, dict):
                raise ValueError(f"9.18 setup 草稿观察点无效: {name}")
            _finite_pose(record, f"观察点 {name}")
            if record.get("arm_view") not in {"left", "right"}:
                raise ValueError(f"观察点 {name} 的 arm_view 必须是 left 或 right")
            restored = dict(record)
            restored["photo"] = session._existing_photo(str(restored.get("photo", "")), f"观察点 {name}")
            session.observations[str(name)] = restored
        viewpoints = payload.get("viewpoints", {})
        if not isinstance(viewpoints, dict) or not set(viewpoints).issubset(VIEWPOINT_NAMES):
            raise ValueError("9.18 setup 草稿机械臂姿态无效")
        for name, record in viewpoints.items():
            restored = dict(record)
            restored["joint"] = _finite_joint(record, f"姿态 {name}")
            session.viewpoints[str(name)] = restored
        session.checkpoint()
        return session

    def _validate(self) -> None:
        if not self.operator:
            raise ValueError("9.18 setup 缺少操作员")
        if not self.map_record:
            raise ValueError("9.18 setup 缺少新场地地图")
        evidence = _inside(self.root / str(self.map_record.get("evidence", "")), self.root, "9.18 地图")
        validate_918_map(evidence)
        if _sha256(evidence) != self.map_record.get("sha256"):
            raise ValueError("9.18 setup 地图 SHA-256 不匹配")
        expected_anchors = [f"R-{index:02d}" for index in range(1, self.anchor_count + 1)]
        if list(self.anchors) != expected_anchors:
            raise ValueError("9.18 setup 路线锚点尚未全部登记")
        expected_observations = [f"P-{index:02d}" for index in range(1, self.observation_count + 1)]
        if list(self.observations) != expected_observations:
            raise ValueError("9.18 setup 观察点尚未全部登记")
        for name, record in self.anchors.items():
            if record.get("pose_source") != "agv_status":
                raise ValueError(f"路线锚点 {name} 不是 AGV 实时位姿")
            self._existing_photo(str(record.get("photo", "")), f"路线锚点 {name}")
        for name, record in self.observations.items():
            if record.get("pose_source") != "agv_status":
                raise ValueError(f"观察点 {name} 不是 AGV 实时位姿")
            if record.get("arm_view") not in {"left", "right"}:
                raise ValueError(f"观察点 {name} 的 arm_view 必须是 left 或 right")
            self._existing_photo(str(record.get("photo", "")), f"观察点 {name}")
        if set(self.viewpoints) != set(VIEWPOINT_NAMES):
            raise ValueError("9.18 setup 缺少 home_safe/left/right 机械臂姿态")
        for name in VIEWPOINT_NAMES:
            _finite_joint(self.viewpoints[name], f"姿态 {name}")
        _validate_geometry(
            self.anchors,
            expected_anchors,
            self.route_mode,
            self.observations,
            self.max_observation_offset_m,
        )

    def publish(self, destination: Path) -> None:
        self._validate()
        destination = _inside(Path(destination), self.runtime_root, "9.18 setup 发布文件")
        if destination.parent != self.runtime_root:
            raise ValueError("9.18 setup 必须直接发布到 runtime/9.18")
        destination.parent.mkdir(parents=True, exist_ok=True)
        evidence_map = self.root / str(self.map_record["evidence"])
        digest = str(self.map_record["sha256"])
        site_dir = self.runtime_root / "site"
        site_dir.mkdir(parents=True, exist_ok=True)
        published_map = site_dir / f"map-{digest[:12]}.smap"
        if published_map.exists():
            if _sha256(published_map) != digest:
                raise ValueError(f"9.18 已发布地图指纹冲突: {published_map}")
        else:
            temporary_map = published_map.with_suffix(".smap.tmp")
            shutil.copyfile(evidence_map, temporary_map)
            if _sha256(temporary_map) != digest:
                temporary_map.unlink(missing_ok=True)
                raise ValueError("9.18 地图复制后 SHA-256 不匹配")
            temporary_map.replace(published_map)

        def published_record(record: Mapping[str, Any]) -> dict[str, Any]:
            result = dict(record)
            result["photo"] = os.path.relpath(self.root / str(record["photo"]), destination.parent)
            return result

        order = list(self.anchors)
        payload = {
            "schema_version": 1,
            "kind": SETUP_KIND,
            "published_at": _now(),
            "operator": self.operator,
            "map": {
                "path": published_map.relative_to(destination.parent).as_posix(),
                "sha256": digest,
                "header": dict(self.map_record["header"]),
            },
            "route": {
                "mode": self.route_mode,
                "order": order,
                "anchors": {name: published_record(self.anchors[name]) for name in order},
                "max_observation_offset_m": self.max_observation_offset_m,
            },
            "observations": {
                name: published_record(record) for name, record in self.observations.items()
            },
            "viewpoints": {name: dict(self.viewpoints[name]) for name in VIEWPOINT_NAMES},
        }
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)


def load_918_setup(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 9.18 独立 setup {source}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != SETUP_KIND:
        raise ValueError("不是 9.18 独立 Demo setup；请运行 ./9.18.sh --setup --map PATH")
    if payload.get("schema_version") != 1:
        raise ValueError("9.18 独立 Demo setup schema_version 必须为 1")
    runtime_root = source.parent

    map_record = payload.get("map")
    if not isinstance(map_record, dict):
        raise ValueError("9.18 独立 setup 缺少地图记录")
    map_path = _inside(runtime_root / str(map_record.get("path", "")), runtime_root, "9.18 地图")
    validate_918_map(map_path)
    digest = str(map_record.get("sha256", ""))
    if len(digest) != 64 or _sha256(map_path) != digest:
        raise ValueError("9.18 地图 SHA-256 指纹不匹配")

    def require_photo(record: Any, label: str) -> None:
        if not isinstance(record, dict):
            raise ValueError(f"{label} 记录格式无效")
        value = record.get("photo")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} 缺少照片")
        photo = _inside(runtime_root / value, runtime_root, f"{label} 照片")
        if not photo.is_file():
            raise ValueError(f"{label} 照片不存在: {photo}")
        image = cv2.imread(str(photo), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise ValueError(f"{label} 照片无法读取: {photo}")

    route = payload.get("route")
    if not isinstance(route, dict):
        raise ValueError("9.18 独立 setup 缺少路线")
    mode = str(route.get("mode", ""))
    order = route.get("order")
    anchors = route.get("anchors")
    if not isinstance(order, list) or not all(isinstance(name, str) for name in order):
        raise ValueError("9.18 路线 order 无效")
    if not isinstance(anchors, dict):
        raise ValueError("9.18 路线 anchors 无效")
    expected_order = [f"R-{index:02d}" for index in range(1, len(order) + 1)]
    if order != expected_order:
        raise ValueError("9.18 路线锚点名称或顺序无效")
    for name in order:
        record = anchors.get(name)
        if not isinstance(record, dict) or record.get("pose_source") != "agv_status":
            raise ValueError(f"路线锚点 {name} 不是 AGV 实时位姿")
        require_photo(record, f"路线锚点 {name}")

    observations = payload.get("observations")
    if not isinstance(observations, dict) or not observations:
        raise ValueError("9.18 独立 setup 至少需要一个观察点")
    expected_observations = [f"P-{index:02d}" for index in range(1, len(observations) + 1)]
    if list(observations) != expected_observations:
        raise ValueError("9.18 观察点名称或顺序无效")
    for name, record in observations.items():
        if not isinstance(record, dict) or record.get("pose_source") != "agv_status":
            raise ValueError(f"观察点 {name} 不是 AGV 实时位姿")
        if record.get("arm_view") not in {"left", "right"}:
            raise ValueError(f"观察点 {name} 的 arm_view 必须是 left 或 right")
        require_photo(record, f"观察点 {name}")

    viewpoints = payload.get("viewpoints")
    if not isinstance(viewpoints, dict) or set(viewpoints) != set(VIEWPOINT_NAMES):
        raise ValueError("9.18 独立 setup 缺少 home_safe/left/right 机械臂姿态")
    normalized_viewpoints: dict[str, dict[str, Any]] = {}
    for name in VIEWPOINT_NAMES:
        record = dict(viewpoints[name])
        record["joint"] = _finite_joint(record, f"姿态 {name}")
        normalized_viewpoints[name] = record

    maximum_offset = float(route.get("max_observation_offset_m", DEFAULT_MAX_OBSERVATION_OFFSET_M))
    _validate_geometry(anchors, order, mode, observations, maximum_offset)
    normalized_anchors = {name: _finite_pose(anchors[name], f"路线锚点 {name}") for name in order}
    normalized_observations = {
        name: {
            "pose": _finite_pose(record, f"观察点 {name}"),
            "arm_view": str(record["arm_view"]),
            "raw": record,
        }
        for name, record in observations.items()
    }
    return {
        "source": str(source),
        "map_path": str(map_path),
        "route_mode": mode,
        "route_order": list(order),
        "anchors": normalized_anchors,
        "observations": normalized_observations,
        "viewpoints": normalized_viewpoints,
        "max_observation_offset_m": maximum_offset,
        "raw": payload,
    }


__all__ = [
    "DEFAULT_MAX_OBSERVATION_OFFSET_M",
    "DRAFT_KIND",
    "ROUTE_MODES",
    "SETUP_KIND",
    "VIEWPOINT_NAMES",
    "Setup918Session",
    "load_918_setup",
    "validate_918_map",
]
