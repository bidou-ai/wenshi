# 架构

Wenshi 是 Wens1 温室巡检的唯一正式项目。路线固定为 `LM1 -> LM4 -> LM3 -> LM2`，
地图、路线顺序和示教点全部从 `config/wenshi.yaml` 解析。

| 层 | 模块 | 职责 |
|---|---|---|
| 硬件 | `agv.py`、`jaka.py` | 保持既有 TCP 协议，不上电、不使能、不清报警 |
| D435 | `camera_bridge.py`、`windows/` | Windows HTTP 帧服务与 Ubuntu ROS2 桥 |
| 机械臂 | `arm_controller.py`、`fixed_approach.py` | J5 扫视、示教回撤和固定抵近 |
| 路线 | `route_controller.py`、`control/route_math.py`、`route_policy.py` | 站点段、纠偏、限速和固定路线约束 |
| 编排 | `patrol_controller.py` | 唯一中文控制台、AGV/JAKA/D435 安全互锁和目标任务状态机 |
| 目标任务 | `target_task.py`、`patrol_target_runtime.py`、`near_capture.py` | 初筛选株、同一巡检去重、目标跟随、低速抵近、远近景质量检查 |
| 视觉 | `vision/` | 纯检测、稳定性、质量、去重和运行记录；模型结果通过编排层才可能影响运动 |
| 后台 | `dashboard/` | 本地/LAN 只读结果查看、媒体放大、管理员软删除和去重重置；不持有硬件对象 |

只有 `patrol_controller.py` 可以同时持有 AGV 与机械臂对象。视觉包和后台不得导入或调用任何
运动客户端。正式巡检每次生成一个 `runtime/runs/run_<timestamp>/`，每株仅保存 `far.jpg`、
`near.jpg` 和 `metadata.json`，事件与硬件日志同样落在该运行目录。
