# 架构

Wenshi 是 Wens1 温室巡检的唯一正式项目。路线固定为 `LM1 -> LM4 -> LM3 -> LM2`，
地图、路线顺序和示教点全部从 `config/wenshi.yaml` 解析。

| 层 | 模块 | 职责 |
|---|---|---|
| 硬件 | `agv.py`、`jaka.py` | 保持既有 TCP 协议，不上电、不使能、不清报警 |
| D435 | `camera_bridge.py`、`windows/` | Windows HTTP 帧服务与 Ubuntu ROS2 桥 |
| 机械臂 | `arm_controller.py`、`fixed_approach.py` | J5 扫视、示教回撤和固定抵近 |
| 路线 | `route_controller.py`、`control/route_math.py`、`route_policy.py` | 站点段、纠偏、限速和固定路线约束 |
| 编排 | `patrol_controller.py` | 唯一中文控制台、AGV/JAKA/D435 安全互锁 |
| 视觉 | `vision/` | 纯检测、图像记录和后续 RGB-D/标定接口，无运动权限 |

只有 `patrol_controller.py` 可以同时持有 AGV 与机械臂对象。视觉包不得导入或调用任何
运动客户端。所有运行产物进入 `runtime/run_YYYYMMDD_HHMMSS`。
