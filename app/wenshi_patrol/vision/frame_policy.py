"""采集前的图像新鲜度检查。"""

from __future__ import annotations


class FrameStaleError(RuntimeError):
    """缓存图像已超过允许采集时间。"""


def require_current_color_frame(age_s: float | None, maximum_age_s: float):
    if age_s is None or age_s > float(maximum_age_s):
        rendered = "None" if age_s is None else f"{age_s:.2f}s"
        raise FrameStaleError(
            f"D435 彩色图像已过期: age={rendered}，限制={float(maximum_age_s):.2f}s"
        )

