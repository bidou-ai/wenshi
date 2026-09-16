# 现场资源

这次一键株高测试不依赖训练集图片，也不要求把现场照片放到 `yubei/`。现场准备以下实物：

1. 24 株主动检测株和 8 株 C 排登记株各自的 `tag25h7` 标签。标签黑色外框实测边长按 90 mm 记录；中间约 60 mm 的编码区不能当作 `tag_size_m`。
2. 一把直尺或卷尺，用于逐株测量卡槽顶到水面的垂直距离。
3. 一个有明确 0.5 m、1.0 m、1.5 m 距离的刚性基准目标，用来做深度和高度可行性检查。打印材料必须检查打印缩放，优先直接用卷尺在现场标出距离。

## 图片和证据放置

- 不修改 `yubei/data`、`yubei/datasets` 或历史 `runtime/field_tests`。
- setup 阶段的 Tag 照片放在 `runtime/height_tests/setup_<timestamp>/tags/<plant_id>.jpg`。
- 基准目标照片放在 `runtime/height_tests/run_<timestamp>/reference/`，建议文件名包含距离，例如 `target_0.5m.jpg`。
- 程序自动保存的逐株图像位于 `runtime/height_tests/run_<timestamp>/plants/<plant_id>/views/<left|center|right>/`。

项目内不放一张容易被打印机缩放的“标准尺寸图片”来替代实测。若现场需要打印纸质标定板，请在照片和记录中写明纸张实际测量尺寸、打印缩放比例和测量日期。
