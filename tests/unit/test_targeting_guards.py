import cv2
import numpy as np

from wenshi_patrol.vision.detector import Detection
from wenshi_patrol.vision import targeting


def test_depth_guard_rejects_zero_depth_inside_target_box():
    depth = np.zeros((40, 40), dtype=np.uint16)
    detection = Detection(20, 20, 18, 18, 0.95, class_name="rice")

    assert hasattr(targeting, "depth_valid_for_detection")
    assert targeting.depth_valid_for_detection(depth, detection) is False


def test_depth_guard_accepts_current_finite_depth_inside_target_box():
    depth = np.zeros((40, 40), dtype=np.uint16)
    depth[14:27, 14:27] = 1200
    detection = Detection(20, 20, 18, 18, 0.95, class_name="rice")

    assert hasattr(targeting, "depth_valid_for_detection")
    assert targeting.depth_valid_for_detection(depth, detection) is True


def test_depth_guard_maps_color_bbox_to_smaller_depth_image():
    depth = np.zeros((480, 640), dtype=np.uint16)
    depth[350:430, 500:620] = 1200
    detection = Detection(1120, 600, 180, 160, 0.95, class_name="rice")

    assert targeting.depth_valid_for_detection(
        depth, detection, source_size=(1280, 720)
    ) is True


def test_locked_target_can_cross_image_center_during_j5_follow():
    previous = Detection(610, 300, 120, 240, 0.95, class_name="rice")
    followed = Detection(650, 302, 122, 238, 0.93, class_name="rice")
    other = Detection(450, 300, 120, 240, 0.96, class_name="rice")

    assert targeting.match_locked_detection([other, followed], previous) is followed


def test_locked_target_rejects_distant_neighbor_after_target_disappears():
    previous = Detection(300, 300, 100, 220, 0.95, class_name="rice")
    neighbor = Detection(520, 300, 100, 220, 0.99, class_name="rice")

    assert targeting.match_locked_detection([neighbor], previous) is None


def test_prepare_preserves_nested_image_label_paths(tmp_path):
    from yubei.dataset_validate import prepare_yolo_dataset

    root = tmp_path / "session"
    (root / "images" / "left").mkdir(parents=True)
    (root / "images" / "right").mkdir(parents=True)
    (root / "labels").mkdir()
    for relative in ("left/frame.jpg", "right/frame.jpg"):
        image = np.zeros((20, 30, 3), dtype=np.uint8)
        cv2.imwrite(str(root / "images" / relative), image)
        stem = relative.removesuffix(".jpg")
        (root / "labels" / f"{stem}.txt").parent.mkdir(parents=True, exist_ok=True)
        (root / "labels" / f"{stem}.txt").write_text("0 0.5 0.5 0.5 0.5\n", encoding="utf-8")
        status = "ambiguous" if relative.startswith("right/") else "labelled"
        (root / "labels" / f"{stem}.json").write_text(
            '{"status":' + repr(status).replace("'", '"') + '}', encoding="utf-8"
        )

    output = tmp_path / "prepared"
    prepare_yolo_dataset(root, output, val_ratio=0.0, seed=17)

    assert (output / "train" / "labels" / "left" / "frame.txt").is_file()
    assert not (output / "train" / "labels" / "right" / "frame.txt").exists()
