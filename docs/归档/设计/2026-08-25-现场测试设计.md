# Wenshi 现场示教与硬件测试设计

## 目标

现场只启动一个入口完成相机预览、JAKA 八点示教、AGV 按 Wens1 地图跑一圈和机械臂示教点运动测试。底盘可以从路线附近接入，程序先沿闭环路线到 LM1，再执行 `LM1 -> LM4 -> LM3 -> LM2 -> LM1`；普通路线不发送倒车速度。

数据集采集、标注、训练和正式巡检代码仍保留，但不进入现场测试入口。现场测试日志独立保存在 `runtime/field_tests/`，不会写入正式 `runtime/runs/`。

## 入口与所有权

入口为：

```bash
./scripts/start_field_test.sh
```

该脚本是现场唯一入口。它启动一个拥有 AGV 状态/运动客户端和 JAKA 运动客户端的 `yubei/field_test.py`，并按环境启动只读的 D435 ROS2 桥和 RViz；后两者只发布/显示，不拥有运动权限。不得同时运行正式巡检控制台、旧 `AgvControl` 或 Roboshop。

## 操作流程

启动后提供：

- `camera`：启动/停止 RGB 预览并显示相机状态。
- `teach`：启动相机预览，人工移动机械臂，按回车依次保存八点；每次保存重新连接 JAKA，读取 `joint_pos`/`tcp_pos`，并保存同名预览图。
- `test arm`：底盘保持停止，连接 JAKA，按 `home_safe -> camera -> camera_left -> camera_right -> left_pre -> left_photo -> right_pre -> right_photo` 逐点运动；每一点运动前等待人工回车确认，输入 `q` 或 `stop` 立即停止。
- `test route`：检查 AGV 状态，优先将当前位置吸附到附近 LM 站点后选择前向线段；横向偏差超过配置上限则拒绝运动。先沿地图方向到 LM1，再跑完整闭环回到 LM1。现场测试默认 `0.10m/s`。普通阻挡进入停止等待，连续解除 `2.0s` 后从当前线段继续；急停、定位过期、通信中断或人工停止立即结束并保持停止。
- `status`：显示 AGV 位姿、路线段、JAKA 状态、相机帧和当前测试状态。
- `stop`：停止 AGV 和 JAKA 当前动作。
- `q`：停止所有客户端并退出。

## 示教与相机

示教连接只允许 `get_joint_pos` 和 `get_tcp_pos`。每次按回车重新建立 TCP 连接，最多执行一次关节和 TCP 查询；连接超时、查询超时、控制器关闭连接和字段不完整都转换为中文错误，不输出未处理 traceback。相机预览进程只读取 Windows D435 `/frame`，没有运动权限；无 GUI 时允许使用 `--no-preview`，示教仍可保存关节数据。

## AGV 路线

路线站点来自 `map/wenshi.smap` 和 `route.station_order`。闭环由 `make_segments(..., loop=True)` 生成。接入阶段先按 `field_test.station_snap_m` 判断是否在 LM 站点附近，避免在拐角处重复上一段；否则选择最近路线段。首段目标为该段终点，随后沿闭环顺序到 LM1。接入和正式单圈都只使用正的车体前向速度；航向偏差较大时先原地纠偏，纠偏完成后再前进。路线测试速度、避障稳定时间、站点吸附距离、最大接入横向偏差、到站容差和状态新鲜度从配置读取。

## JAKA 运动

机械臂不由软件上电、使能、下电或解除报警。运动测试使用正式 `JakaClient.joint_move`，速度和加速度低于巡检默认值。示教文件必须包含八个六关节点，相邻点关节跨度超过安全上限时拒绝测试；每个点前人工确认，运动无进展、状态过期、连接中断或用户停止都会发送 `stop_program` 并结束测试。

## 日志与文件

每次入口启动创建：

```text
runtime/field_tests/field_test_<timestamp>/
  field_test.log
  events.jsonl
  teach/<viewpoint>.jpg
  teach/viewpoints.json
```

启动脚本还会按需创建 `runtime/field_tests/field_test_ros_<timestamp>/`，保存 `camera.log`、`camera_console.log`
和 `rviz.log`。这些是现场测试的显示辅助日志，可在确认不再需要后整体清理；不得删除正在运行的目录。

现场测试不会覆盖 `config/viewpoints.json`。验证通过后仍使用现有 `yubei/start_yubei.sh publish-viewpoints --confirm` 显式发布。

## 暂留疑问

机器人公网/Wi-Fi 拓扑和远程访问方案继续保留在 `liuyi666.md`，本次现场测试不修改网络配置，也不开放公网端口。
