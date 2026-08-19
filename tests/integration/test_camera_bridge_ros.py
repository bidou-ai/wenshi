import unittest

try:
    from builtin_interfaces.msg import Time

    from wenshi_patrol.camera_bridge import CameraBridgeNode
except ModuleNotFoundError:
    Time = None
    CameraBridgeNode = None


@unittest.skipUnless(CameraBridgeNode is not None, "ROS2 Python environment is not loaded")
class CameraBridgeRosTest(unittest.TestCase):
    def test_camera_info_matrix_lengths(self):
        node = object.__new__(CameraBridgeNode)
        node._frame_id = "camera_color_optical_frame"
        message = node._camera_info(
            {"width": 640, "height": 480, "fx": 600.0, "fy": 601.0, "cx": 320.0, "cy": 240.0},
            Time(),
        )
        self.assertEqual(len(message.k), 9)
        self.assertEqual(len(message.r), 9)
        self.assertEqual(len(message.p), 12)

