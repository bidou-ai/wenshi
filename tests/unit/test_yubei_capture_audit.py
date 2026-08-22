import json
from pathlib import Path

import cv2
import numpy as np

from yubei.capture_audit import audit_session


def test_audit_session_reports_quality_and_duplicate_counts(tmp_path: Path):
    root = tmp_path / "dataset"
    (root / "images").mkdir(parents=True)
    image = np.zeros((80, 100, 3), dtype=np.uint8)
    cv2.rectangle(image, (10, 10), (90, 70), (255, 255, 255), 2)
    cv2.imwrite(str(root / "images" / "000001.jpg"), image)
    cv2.imwrite(str(root / "images" / "000002.jpg"), image)
    (root / "manifest.json").write_text(
        json.dumps({"images": [
            {"filename": "images/000001.jpg", "capture_tag": "flower"},
            {"filename": "images/000002.jpg", "capture_tag": "flower"},
        ]}),
        encoding="utf-8",
    )

    report = audit_session(root)

    assert report["images"] == 2
    assert report["tags"]["flower"] == 2
    assert report["duplicates"] >= 1
    assert (root / "capture_audit.json").is_file()
