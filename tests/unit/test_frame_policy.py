import pytest

from wenshi_patrol.vision.frame_policy import FrameStaleError, require_current_color_frame


def test_current_color_frame_is_accepted():
    require_current_color_frame(0.2, 1.0)


def test_stale_color_frame_is_rejected_before_capture():
    with pytest.raises(FrameStaleError, match="过期"):
        require_current_color_frame(1.01, 1.0)

