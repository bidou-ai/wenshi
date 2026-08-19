"""Geometry helpers shared by the rice testing nodes."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np


def quaternion_to_matrix(tx: float, ty: float, tz: float,
                         qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    """Convert translation and quaternion to a 4x4 homogeneous matrix."""
    matrix = np.eye(4, dtype=np.float64)
    matrix[0, 0] = 1 - 2 * qy * qy - 2 * qz * qz
    matrix[0, 1] = 2 * qx * qy - 2 * qz * qw
    matrix[0, 2] = 2 * qx * qz + 2 * qy * qw
    matrix[1, 0] = 2 * qx * qy + 2 * qz * qw
    matrix[1, 1] = 1 - 2 * qx * qx - 2 * qz * qz
    matrix[1, 2] = 2 * qy * qz - 2 * qx * qw
    matrix[2, 0] = 2 * qx * qz - 2 * qy * qw
    matrix[2, 1] = 2 * qy * qz + 2 * qx * qw
    matrix[2, 2] = 1 - 2 * qx * qx - 2 * qy * qy
    matrix[0, 3] = tx
    matrix[1, 3] = ty
    matrix[2, 3] = tz
    return matrix


def transform_msg_to_matrix(transform_msg) -> np.ndarray:
    """Convert a geometry_msgs TransformStamped to a 4x4 matrix."""
    translation = transform_msg.transform.translation
    rotation = transform_msg.transform.rotation
    return quaternion_to_matrix(
        translation.x,
        translation.y,
        translation.z,
        rotation.x,
        rotation.y,
        rotation.z,
        rotation.w,
    )


def matrix_from_flat(values: Iterable[float]) -> np.ndarray:
    """Load a 4x4 matrix from a flat list of 16 numbers."""
    flat = [float(value) for value in values]
    if len(flat) != 16:
        raise ValueError(f"Expected 16 values for a 4x4 matrix, got {len(flat)}")
    return np.array(flat, dtype=np.float64).reshape(4, 4)


def pixel_to_camera(px: float, py: float, depth_m: float, camera_k: np.ndarray) -> np.ndarray:
    """Project one RGB-D pixel into the camera optical frame."""
    fx = float(camera_k[0, 0])
    fy = float(camera_k[1, 1])
    cx = float(camera_k[0, 2])
    cy = float(camera_k[1, 2])
    x = (float(px) - cx) / fx * depth_m
    y = (float(py) - cy) / fy * depth_m
    return np.array([x, y, depth_m, 1.0], dtype=np.float64)


def clamp_vector(vector: np.ndarray, lower: Iterable[float], upper: Iterable[float]) -> np.ndarray:
    """Clamp a 3D vector component-wise."""
    low = np.array(list(lower), dtype=np.float64)
    high = np.array(list(upper), dtype=np.float64)
    return np.minimum(np.maximum(vector, low), high)


def angular_error_deg(current: float, target: float) -> float:
    """Shortest absolute distance between two Euler angles in degrees."""
    return abs((float(current) - float(target) + 180.0) % 360.0 - 180.0)


def vector_norm(values: Iterable[float]) -> float:
    """Return Euclidean norm as a plain float."""
    return float(math.sqrt(sum(float(value) * float(value) for value in values)))
