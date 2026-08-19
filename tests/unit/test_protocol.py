import unittest

from wenshi_patrol.protocol import encode_frame, parse_frames


class ProtocolTest(unittest.TestCase):
    def test_round_trip(self):
        buffer = bytearray(encode_frame(0x07DA, {"vx": 0.1, "vy": 0.0, "w": 0.02}))
        frames = parse_frames(buffer)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], 0x07DA)
        self.assertEqual(frames[0][1]["vx"], 0.1)
        self.assertEqual(buffer, bytearray())

    def test_partial_frame(self):
        encoded = encode_frame(0x2454, {"interval": 100})
        buffer = bytearray(encoded[:10])
        self.assertEqual(parse_frames(buffer), [])
        buffer.extend(encoded[10:])
        self.assertEqual(parse_frames(buffer)[0][0], 0x2454)

    def test_multiple_frames_and_noise(self):
        buffer = bytearray(b"noise" + encode_frame(1) + encode_frame(2, {"ok": True}))
        frames = parse_frames(buffer)
        self.assertEqual([item[0] for item in frames], [1, 2])
        self.assertIsNone(frames[0][1])
        self.assertTrue(frames[1][1]["ok"])

