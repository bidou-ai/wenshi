import json
from pathlib import Path

import numpy as np


def test_store_writes_rgbd_evidence_tree(tmp_path):
    from wenshi_patrol.height_test.capture import DetectionBundle
    from wenshi_patrol.height_test.models import FramePacket, MethodResult, ViewAnalysis
    from wenshi_patrol.height_test.storage import HeightTestStore
    from wenshi_patrol.vision.detector import Detection
    store = HeightTestStore.create(tmp_path, {"active": 24})
    packet = FramePacket(np.zeros((8, 8, 3), np.uint8), np.ones((8, 8), np.uint16), 7, {"fx": 1, "fy": 1})
    bundle = DetectionBundle("plant.pt", "panicle.pt", (Detection(4, 4, 2, 2, .9),), (Detection(4, 3, 2, 2, .8),), packet.color)
    analysis = ViewAnalysis({"tag_panicle_3d": MethodResult("tag_panicle_3d", .9)})
    store.save_view("left-01", "A-01", "center", packet, bundle, analysis)
    assert (store.path / "plants/A-01/views/center/depth.png").is_file()
    assert json.loads((store.path / "plants/A-01/views/center/frame.json").read_text())["seq"] == 7
