# Wenshi 明日现场测试清单

这份清单按“先只读、再单机、最后联动”执行。每一阶段只在上一阶段通过后继续。任何急停、报警、
姿态异常、目标丢失或人员进入运动区域，先按现场急停，再输入 `stop`，不要靠改配置绕过互锁。

## 1. 当前结论

离线代码、配置解析、数据目录、后台、预备工具和状态机已有自动测试。当前正式配置仍有以下硬阻断：

| 完整视觉 Demo 条件 | 当前状态 | 现场动作 |
|---|---|---|
| rice 模型与评估 | 模型未发布 | 先采集、标注、训练、回放评估 |
| 八点示教 | 缺 `home_safe` | 重新保存八点并验证后发布 |
| `vision.enabled` | `false` | 模型验证后才允许开启 |
| `fixed_approach.enabled` | `false` | 左右路径低速验收后才允许开启 |
| `patrol_target.enabled` | `false` | 所有目标任务条件通过后才开启 |
| `rear_radar_verified` 和倒车锁 | `false` | 实车确认雷达/看护/停止距离 |
| `target_reverse_limit_m` | 暂存 0.60m，未标定 | 实测多走/少走后再定，不可直接照用 |

上述任一项未完成时，不得启用完整视觉抵近。可以验收后台、相机、数据集工具、JAKA只读示教、
基础正向路线和单项安全停止。

## 2. 四个日常入口

| 位置 | 只启动这个文件 | 作用 |
|---|---|---|
| 机器人 Windows 相机电脑 | 双击 `start_camera_server.bat`，或直接运行 `windows_realsense_server.py` | D435 HTTP 服务 |
| Ubuntu 预备工具 | `./yubei/start_yubei.sh` | 检查、采集、标注、训练、示教 |
| Ubuntu 现场测试 | `./scripts/start_field_test.sh` | 自动启动 D435 ROS2 桥、RViz、相机预览、示教、地图单圈、示教点运动 |
| Ubuntu 正式巡检 | `./scripts/start_wenshi.sh` | 全预检后启动正式控制台 |
| Ubuntu 结果后台 | `./scripts/start_dashboard.sh` | 本机浏览器复核远近景 |

四个入口都可以从项目文档指定位置直接启动。正式巡检脚本内部已经包含
`check_environment.sh` 和 `check_hardware_links.sh`，操作者不再依次启动多个文件。

## 3. 无机器人离线检查

在 Ubuntu 新终端执行：

```bash
cd /home/ubuntu/jaka/wenshi
./scripts/start_wenshi.sh --help
./yubei/start_yubei.sh --help
./scripts/start_dashboard.sh
```

验收：后台自动打开 `http://127.0.0.1:8088/`；没有巡检数据时显示空状态；按 `Ctrl+C` 正常停止。
未设置 `WENSHI_ADMIN_PIN` 时查看正常、管理登录失败，这是预期安全状态。

## 4. 网络与 D435

1. AGV、JAKA、Windows 相机电脑保持接在**机器人底盘内部路由器**；不要把它们接到温室公网路由器。
2. Ubuntu 必须有一条单独路径进入底盘内部路由器，机器人网卡只走 `192.168.192.0/24`，且不设默认网关。
3. Ubuntu 另一张网卡连接温室路由器 Wi-Fi 上公网，承担默认路由。
4. 在机器人 Windows 相机电脑启动服务并保持窗口打开：有 `start_camera_server.bat` 时双击；只有
   `windows_realsense_server.py` 时在其目录运行 `py windows_realsense_server.py --host 0.0.0.0 --port 18080`。
5. Ubuntu 运行 `./yubei/start_yubei.sh check`，保存完整输出。

必须确认：AGV `192.168.192.5:19204`、JAKA `192.168.192.160:10001`、D435
`192.168.192.203:18080` 可达；D435 `/health` 为 `ok=true`；连续 10 帧可解码且尺寸符合预期。

## 5. JAKA 示教与单机检查

先由现场人员上电、使能、清理工作空间，程序不会代做。运行：

```bash
./yubei/start_yubei.sh teach
./yubei/start_yubei.sh verify
```

按顺序人工移动并保存 `home_safe`、`camera`、`camera_left`、`camera_right`、`left_pre`、
`left_photo`、`right_pre`、`right_photo`。工具只读关节/TCP，不发送运动命令。验证报告通过后，
仍要人工复核每个点、左右方向、相机朝后关系、关节跨越和回撤通道，才能显式发布到正式配置。

现场统一入口也可以完成同样示教和后续动作测试：

```bash
./scripts/start_field_test.sh
field> teach
field> test route
field> test arm
```

启动后先确认 RViz 中能看到 `/map` 地图、LM 站点、红色 AGV 位姿箭头和 D435 RGB 画面；若只做无图形硬件测试，使用
`./scripts/start_field_test.sh --no-rviz`。RViz/相机桥日志在 `runtime/field_tests/field_test_ros_<时间>/`。

`test route` 允许底盘只放在路线附近，程序会沿闭环方向接入并到 LM1，然后完整运行
`LM1 -> LM4 -> LM3 -> LM2 -> LM1`。`test arm` 必须在底盘停止时逐点确认；任一异常先按实体急停，再输入 `stop`。
路线测试默认速度为 `0.10m/s`。普通避障触发时应立即停止，障碍物清除并连续稳定 `2.0s` 后从当前线段自动继续；急停、定位过期、横向偏差超限或通信中断必须保持停止并报告失败。
若从 LM4 附近开始，日志中应看到从 `LM4->LM3` 接入，而不是重复 `LM1->LM4`。在 LM4、LM1 转弯后应沿下一地图段正向前进，不能出现负 `vx`。

## 6. 基础正向路线

底盘放在 LM1 附近，确认急停、定位、障碍区和人员位置。先运行：

```bash
./scripts/start_wenshi.sh --check
```

全部通过后再运行 `./scripts/start_wenshi.sh`。控制台先输入 `status`，再输入 `start` 做一次
`LM1 -> LM4 -> LM3 -> LM2`。本阶段 `patrol_target.enabled=false`，不得出现目标对齐倒车。
记录每段方向、停止误差、是否误触发阻挡、J5巡视范围和 `runtime/runs/run_<时间>/` 全部日志。
只有单圈稳定后才使用 `start loop`。

## 7. 数据集与模型

使用 `./yubei/start_yubei.sh` 菜单依次完成采集、标注、准备和训练。采集每次回车只保存一张 RGB；
一张图可标多个完整植株；严重交叠标为 `ambiguous`。`prepare` 只纳入 `labelled` 图片并自动生成
`train/val/data.yaml`。训练完成后先检查验证集误检、漏检和完整植株框，再人工发布模型。

发布后重新运行 `./scripts/start_wenshi.sh --check`。预检仍会列出未通过的目标 Demo 条件，必须逐项关闭。
目标任务启用后，预检也必须确认运行巡检的同一个 Python 环境可以导入 `ultralytics`。

## 8. 完整目标任务放行

只有以下证据齐全时才讨论修改正式开关：rice 模型文件存在且离线评估通过；八点示教已发布；
左右固定抵近分别低速验收；后退区域清空；车尾雷达状态已确认；`target_reverse_limit_m` 从短距离逐步实测；
J5目标跟随方向正确；远近景质量阈值用现场图验证。

现场必须专门放两株同侧、画面有轻微交叠的水稻，确认从远景锁定、后退跟随到近景不会切换到邻株。
当前完整植株构图判断依赖 rice bbox 是否贴合整株上部，因此还要人工确认顶部不裁切、主体完整且根茎露出不超过约2cm。

放行后顺序应为：稳定 rice 3/5帧 -> 远景质量合格 -> 选中央株 -> 低速后退且 J5跟随 ->
重新定位 -> 锁定侧示教抵近 -> 当前近拍姿态最多3轮、每轮5张 -> 只保存最佳合格图 -> 回撤 ->
继续原路线。周围30cm目标本圈暂缓，同株2小时不重复；后台重置后控制器写入
`dedupe_reset_applied` 才算真正生效。

## 9. 必须保存的现场结果

每个阶段记录：时间、操作者、配置提交号、设备 IP、命令、结果、失败原因、照片和对应 `run_id`。
重点查看 `system.log`、`console.log`、`camera.log`、`camera_console.log`、`events.jsonl`、`agv.csv`、
`jaka.csv` 和每株 `metadata.json`。现场没有验证的项目写“未验证”，不要写“通过”。
