# Wenshi yubei 预备工具

这里的工具是独立、可删除的准备区，不会被正式 `wenshi/app/wenshi_patrol` 导入。它们服务于相机检查、局域网检查、RGB 数据集采集、YOLO 标注/训练、模型发布和 JAKA 八点示教。

## 单一入口

```bash
./yubei/start_yubei.sh
```

菜单覆盖设备/相机检查、采集、标注、数据准备、训练、八点示教和验证。`check` 会读取正式
`config/wenshi.yaml` 的设备地址，避免预备检查和正式巡检检查不同目标。也可在同一文件后使用
`check`、`camera-check`、`capture`、`audit`、`label`、`prepare`、`train`、`teach`、`verify`、`publish-viewpoints`、
`publish-model` 子命令；发布命令必须显式确认并会先备份旧文件。
`./yubei/start_yubei.sh --help` 显示完整说明。

设备检查和示教读取不会自动上电、使能或运动；需要运动的机械臂动作由操作者在现场明确控制，采集工具只在到位后保存 RGB 图像。示教入口在一次只读连接中依次保存八点，遇到 TCP 拆包也会增量读取完整 JSON。

## 现场统一示教与硬件测试

现场只启动一个入口：

```bash
./scripts/start_field_test.sh
```

进入 `field>` 后可使用 `camera`/`camera stop`、`teach`、`test route`、`test arm`、`status`、`stop`、`q`。
`teach` 每次按回车重新短连接 JAKA，保存正式协议的 `joint_pos/tcp_pos` 和同名相机预览图；
它不会上电、使能或运动。`test route` 从当前位置附近接入 `wens1` 地图，先到 `LM1`，再运行
`LM1 -> LM4 -> LM3 -> LM2 -> LM1` 一圈；普通路线不发送负速度。`test arm` 要求八点示教已经
保存在本次现场测试目录，逐点等待人工确认后才运动。现场测试日志位于 `runtime/field_tests/`，
不会覆盖 `config/viewpoints.json` 或正式 `runtime/runs/`。

默认入口还会启动 RViz 和 D435 ROS2 桥：RViz 中显示 Wens1 地图、LM 标记、AGV 位姿和相机 RGB 画面。
ROS2 桥只读状态/图像，不拥有 AGV/JAKA 运动权限。没有图形环境时使用
`./scripts/start_field_test.sh --no-rviz`。现场测试路线速度默认 `0.10m/s`；普通避障会停住，障碍物连续解除
`2.0s` 后从当前线段继续，急停仍需人工复位。启动时若已靠近 LM4 等拐角，会按站点接入而不重复跑前一段。
示教单点通信失败会提示保持当前位置重新按回车，继续保存该点，不会因一次超时退出八点流程。

数据集采集与正式巡检采集完全分开。数据集会话使用 `yubei/data/dataset_<timestamp>/`；正式巡检使用 `runtime/runs/run_<timestamp>/`。训练集划分优先按 `plant_id` 分组，其次按 `capture_batch`；缺少两者的旧数据会按整个会话分组，绝不把同一未知会话的相关帧拆到 train 和 val。

只拍照片时使用 `camera-check`，它只访问 Windows D435 服务，不检查、不连接、不控制 AGV/JAKA。

采集开花照片时可直接使用：

```bash
./yubei/start_yubei.sh capture --focus flower
```

预览窗口中回车保存当前帧；输入 `f`、`r`、`n` 只切换后续照片的批次标记，不会自动改变 YOLO 类别。
每张照片会记录相机帧号、清晰度/曝光指标和相似图提示。采集结束后运行
`./yubei/start_yubei.sh audit` 生成 `capture_audit.json`，先处理模糊、曝光异常和重复图，再进入标注。

## 在另一台 Windows 电脑标注

Ubuntu 上先把最新采集会话打包：

```bash
cd /home/ubuntu/jaka/wenshi
./yubei/start_yubei.sh package-labeler
```

也可以指定某个会话和输出目录：

```bash
./yubei/start_yubei.sh package-labeler yubei/data/dataset_时间 yubei/windows_labeler_dataset_时间
```

把生成的整个 `yubei/windows_labeler_dataset_时间/` 文件夹复制到 Windows。Windows 电脑只需要安装 Python 3.10
或更新版本，双击里面的 `start_label_windows.bat`，浏览器会打开和 Ubuntu 相同的标注网页。

标注完成后，把 Windows 包里的 `dataset/labels/` 整个文件夹复制回 Ubuntu 原始会话的 `labels/`，覆盖同名
`json/txt` 文件。随后在 Ubuntu 执行：

```bash
./yubei/start_yubei.sh prepare yubei/data/dataset_时间
```
