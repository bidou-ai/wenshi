import json

import numpy as np

from wenshi_patrol.vision.hand_eye import load_transform


def test_hand_eye_loader_accepts_homogeneous_matrix(tmp_path):
    path = tmp_path / "hand_eye.json"
    matrix = np.eye(4).reshape(-1).tolist()
    path.write_text(json.dumps({"matrix": matrix}), encoding="utf-8")
    assert np.array_equal(load_transform(path), np.eye(4))

