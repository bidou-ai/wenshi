# 模型目录

当前项目没有经过验证的水稻识别模型。将来放入的 ONNX 模型不纳入 Git，必须在
正式视觉能力仍未纳入当前 32 株表型流程。历史模型、类别和验证资料见
`docs/归档/旧资料/视觉状态旧版.md`；没有新的现场验收证据前，不得将 `vision.enabled`
改为 `true`。
# Formal model directory

`rice_demo.pt` is published here only by `python3 yubei/publish_model.py` after
offline dataset validation and training. Its sidecar `rice_demo.json` records
the class map and SHA256. A previous model is copied to `models/archive/`
before replacement.

The first model uses `rice=0` and keeps `flower=1` reserved for later data.
