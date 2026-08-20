# Wenshi 温室巡检操作手册

> **先看结论**：机器人设备继续留在温室内部网络；工作站和 Ubuntu 另外使用 Wi-Fi 上网。
> Ubuntu 需要两条网络路径：一条到 AGV/JAKA/相机电脑，一条到公网。远程 SSH 只有在公网路由器把端口转发到 Ubuntu 后才成立；
> ToDesk 则用于远程查看图形界面。不要把 AGV、JAKA 或机器人相机电脑的端口发布到公网。

## 快速导航

| 需要做什么 | 看哪一节 |
|---|---|
| 理解机器人、工作站、Ubuntu 的连接关系 | 第 4 节 网络布局 |
| 从外部电脑 SSH 到 Ubuntu | 第 9 节 远程 SSH |
| 远程查看 Ubuntu 图形界面 | 第 9 节 ToDesk |
| 查看巡检照片和后台 | 第 10 节 后台 |
| 处理断网、断流或误配置 | 第 11 节 故障恢复 |

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

## 4. 网络布局

### 4.1 三个网络角色

| 设备/网络 | 作用 | 是否进入公网 |
|---|---|---:|
| AGV、JAKA、机器人上的 Windows 相机电脑 | 机器人控制和 D435 图像服务 | 否 |
| 温室内部路由器/内部有线网 | 连接机器人设备和工作站的机器人通信网 | 否 |
| 工作站 Wi-Fi、Ubuntu 的互联网网卡 | GitHub、软件更新、SSH、ToDesk、后续远程后台 | 是 |

机器人上的 AGV、JAKA 和 Windows 相机电脑不需要、也不应该连接公网。公网只给运行 Ubuntu 的工作站使用。

### 4.2 推荐拓扑

```text
                 温室内部网络（不进公网）
       ┌──────────┬──────────┬────────────────┐
       │          │          │                │
      AGV       JAKA   机器人 Windows      工作站有线网卡
                         相机电脑           （机器人网）
                                               │
                                       Ubuntu 网卡 A
                                               │
                                         Wenshi 程序

       工作站 Wi-Fi ──────────────── Ubuntu 网卡 B
                    （公网 / GitHub / SSH / ToDesk）
```

Ubuntu 的两条路径必须分工明确：

- 网卡 A 只访问 `192.168.192.0/24` 的机器人设备，不设置默认网关。
- 网卡 B 访问 Wi-Fi 和公网，承担默认路由。
- 如果工作站或 Ubuntu 只有 Wi-Fi、没有通往内部机器人网的第二条路径，AGV/JAKA 就无法通信。
- 机器人控制流量不经过公网 Wi-Fi；Ubuntu 访问 GitHub 等公网服务不影响机器人内网地址。
- Wi-Fi 的 SSID 和密码只控制谁能加入这个 Wi-Fi；它不会自动让外部电脑能够 SSH 到 Ubuntu。

当前地址约定仍为 AGV `192.168.192.5`、JAKA `192.168.192.160`、相机 `192.168.192.203:18080`，现场如有调整只修改配置和检查参数。

### 4.3 VMware 的两张虚拟网卡

Ubuntu 虚拟机需要两张网卡：

1. **机器人网卡**：桥接到工作站连接内部路由器的有线网卡，获得机器人网段地址。
2. **互联网网卡**：连接工作站 Wi-Fi，可使用 VMware NAT 或桥接 Wi-Fi；它提供 Ubuntu 的默认路由。

不要把两张网卡放在同一个网段，也不要让机器人网卡获得默认网关。VMware 的桥接模式把虚拟机接入物理 LAN，NAT 模式让虚拟机借用主机网络访问外部网络；两种模式可以分别用于两张网卡。[VMware 网络类型说明](https://knowledge.broadcom.com/external/article/309842/understanding-networking-types-in-hosted.html)

### 4.4 只做连通性检查

```bash
ip -4 -br address
ip route
ip route get 192.168.192.5
ip route get 192.168.192.160
ip route get 192.168.192.203
ip route get 1.1.1.1
python3 yubei/network_check.py
python3 yubei/camera_check.py --url http://192.168.192.203:18080 --samples 10
```

前三个机器人地址必须走网卡 A；`1.1.1.1` 必须走网卡 B。Windows D435 服务仍需在相机电脑上人工启动，Ubuntu 只检查它是否在线。

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

## 9. 远程 SSH 与 ToDesk

### 9.1 外部 SSH 的事实

Ubuntu 能访问公网，只代表它可以主动访问 GitHub、软件源等服务；这不会自动让外部电脑能够 SSH 进入 Ubuntu。

外部 SSH 必须存在一条入站路径：

```text
外部电脑 -> 公网路由器端口转发 -> Ubuntu SSH 服务
```

因此，普通方案需要在提供 Wi-Fi 的公网路由器上把一个外部端口转发到 Ubuntu 的 SSH 端口。若该 Wi-Fi 没有公网 IPv4、处于运营商 CGNAT，或路由器禁止端口转发，外部 SSH 就无法直接成立；这不是项目代码问题。

按当前要求可以使用普通 SSH 密码登录，但必须遵守以下最小边界：

- 只允许普通 Ubuntu 用户登录，禁止 root 直接登录。
- SSH 密码不能与 Wi-Fi、GitHub 或机器人设备密码相同。
- Ubuntu 防火墙只允许 SSH 端口，不允许 AGV `19204/19205`、JAKA `10001` 或相机 `18080` 对公网开放。
- SSH 登录只用于维护 Ubuntu、查看日志和检查进程，不用于绕过控制台安全状态。
- 外部连接前先确认公网 IP 和端口转发，再从手机热点等第二网络测试；不要在同一 Wi-Fi 内把“能连”误认为“公网能连”。

SSH 成功后的查看位置：

```bash
cd /home/ubuntu/jaka/wenshi
tail -f runtime/runs/run_*/system.log
ls -lt runtime/runs
python3 dashboard/server.py --root runtime/runs --host 127.0.0.1 --port 8088
```

### 9.2 ToDesk

ToDesk 用于查看 Ubuntu 或 Windows 工作站的图形界面和运行状态。它和 SSH 的用途不同：

- SSH：命令行维护、查看日志、检查进程。
- ToDesk：查看桌面、终端窗口、RViz 和后台页面。

ToDesk 应安装在 Ubuntu 或工作站上，机器人设备不安装远程桌面软件。远程桌面只用于观察和维护，不能代替 `stop`、急停和现场安全人员。
ToDesk 通常由设备主动连接服务，因此一般不需要为 Ubuntu 单独开放入站端口；具体仍以现场网络和 ToDesk 状态为准。

### 9.3 远程后台

当前阶段后台只在 Ubuntu 本机打开，不做局域网和公网暴露。使用一键脚本：

```bash
cd /home/ubuntu/jaka/wenshi
./scripts/start_dashboard.sh
```

脚本会启动后台服务、自动打开 Ubuntu 本机浏览器，并在终端显示 `http://127.0.0.1:8088/`。终端保持运行，按 `Ctrl+C` 停止后台。
后台服务本身由 `dashboard/server.py` 提供，浏览器页面位于 `dashboard/static/`；普通使用者不需要单独运行 Python 文件。

## 10. 后台

默认本机访问：

```bash
./scripts/start_dashboard.sh
```

后台展示当前/历史巡检、目标计数、路线段、侧别、远景/近景、质量和失败原因，不提供运动按钮。
前期不配置外部浏览器访问；需要远程查看时使用 ToDesk 进入 Ubuntu 桌面。

## 11. 故障恢复

- 相机断流：立即停止相关运动，查看 `camera.log` 和 Windows服务 `/health`，恢复后由人工决定是否结束本次运行。
- 目标丢失：抵近后退立即停底盘，目标任务通过已知回撤/巡视路径回到巡视姿态，并沿正方向继续；失败原因写入目标 metadata。
- AGV阻挡/急停：AGV和JAKA都停，排除现场原因后重新执行前置检查。
- JAKA错误或路径不在回撤通道：不得强行发送下一点，保存日志并人工回到安全姿态。
- 程序异常退出：下一次启动创建新 `run_id`，旧目录保留为异常中断记录，不自动续接旧去重。

## 12. 管理员删除和清理

后台管理员 PIN 只能执行软删除和去重重置。永久清理必须先预览：

```bash
python3 dashboard/cleanup.py --root runtime/runs --list
python3 dashboard/cleanup.py --root runtime/runs --preview run_<timestamp>
python3 dashboard/cleanup.py --root runtime/runs --execute run_<timestamp> --confirm run_<timestamp>
```

清理工具拒绝正在运行的目录、路径穿越和不完整确认。不要对 `runtime/` 根目录使用递归删除。
