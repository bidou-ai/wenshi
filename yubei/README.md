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

数据集采集与正式巡检采集完全分开。数据集会话使用 `yubei/data/dataset_<timestamp>/`；正式巡检使用 `runtime/runs/run_<timestamp>/`。

只拍照片时使用 `camera-check`，它只访问 Windows D435 服务，不检查、不连接、不控制 AGV/JAKA。

采集开花照片时可直接使用：

```bash
./yubei/start_yubei.sh capture --focus flower
```

预览窗口中回车保存当前帧；输入 `f`、`r`、`n` 只切换后续照片的批次标记，不会自动改变 YOLO 类别。
每张照片会记录相机帧号、清晰度/曝光指标和相似图提示。采集结束后运行
`./yubei/start_yubei.sh audit` 生成 `capture_audit.json`，先处理模糊、曝光异常和重复图，再进入标注。
