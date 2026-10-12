# 模型目录

这里存放经过明确类型发布的模型。当前项目约定模型权重、对应 sidecar 和训练证据均纳入 GitHub；提交前必须运行 `python3 scripts/check_github_upload.py`，确认没有超过 GitHub 单文件限制，也没有疑似密钥。

| 文件 | 类别 | 状态 |
| --- | --- | --- |
| `rice_plant.pt` | `rice_plant` | 当前尚未正式发布 |
| `panicle.pt` | `panicle` | 当前尚未正式发布 |

发布前必须完成离线数据检查、训练结果复核和独立现场验收。发布命令必须显式指定
`--model-type plant` 或 `--model-type panicle`，sidecar JSON 会记录类别、来源和 SHA256。
旧 `rice_demo.pt` 双类别模型仅属于兼容历史接口，不属于当前两个模型流程。
