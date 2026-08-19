# 模型目录

当前项目没有经过验证的水稻识别模型。将来放入的 ONNX 模型不纳入 Git，必须在
`docs/VISION_STATUS.md` 登记来源、类别、验证日期和阈值后，才允许将 `vision.enabled`
改为 `true`。
# Formal model directory

`rice_demo.pt` is published here only by `python3 yubei/publish_model.py` after
offline dataset validation and training. Its sidecar `rice_demo.json` records
the class map and SHA256. A previous model is copied to `models/archive/`
before replacement.

The first model uses `rice=0` and keeps `flower=1` reserved for later data.
