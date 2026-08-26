# Wenshi 温室巡检操作手册

> **先看结论**：AGV、JAKA 和机器人相机电脑继续留在机器人底盘内部路由器；温室路由器只给工作站和 Ubuntu 提供公网 Wi-Fi。
> Ubuntu 需要两条独立网络路径：一条进入底盘路由器访问机器人设备，一条连接温室路由器访问公网。远程 SSH 只有在公网路由器把端口转发到 Ubuntu 后才成立；
> ToDesk 则用于远程查看图形界面。不要把 AGV、JAKA 或机器人相机电脑的端口发布到公网。

## 快速导航

| 需要做什么 | 看哪一节 |
|---|---|
| 理解机器人、工作站、Ubuntu 的连接关系 | 第 4 节 网络布局 |
| 从外部电脑 SSH 到 Ubuntu | 第 9 节 远程 SSH |
| 远程查看 Ubuntu 图形界面 | 第 9 节 ToDesk |
| 查看巡检照片和后台 | 第 10 节 后台 |
| 处理断网、断流或误配置 | 第 11 节 故障恢复 |
| 明天按顺序做现场验收 | [现场测试清单](FIELD_TEST_CHECKLIST.md) |

## 1. 项目边界

正式项目目录是 `wenshi/`，旧工程 `jakarobot/` 只作为参考。`wenshi/yubei/` 是独立的预备工具，后续可以删除；它不被正式巡检导入。

正式路线为：

```text
LM1 -> LM4 -> LM3 -> LM2 -> LM1 -> ...
```

正常路线只向前行驶。检测到水稻后进入受控目标任务，允许底盘低速后退对位；普通路线和普通手动测试不开放倒车。后退时 J5 实时跟随目标，完成后执行锁定侧的机械臂示教路径并恢复原方向。

当前代码已经包含目标选择、远近景质量检查、2小时去重、当前圈30cm邻株暂缓、J5跟随、固定示教抵近、失败回撤和后台整理；但当前配置故意保持以下状态：

| 条件 | 当前值 | 结论 |
|---|---|---|
| rice 模型 | 未发布 | 不能做真实 rice 初筛 |
| 八点示教 | 正式文件缺 `home_safe` | 不能启用固定抵近 |
| `vision.enabled` | `false` | 识别关闭 |
| `fixed_approach.enabled` | `false` | 抵近关闭 |
| `patrol_target.enabled` | `false` | 目标任务关闭 |
| 倒车安全锁 | 未放行 | 目标对齐后退关闭 |

因此，基础路线可以单独验收，完整视觉抵近必须等上述项目和现场距离标定通过后再启用。

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
  system.log               # 控制器事件和硬件状态日志
  console.log              # 控制台输出和 Python 异常
  camera.log               # D435 桥内部日志
  camera_console.log       # 相机桥标准输出和异常
  agv.csv                  # AGV状态和速度命令采样
  jaka.csv                 # JAKA关节状态采样
  targets/T0001/
    far.jpg                # 远景唯一原图
    near.jpg               # 近景最佳原图
    metadata.json          # bbox、深度、质量、路线段、侧别、失败原因
```

数据集采集不写入这里，而写入 `wenshi/yubei/data/dataset_<timestamp>/`。训练输出写入 `wenshi/yubei/training/<timestamp>/`。发布模型在 `wenshi/models/rice_demo.pt`，旧模型在 `wenshi/models/archive/`。

## 4. 网络布局

### 4.1 网络角色

| 设备/网络 | 作用 | 是否进入公网 |
|---|---|---:|
| AGV、JAKA、机器人上的 Windows 相机电脑 | 接入机器人底盘内部路由器，提供控制和 D435 图像服务 | 否 |
| 机器人底盘内部路由器 | 只连接机器人设备的专用内网 | 否 |
| 温室路由器 | 为现场工作站/Ubuntu 提供 Wi-Fi 和公网 | 是 |

机器人上的 AGV、JAKA 和 Windows 相机电脑不需要、也不应该连接公网。公网只给运行 Ubuntu 的工作站使用。

### 4.2 两台路由器和 Ubuntu 的连接

```text
      机器人底盘内部路由器（不进公网）
       ┌──────────┬──────────┬───────────────┐
       │          │          │               │
      AGV       JAKA   机器人 Windows       │
                         相机电脑           │
                                             │
                         Ubuntu 机器人网卡 A│
                              （现场实际可为
                               Wi-Fi 或有线）

      温室路由器（公网 Wi-Fi）
                         │
                  Ubuntu 互联网网卡 B
                         │
                  GitHub / SSH / ToDesk
```

Ubuntu 的两条路径必须分工明确：

- 网卡 A 必须能进入**机器人底盘内部路由器**，只访问 `192.168.192.0/24` 的机器人设备，不设置默认网关。
- 网卡 B 才连接**温室路由器**并访问公网，承担默认路由。
- AGV、JAKA 和机器人 Windows 相机电脑不需要、也不应该连接温室公网路由器。
- 如果 Ubuntu 只有温室 Wi-Fi、没有进入底盘路由器的第二条路径，AGV/JAKA/D435 一定无法通信。
- 机器人控制流量不经过公网 Wi-Fi；Ubuntu 访问 GitHub 等公网服务不影响机器人内网地址。
- Wi-Fi 的 SSID 和密码只控制谁能加入这个 Wi-Fi；它不会自动让外部电脑能够 SSH 到 Ubuntu。

当前地址约定仍为 AGV `192.168.192.5`、JAKA `192.168.192.160`、相机 `192.168.192.203:18080`，现场如有调整只修改配置和检查参数。

### 4.3 VMware 的两张虚拟网卡

Ubuntu 虚拟机需要两张网卡：

1. **机器人网卡**：桥接到能够连接机器人底盘内部路由器的物理适配器；现场如果没有网线，必须使用连接该路由器的另一张 Wi-Fi/USB 无线适配器。
2. **互联网网卡**：连接温室路由器 Wi-Fi，可使用 VMware NAT 或桥接 Wi-Fi；它提供 Ubuntu 的默认路由。

不要把两张网卡放在同一个网段，也不要让机器人网卡获得默认网关。若工作站没有第二个网络适配器，不能同时保持“温室公网 Wi-Fi”和“底盘内部 Wi-Fi”；此时只能分时测试，不能在同一时间运行需要 AGV/JAKA 的完整流程。VMware 的桥接模式把虚拟机接入指定物理适配器，NAT 模式让虚拟机借用主机网络访问外部网络；两种模式可以分别用于两张网卡。[VMware 网络类型说明](https://knowledge.broadcom.com/external/article/309842/understanding-networking-types-in-hosted.html)

### 4.4 只做连通性检查

```bash
./yubei/start_yubei.sh check
```

这个入口从 `config/wenshi.yaml` 读取 AGV、JAKA 和 D435 地址，执行默认路由、TCP 端口、D435
健康状态和连续帧解码检查。Windows D435 服务仍需在相机电脑上人工启动，Ubuntu 只检查它是否在线。
遇到网络布局问题时，再用 `ip -4 -br address`、`ip route` 和 `ip route get` 做底层诊断；这些不是日常启动步骤。

## 5. yubei 数据集采集和标注

所有预备工具统一从一个文件进入：

```bash
./yubei/start_yubei.sh
```

如果当天只采照片，不需要 AGV/JAKA 全设备检查，使用 `./yubei/start_yubei.sh camera-check`；该命令只访问
Windows D435 服务，不发送任何机器人运动指令。完整 `check` 才会同时检查 AGV、JAKA 和 D435。

菜单选择“回车采集 RGB 数据集”。专门采集开花照片时直接运行 `./yubei/start_yubei.sh capture --focus flower`。
预览中回车保存当前帧；`f`、`r`、`n` 只切换后续照片的批次标记，`q` 结束。

实时窗口只显示 RGB。输入回车保存一张 JPG；中间可以人工移动机械臂；输入 `q` 结束。每个 bbox 是一个完整水稻植株，一个图片允许多个 bbox。轻微交叠分别标注，严重交叠选择歧义，不合成一个框。

启动标注网页：

采集结束先运行 `./yubei/start_yubei.sh audit`，检查模糊、曝光异常和重复图。菜单选择“标注最新数据集”，或运行
`./yubei/start_yubei.sh label`。脚本自动选择最新会话并打开本地浏览器。

网页支持按开花/水稻批次筛选、画框、移动/删除、撤销/重做、复制上一张框、选中框改类别、保存并下一张、
rice/flower、歧义跳过和 YOLO TXT 导出。初期可先标 rice，同时对有明确花部的照片标 `flower`。

## 6. 检查、训练和发布

菜单依次选择“验证并生成训练数据集”和“训练 YOLO 模型”，对应直接命令为：

```bash
./yubei/start_yubei.sh prepare
./yubei/start_yubei.sh train
```

`prepare` 只纳入状态为 `labelled` 的图片，排除 `ambiguous`、`skipped` 和未标注图片，生成独立的
`yubei/datasets/<会话_时间>/train/`、`val/` 和可直接训练的 `data.yaml`。静态示例 YAML 不再作为训练入口。
训练不会自动覆盖正式模型；人工确认评估结果后从同一菜单选择“发布已确认的模型”，或执行：

```bash
./yubei/start_yubei.sh publish-model yubei/training/<训练名>/weights/best.pt --confirm
```

训练不会自动覆盖正式模型；发布会计算 SHA256 并备份旧模型。当前 Ubuntu 在 VMware 中，显卡方案见 `liuyi666.md`。

## 7. 示教准备

现场只进行相机、示教和硬件动作测试时，不启动正式巡检控制台，使用统一入口：

```bash
./scripts/start_field_test.sh
```

在 `field>` 中执行 `camera`、`teach`、`test route`、`test arm`、`status`、`stop` 或 `q`。底盘可以放在
Wens1 路线附近，`test route` 会先沿地图闭环方向到 LM1，再跑 `LM1 -> LM4 -> LM3 -> LM2 -> LM1`；
普通路线不倒车。默认入口还会启动 D435 ROS2 桥和 RViz，在同一窗口显示地图、LM 站点、AGV 位姿和 RGB 画面；
无图形环境时使用 `./scripts/start_field_test.sh --no-rviz`。现场测试路线速度为 `0.10m/s`；普通避障触发后底盘
停止，障碍物连续解除 2 秒后从当前线段继续，急停不会自动恢复。若从 LM4 附近启动，程序按站点接入下一段，
不会重复 `LM1 -> LM4`。`test arm` 在底盘停止时按八个示教点逐点等待人工确认；单点通信超时会停在该点等待重试。
现场测试结果位于 `runtime/field_tests/`，显示辅助日志位于 `runtime/field_tests/field_test_ros_<时间>/`，不会覆盖正式示教文件。

完整示教点为：

```text
home_safe, camera, camera_left, camera_right,
left_pre, left_photo, right_pre, right_photo
```

菜单选择“依次保存八个示教点”，或运行：

```bash
./yubei/start_yubei.sh teach
./yubei/start_yubei.sh verify
```

工具不会自动上电或使能。验证通过后，用菜单“发布已验证的示教点”或
`./yubei/start_yubei.sh publish-viewpoints --confirm` 才覆盖正式 `config/viewpoints.json`，旧文件会备份到 `yubei/backups/`。如果没有现场保存的 `home_safe`，不得伪造或启用固定抵近。

## 8. 正式巡检

正式巡检只启动一个文件：

```bash
./scripts/start_wenshi.sh
```

脚本内部顺序固定为 ROS2 加载 -> 离线配置/地图/示教检查 -> AGV/JAKA/D435 连通检查 -> 创建唯一
`run_id` -> 相机桥/RViz/控制器。只检查使用 `./scripts/start_wenshi.sh --check`。

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
ls -lt runtime/runs
tail -f runtime/runs/run_<终端中看到的具体时间>/system.log
tail -f runtime/runs/run_<终端中看到的具体时间>/console.log
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

默认不设置管理员 PIN 时，所有查看功能可用，删除与去重重置明确禁用。需要管理功能时启动：

```bash
WENSHI_ADMIN_PIN='现场设置的PIN' ./scripts/start_dashboard.sh
```

## 11. 故障恢复

- 相机断流：立即停止相关运动，查看 `camera.log`、`camera_console.log` 和 Windows服务 `/health`，恢复后由人工决定是否结束本次运行。
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

Windows 相机电脑日常启动 D435 服务有两种方式：仓库完整复制到该电脑时双击
`windows/start_camera_server.bat`；如果该电脑只有 `windows_realsense_server.py`，就在该文件所在目录运行
`py windows_realsense_server.py --host 0.0.0.0 --port 18080`。第一次缺依赖时安装
`flask`、`numpy`、`opencv-python` 和 `pyrealsense2`；`.bat` 只是便捷包装，不是必须文件。
