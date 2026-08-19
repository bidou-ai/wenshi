"""YOLO wrapper for rice white marker detection."""

from __future__ import annotations

import os
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Detection:
    """One model detection in image coordinates."""

    cx: float
    cy: float
    width: float
    height: float
    confidence: float
    class_id: int | None = None
    class_name: str = "target"

    @property
    def area(self) -> float:
        return float(self.width) * float(self.height)

    def pixel_at(self, x_ratio: float, y_ratio: float) -> tuple[float, float]:
        x1 = float(self.cx) - float(self.width) / 2.0
        y1 = float(self.cy) - float(self.height) / 2.0
        return (
            x1 + float(self.width) * max(0.0, min(float(x_ratio), 1.0)),
            y1 + float(self.height) * max(0.0, min(float(y_ratio), 1.0)),
        )

    def to_dict(self) -> dict:
        return {
            "cx": self.cx,
            "cy": self.cy,
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence,
            "class_id": self.class_id,
            "class_name": self.class_name,
            "area": self.area,
        }


class RiceMarkerDetector:
    """Load and run a YOLO model for white rice marker candidates."""

    def __init__(self, model_path: str = "", conf_threshold: float = 0.35,
                 target_class_names: list[str] | None = None,
                 allow_missing_model: bool = True):
        self.model_path = model_path
        self.conf_threshold = float(conf_threshold)
        self.target_class_names = {
            name.strip().lower()
            for name in (target_class_names or [])
            if name and name.strip()
        }
        self.allow_missing_model = bool(allow_missing_model)
        self.model = None
        self.names = {}

    def load_model(self) -> bool:
        if not self.model_path:
            message = "[Detector] model_path is empty"
            if self.allow_missing_model:
                print(message + "; detector will return no results")
                return True
            print(message)
            return False

        if not os.path.exists(self.model_path):
            message = f"[Detector] model file does not exist: {self.model_path}"
            if self.allow_missing_model:
                print(message + "; detector will return no results")
                return True
            print(message)
            return False

        try:
            from ultralytics import YOLO

            self.model = YOLO(self.model_path)
            self.names = getattr(self.model, "names", {}) or {}
            print(f"[Detector] model loaded: {self.model_path}")
            return True
        except Exception as exc:
            print(f"[Detector] failed to load model: {exc}")
            return False

    def detect(self, image: np.ndarray) -> list[Detection]:
        if self.model is None:
            return []

        results = self.model(image, conf=self.conf_threshold, verbose=False)
        detections: list[Detection] = []

        for result in results:
            names = getattr(result, "names", None) or self.names
            for box in result.boxes:
                x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
                confidence = float(box.conf[0])
                class_id = int(box.cls[0]) if getattr(box, "cls", None) is not None else None
                class_name = str(names.get(class_id, class_id if class_id is not None else "target"))

                if self.target_class_names and class_name.lower() not in self.target_class_names:
                    continue

                detections.append(Detection(
                    cx=(x1 + x2) / 2.0,
                    cy=(y1 + y2) / 2.0,
                    width=x2 - x1,
                    height=y2 - y1,
                    confidence=confidence,
                    class_id=class_id,
                    class_name=class_name,
                ))

        detections.sort(key=lambda item: item.confidence, reverse=True)
        return detections

    def draw(self, image: np.ndarray, detections: list[Detection],
             selected: Detection | None = None) -> np.ndarray:
        for detection in detections:
            color = (0, 255, 0) if detection is selected else (80, 180, 255)
            x1 = int(round(detection.cx - detection.width / 2.0))
            y1 = int(round(detection.cy - detection.height / 2.0))
            x2 = int(round(detection.cx + detection.width / 2.0))
            y2 = int(round(detection.cy + detection.height / 2.0))
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            cv2.circle(image, (int(round(detection.cx)), int(round(detection.cy))), 4, color, -1)
            label = f"{detection.class_name} {detection.confidence:.0%}"
            cv2.putText(
                image,
                label,
                (x1, max(18, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )
        return image
