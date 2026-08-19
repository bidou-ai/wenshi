# 迁移清单

| 目标 | 来源 | 状态 | 保留理由 | 验证 |
|---|---|---|---|---|
| `map/wenshi.smap` | `rice_patrol_wens1/map/wens1.smap` | 已复制并改名 | Wens1 当前地图 | 地图、路线测试 |
| `config/viewpoints.json` | `rice_patrol_wens1/config/viewpoints.json` | 已复制 | 已有示教关节姿态 | 固定路径测试 |
| `config/wenshi.yaml` | `rice_patrol_wens1/config/wens1.yaml` | 已整理 | 统一正式配置，倒车/视觉/抵近默认关闭 | 配置测试 |
| `agv.py`、`protocol.py` | `rice_patrol` 基础层 | 已复制 | AGV 帧协议、状态和运动看门狗 | 协议与离线测试 |
| `jaka.py`、`arm_controller.py` | `rice_patrol` JAKA/ArmSweepWorker | 已修改 | 保留不改变电源状态的 TCP 客户端和示教回撤 | 机械臂假客户端测试 |
| `fixed_approach.py` | `rice_patrol/fixed_demo.py` | 已修改 | 保留示教通道；正式配置默认禁用固定抵近 | 回撤和启用开关测试 |
| `control/route_math.py` | `rice_patrol_wens1/route_math.py` | 已复制 | Wens1 多站点速度与到站判断 | 路线数学与地图测试 |
| `route_controller.py` | 新建装配层 | 已创建 | 让正式路线成为独立、可测试的控制边界 | 路线装配测试 |
| `patrol_controller.py` | `rice_patrol_wens1/patrol_console.py` | 已修改 | 作为唯一控制台；去除旧包路径和危险日志删除 | 编译与离线策略测试 |
| `camera_bridge.py` | `rice_patrol/camera_bridge.py` | 已修改 | D435 HTTP 到 ROS2；改用 WENSHI 运行目录 | ROS2 环境测试 |
| `vision/detector.py`、`geometry.py` | `rice_testing` 视觉基础 | 已修改 | 仅检测/几何，无旧工程搜索和运动权限 | 视觉边界与存储测试 |
| `vision/hand_eye.py` | `rice_testing` 几何接口 | 已创建 | 仅加载标定矩阵，不触发运动 | 标定矩阵测试 |
| `windows/windows_realsense_server.py` | `windows_realsense_server2.py` | 已修改 | 保留 profile fallback；启动失败仍提供 `/health` | Windows 现场 HTTP 检查 |
| `scripts/`、`docs/` | 新建 | 已创建 | 唯一入口、预检和中文交接 | shell 语法和交付文件测试 |
