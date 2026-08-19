# Wenshi 温室巡检操作手册

## 1. 项目边界

正式项目目录是 `wenshi/`，旧工程 `jakarobot/` 只作为参考。`wenshi/yubei/` 是独立的预备工具，后续可以删除；它不被正式巡检导入。

正式路线为：

```text
LM1 -> LM4 -> LM3 -> LM2 -> LM1 -> ...
```

正常路线只向前行驶。检测到水稻后进入受控目标任务，允许底盘低速后退对位；普通路线和普通手动测试不开放倒车。后退时 J5 实时跟随目标，完成后执行锁定侧的机械臂示教路径并恢复原方向。

## 2. 控制框架

- `agv.py`：AGV 状态端口19204和连续运动端口19205。
- `jaka.py`：只负责 JAKA TCP 连接、关节读取、停止和已验证的关节移动。
- `arm_controller.py`：J5巡视、固定左/右抵近、回撤和 home 通道；同一时刻只有一个机械臂动作所有者。
- `camera_bridge.py`：从 Windows D435 HTTP 服务获取 RGB、对齐深度和相机内参，发布 ROS2 话题。
- `vision/`：YOLO检测、5帧中3帧稳定目标、深度统计、照片质量、去重和运行目录。
- `patrol_controller.py`：路线、状态机、AGV/JAKA/视觉任务互锁和异常恢复的唯一协调者。
- `dashboard/`：只读结果后台，不拥有任何运动权限。

## 3. 目录与日志

一次正式巡检只创建一个运行目录：

```text
wenshi/runtime/runs/run_<timestamp>/
  run.json                 # 运行状态和开始/结束时间
  events.jsonl             # 状态、检测、抵近、失败、管理员事件
  demo.log 或 system.log   # 主程序日志（旧兼容日志可能叫 demo.log）
  camera.log               # D435 HTTP/桥接日志
  agv.csv                  # AGV状态和速度命令采样
  jaka.csv                 # JAKA关节状态采样
  targets/T0001/
    far.jpg                # 远景唯一原图
    near.jpg               # 近景最佳原图
    metadata.json          # bbox、深度、质量、路线段、侧别、失败原因
```

数据集采集不写入这里，而写入 `wenshi/yubei/data/dataset_<timestamp>/`。训练输出写入 `wenshi/yubei/training/<timestamp>/`。发布模型在 `wenshi/models/rice_demo.pt`，旧模型在 `wenshi/models/archive/`。

## 4. 网络与相机

AGV、JAKA、Windows D435电脑都通过温室内部路由器的有线网络连接。工作站使用同一路由器时，必须保证同一子网、关闭客户端隔离、固定/保留 IP；当前默认地址为 AGV `192.168.192.5`、JAKA `192.168.192.160`、相机 `192.168.192.203:18080`。

检查命令：

```bash
python3 yubei/network_check.py
python3 yubei/camera_check.py --url http://192.168.192.203:18080 --samples 10
```

Windows D435服务需在相机电脑上人工启动；Ubuntu只检查它是否在线，不远程启动。工作站若需同时上网，可以用第二张网卡；机器人网卡不要设置错误的默认网关。

## 5. yubei 数据集采集和标注

```bash
python3 yubei/dataset_capture.py --output yubei/data --preview
```

实时窗口只显示 RGB。输入回车保存一张 JPG；中间可以人工移动机械臂；输入 `q` 结束。每个 bbox 是一个完整水稻植株，一个图片允许多个 bbox。轻微交叠分别标注，严重交叠选择歧义，不合成一个框。

启动标注网页：

```bash
python3 yubei/label_server.py --session yubei/data/dataset_<timestamp>
```

网页支持画框、移动/删除、撤销/重做、复制上一张框、缩放、rice/flower、歧义跳过和 YOLO TXT 导出。初期只标 rice。

## 6. 检查、训练和发布

```bash
python3 yubei/dataset_validate.py yubei/data/dataset_<timestamp>
python3 yubei/train_yolo.py --data yubei/yolo_data.yaml --device cpu --epochs 100
python3 yubei/publish_model.py yubei/training/<timestamp>/weights/best.pt --models models
```

训练不会自动覆盖正式模型；发布会计算 SHA256 并备份旧模型。当前 Ubuntu 在 VMware 中，显卡方案见 `liuyi666.md`。

## 7. 示教准备

完整示教点为：

```text
home_safe, camera, camera_left, camera_right,
left_pre, left_photo, right_pre, right_photo
```

读取/保存示教点：

```bash
python3 yubei/teach_viewpoints.py --output yubei/viewpoints_staged.json --name camera --save
python3 yubei/viewpoint_verify.py yubei/viewpoints_staged.json
```

工具不会自动上电或使能。验证通过后，显式发布才覆盖正式 `config/viewpoints.json`，旧文件会备份到 `yubei/backups/`。如果没有现场保存的 `home_safe`，不得伪造或启用固定抵近。

## 8. 正式巡检

启动前：

```bash
./scripts/check_environment.sh
./scripts/check_hardware_links.sh
./scripts/start_wenshi.sh
```

控制台命令包括 `start`（一圈）、`start loop`（循环）、`status`、`stop`、`goto home`、`collect`、`detect`、`test arm`、固定抵近测试和 `q`。初步 Demo 启用视觉和固定抵近前，必须完成模型、八点示教、相机和人工看护验证。

目标流程：检测稳定3/5帧 -> 选画面中央 rice -> 锁定左/右 -> 远景一张 -> 目标任务低速后退、J5跟随 -> 重新定位 -> 固定示教靠近 -> 近拍最多3轮，每轮5张，质量不合格自动重拍，仅保存最佳合格一张 -> 回撤 -> 继续原方向。

本圈目标周围30cm暂缓，下一圈重新处理；选中目标2小时去重。新 `run_id` 重新开始去重。

## 9. 后台

默认本机访问：

```bash
python3 dashboard/server.py --root runtime/runs --host 127.0.0.1 --port 8088
```

温室局域网访问时显式绑定工作站地址或 `0.0.0.0`，不要映射到公网。后台展示当前/历史巡检、目标计数、路线段、侧别、远景/近景、质量和失败原因，不提供运动按钮。

## 10. 故障恢复

- 相机断流：立即停止相关运动，查看 `camera.log` 和 Windows服务 `/health`，恢复后由人工决定是否结束本次运行。
- 目标丢失：抵近后退立即停底盘，目标任务通过已知回撤/巡视路径回到巡视姿态，并沿正方向继续；失败原因写入目标 metadata。
- AGV阻挡/急停：AGV和JAKA都停，排除现场原因后重新执行前置检查。
- JAKA错误或路径不在回撤通道：不得强行发送下一点，保存日志并人工回到安全姿态。
- 程序异常退出：下一次启动创建新 `run_id`，旧目录保留为异常中断记录，不自动续接旧去重。

## 11. 管理员删除和清理

后台管理员 PIN 只能执行软删除和去重重置。永久清理必须先预览：

```bash
python3 dashboard/cleanup.py --root runtime/runs --list
python3 dashboard/cleanup.py --root runtime/runs --preview run_<timestamp>
python3 dashboard/cleanup.py --root runtime/runs --execute run_<timestamp> --confirm run_<timestamp>
```

清理工具拒绝正在运行的目录、路径穿越和不完整确认。不要对 `runtime/` 根目录使用递归删除。
