"""Independent setup evidence and schema for the fixed-base arm demo."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

import cv2
import numpy as np


SETUP_KIND = "wenshi_arm_panicle_demo"
DRAFT_KIND = "wenshi_arm_panicle_demo_draft"
DRAFT_FILENAME = "setup_draft.json"
POSE_NAMES: tuple[str, ...] = (
    "home_safe",
    "left_clearance",
    "rice1_pre",
    "rice1_center",
    "rice1_scan_a",
    "rice1_scan_b",
    "rice2_pre",
    "rice2_center",
    "rice2_scan_a",
    "rice2_scan_b",
)
MAX_SAMPLE_SPREAD_DEG = 0.20
MAX_SCAN_TCP_OFFSET_MM = 200.0
MAX_SCAN_JOINT_OFFSET_DEG = 30.0
JOINT_SAMPLE_MEAN_TOLERANCE_DEG = 1e-9


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inside(path: Path, root: Path, label: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    base = Path(root).expanduser().resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"{label} 不在 runtime/arm_demo 目录内: {candidate}") from exc
    return candidate


def _finite_vector(value: Any, length: int, label: str) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{label} 必须包含有限 {length} 轴数值")
    try:
        result = [float(item) for item in value]
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须包含有限 {length} 轴数值") from exc
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{label} 包含非有限值")
    return result


def _joint_samples(value: Any, label: str) -> list[list[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{label} 必须连续提供三次关节采样")
    samples = [_finite_vector(sample, 6, label) for sample in value]
    spread = max(
        max(sample[index] for sample in samples)
        - min(sample[index] for sample in samples)
        for index in range(6)
    )
    if spread > MAX_SAMPLE_SPREAD_DEG:
        raise ValueError(
            f"{label} 波动 {spread:.3f} 超过 {MAX_SAMPLE_SPREAD_DEG:.2f} deg"
        )
    return samples


def _joint_matches_samples(
    joint: Sequence[float], samples: Sequence[Sequence[float]], label: str
) -> list[float]:
    normalized_joint = _finite_vector(joint, 6, label)
    for index, value in enumerate(normalized_joint):
        average = sum(sample[index] for sample in samples) / len(samples)
        if not math.isclose(
            value,
            average,
            rel_tol=0.0,
            abs_tol=JOINT_SAMPLE_MEAN_TOLERANCE_DEG,
        ):
            raise ValueError(f"{label} 必须等于三次关节采样的算术平均")
    return normalized_joint


def _iso_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空 ISO 时间戳")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} 必须是可解析的 ISO 时间戳") from exc
    return value


def _readable_photo(path: Path, label: str) -> Path:
    if not path.is_file() or cv2.imread(str(path), cv2.IMREAD_COLOR) is None:
        raise ValueError(f"{label} 照片不存在或无法读取")
    return path


def _session_photo(
    photo_value: Any,
    name: str,
    evidence_root: Path,
    runtime_root: Path,
) -> Path:
    expected = evidence_root / "poses" / f"{name}.jpg"
    expected_relative = expected.relative_to(runtime_root).as_posix()
    if photo_value != expected_relative:
        raise ValueError(f"姿态 {name} 照片路径必须为 {expected_relative}")
    photo = _inside(runtime_root / photo_value, runtime_root, f"姿态 {name} 照片")
    expected_photo = _inside(expected, evidence_root, f"姿态 {name} 照片")
    if photo != expected_photo:
        raise ValueError(f"姿态 {name} 照片路径必须为 {expected_relative}")
    return _readable_photo(photo, f"姿态 {name}")


def _published_photo(
    photo_value: Any, name: str, runtime_root: Path
) -> Path:
    if not isinstance(photo_value, str):
        raise ValueError(f"姿态 {name} 缺少照片")
    parts = Path(photo_value).parts
    if (
        len(parts) != 3
        or parts[0] in ("", ".", "..")
        or photo_value != f"{parts[0]}/poses/{name}.jpg"
    ):
        raise ValueError(
            f"姿态 {name} 照片路径必须为 runtime/arm_demo/<setup>/poses/{name}.jpg"
        )
    evidence_root = runtime_root / parts[0]
    return _session_photo(photo_value, name, evidence_root, runtime_root)


def _canonical_without_integrity(payload: Mapping[str, Any]) -> bytes:
    value = {key: item for key, item in payload.items() if key != "sha256"}
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded.encode("utf-8")


def _sha256_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_without_integrity(payload)).hexdigest()


def _write_json_atomic(destination: Path, payload: Mapping[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


class ArmDemoSetupSession:
    """Collect 10 absolute arm poses without issuing any motion commands."""

    def __init__(self, root: Path, runtime_root: Path):
        self.runtime_root = Path(runtime_root).expanduser().resolve()
        self.root = _inside(Path(root), self.runtime_root, "setup 证据目录")
        self.root.mkdir(parents=True, exist_ok=True)
        self.operator = ""
        self.poses: dict[str, dict[str, Any]] = {}

    @property
    def draft_path(self) -> Path:
        return self.root / DRAFT_FILENAME

    def _draft_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": DRAFT_KIND,
            "updated_at": _now(),
            "operator": self.operator,
            "order": list(self.poses),
            "poses": {name: dict(record) for name, record in self.poses.items()},
        }

    def _checkpoint(self) -> None:
        _write_json_atomic(self.draft_path, self._draft_payload())

    def set_operator(self, operator: str) -> None:
        value = str(operator).strip()
        if not value:
            raise ValueError("操作者不能为空")
        self.operator = value
        self._checkpoint()

    def record_pose(
        self,
        name: str,
        joint_samples: Sequence[Sequence[float]],
        tcp: Sequence[float],
        photo: np.ndarray,
    ) -> None:
        expected = POSE_NAMES[len(self.poses)] if len(self.poses) < len(POSE_NAMES) else None
        if name not in POSE_NAMES:
            raise ValueError(f"未知姿态: {name}")
        if name in self.poses:
            raise ValueError(f"姿态重复: {name}")
        if name != expected:
            raise ValueError(f"姿态必须按顺序记录，下一姿态应为 {expected}")
        normalized_samples = _joint_samples(joint_samples, "关节采样")
        normalized_tcp = _finite_vector(tcp, 6, "TCP")
        if not isinstance(photo, np.ndarray) or photo.size == 0:
            raise ValueError("姿态照片不能为空")
        photo_path = self.root / "poses" / f"{name}.jpg"
        _inside(photo_path, self.root, "姿态照片")
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{photo_path.stem}.", suffix=".tmp.jpg", dir=photo_path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.close(descriptor)
            descriptor = -1
            if not cv2.imwrite(str(temporary), photo):
                raise OSError(f"无法写入姿态照片: {photo_path}")
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, photo_path)
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise
        joint = [sum(sample[index] for sample in normalized_samples) / 3.0 for index in range(6)]
        self.poses[name] = {
            "joint_samples": normalized_samples,
            "joint": joint,
            "tcp": normalized_tcp,
            "photo": photo_path.relative_to(self.runtime_root).as_posix(),
        }
        self._checkpoint()

    @classmethod
    def resume(cls, root: Path, runtime_root: Path) -> "ArmDemoSetupSession":
        session = cls(root, runtime_root)
        try:
            payload = json.loads(session.draft_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法恢复 setup 草稿: {session.draft_path}") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("kind") != DRAFT_KIND
            or payload.get("schema_version") != 1
        ):
            raise ValueError("setup 草稿 schema 无效")
        _iso_timestamp(payload.get("updated_at"), "setup 草稿更新时间")
        operator = payload.get("operator", "")
        poses = payload.get("poses")
        order = payload.get("order")
        if not isinstance(operator, str) or not operator.strip():
            raise ValueError("setup 草稿缺少操作者")
        if not isinstance(poses, dict) or order != list(poses):
            raise ValueError("setup 草稿记录无效")
        if list(poses) != list(POSE_NAMES[: len(poses)]) or len(poses) > len(POSE_NAMES):
            raise ValueError("setup 草稿姿态顺序无效")
        session.operator = operator
        session.poses = {}
        for name, raw_record in poses.items():
            if not isinstance(raw_record, dict):
                raise ValueError(f"姿态 {name} 记录无效")
            record = dict(raw_record)
            session.poses[name] = record
            record["joint_samples"] = _joint_samples(
                record.get("joint_samples"), f"姿态 {name} 关节采样"
            )
            record["joint"] = _joint_matches_samples(
                record.get("joint"), record["joint_samples"], f"姿态 {name} 关节"
            )
            record["tcp"] = _finite_vector(record.get("tcp"), 6, f"姿态 {name} TCP")
            _session_photo(record.get("photo"), name, session.root, session.runtime_root)
        return session

    def _validate(self) -> None:
        if not isinstance(self.operator, str) or not self.operator.strip():
            raise ValueError("缺少操作者")
        if list(self.poses) != list(POSE_NAMES):
            raise ValueError("必须完成完整 10 姿态集合")
        for name, record in self.poses.items():
            if not isinstance(record, dict):
                raise ValueError(f"姿态 {name} 记录无效")
            record["joint_samples"] = _joint_samples(
                record.get("joint_samples"), f"姿态 {name} 关节采样"
            )
            record["joint"] = _joint_matches_samples(
                record.get("joint"), record["joint_samples"], f"姿态 {name} 关节"
            )
            tcp = _finite_vector(record.get("tcp"), 6, f"姿态 {name} TCP")
            record["tcp"] = tcp
            _session_photo(record.get("photo"), name, self.root, self.runtime_root)
        for plant in (1, 2):
            center = self.poses[f"rice{plant}_center"]
            center_tcp = center["tcp"]
            center_joint = center["joint"]
            for suffix in ("scan_a", "scan_b"):
                name = f"rice{plant}_{suffix}"
                record = self.poses[name]
                distance = math.sqrt(
                    sum(
                        (record["tcp"][index] - center_tcp[index]) ** 2
                        for index in range(3)
                    )
                )
                if distance > MAX_SCAN_TCP_OFFSET_MM:
                    raise ValueError(
                        f"{name} TCP 距 center {distance:.1f} mm 超过 "
                        f"{MAX_SCAN_TCP_OFFSET_MM:.0f} mm"
                    )
                joint_distance = max(
                    abs(record["joint"][index] - center_joint[index])
                    for index in range(6)
                )
                if joint_distance > MAX_SCAN_JOINT_OFFSET_DEG:
                    raise ValueError(
                        f"{name} 关节距 center {joint_distance:.1f} deg 超过 "
                        f"{MAX_SCAN_JOINT_OFFSET_DEG:.0f} deg"
                    )

    def publish(self, destination: Path | None = None) -> Path:
        self._validate()
        requested_target = (
            self.runtime_root / "demo_setup.json"
            if destination is None
            else Path(destination).expanduser().absolute()
        )
        if requested_target.is_symlink():
            raise ValueError("setup 发布文件不能是符号链接")
        target = _inside(requested_target, self.runtime_root, "setup 发布文件")
        if target.parent != self.runtime_root:
            raise ValueError("setup 发布文件必须直接位于 runtime/arm_demo")
        payload: dict[str, Any] = {
            "schema_version": 1,
            "kind": SETUP_KIND,
            "published_at": _now(),
            "operator": self.operator,
            "order": list(POSE_NAMES),
            "poses": {name: dict(self.poses[name]) for name in POSE_NAMES},
        }
        payload["sha256"] = _sha256_payload(payload)
        _write_json_atomic(target, payload)
        return target


def load_arm_demo_setup(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 arm demo setup: {source}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("kind") != SETUP_KIND
        or payload.get("schema_version") != 1
    ):
        raise ValueError("不是有效的 arm demo setup")
    operator = payload.get("operator")
    if not isinstance(operator, str) or not operator.strip():
        raise ValueError("setup 缺少操作者")
    digest = payload.get("sha256")
    if not isinstance(digest, str) or digest != _sha256_payload(payload):
        raise ValueError("setup 发布文件完整性校验失败，可能已被篡改")
    _iso_timestamp(payload.get("published_at"), "setup 发布时间")
    poses = payload.get("poses")
    if (
        not isinstance(poses, dict)
        or payload.get("order") != list(POSE_NAMES)
        or list(poses) != list(POSE_NAMES)
    ):
        raise ValueError("setup 缺少完整或有序的 10 姿态集合")
    runtime_root = source.parent
    normalized: dict[str, dict[str, Any]] = {}
    evidence_directory: str | None = None
    for name in POSE_NAMES:
        record = poses[name]
        if not isinstance(record, dict):
            raise ValueError(f"姿态 {name} 记录无效")
        joint = _finite_vector(record.get("joint"), 6, f"姿态 {name} 关节")
        tcp = _finite_vector(record.get("tcp"), 6, f"姿态 {name} TCP")
        samples = record.get("joint_samples")
        if not isinstance(samples, list) or len(samples) != 3:
            raise ValueError(f"姿态 {name} 必须包含三次关节采样")
        normalized_samples = _joint_samples(samples, f"姿态 {name} 关节采样")
        joint = _joint_matches_samples(joint, normalized_samples, f"姿态 {name} 关节")
        photo_value = record.get("photo")
        _published_photo(photo_value, name, runtime_root)
        current_directory = Path(photo_value).parts[0]
        if evidence_directory is None:
            evidence_directory = current_directory
        elif current_directory != evidence_directory:
            raise ValueError("setup 的姿态照片必须来自同一证据目录")
        normalized[name] = {
            **record,
            "joint": joint,
            "joint_samples": normalized_samples,
            "tcp": tcp,
        }
    for plant in (1, 2):
        center = normalized[f"rice{plant}_center"]
        for suffix in ("scan_a", "scan_b"):
            name = f"rice{plant}_{suffix}"
            scan = normalized[name]
            tcp_distance = math.sqrt(
                sum(
                    (scan["tcp"][index] - center["tcp"][index]) ** 2
                    for index in range(3)
                )
            )
            joint_distance = max(
                abs(scan["joint"][index] - center["joint"][index])
                for index in range(6)
            )
            if tcp_distance > MAX_SCAN_TCP_OFFSET_MM:
                raise ValueError(
                    f"{name} TCP 距 center 超过 {MAX_SCAN_TCP_OFFSET_MM:.0f} mm"
                )
            if joint_distance > MAX_SCAN_JOINT_OFFSET_DEG:
                raise ValueError(
                    f"{name} 关节距 center 超过 {MAX_SCAN_JOINT_OFFSET_DEG:.0f} deg"
                )
    return {"poses": normalized, "raw": payload, "source": str(source)}


__all__ = ["DRAFT_KIND", "POSE_NAMES", "SETUP_KIND", "ArmDemoSetupSession", "load_arm_demo_setup"]
