# 模型目录

这里存放经过明确类型发布的模型，不纳入 Git 的大文件只保留在本机。

| 文件 | 类别 | 状态 |
| --- | --- | --- |
| `rice_plant.pt` | `rice_plant` | 当前尚未正式发布 |
| `panicle.pt` | `panicle` | 当前尚未正式发布 |

发布前必须完成离线数据检查、训练结果复核和独立现场验收。发布命令必须显式指定
`--model-type plant` 或 `--model-type panicle`，sidecar JSON 会记录类别、来源和 SHA256。
旧 `rice_demo.pt` 双类别模型仅属于兼容历史接口，不属于当前两个模型流程。
