# Wenshi yubei 预备工具

这里的工具是独立、可删除的准备区，不会被正式 `wenshi/app/wenshi_patrol` 导入。它们服务于相机检查、局域网检查、RGB 数据集采集、YOLO 标注/训练、模型发布和 JAKA 八点示教。

## 常用命令

```bash
python3 yubei/camera_check.py --url http://192.168.192.203:18080 --samples 10
python3 yubei/network_check.py --agv 192.168.192.5 --jaka 192.168.192.160 --camera 192.168.192.203
python3 yubei/device_check.py --agv 192.168.192.5 --jaka 192.168.192.160
python3 yubei/dataset_capture.py --output yubei/data --preview
python3 yubei/label_server.py --session yubei/data/dataset_<时间>
python3 yubei/dataset_validate.py yubei/data/dataset_<时间>
python3 yubei/teach_viewpoints.py --output yubei/viewpoints_staged.json --name camera --save
python3 yubei/viewpoint_verify.py yubei/viewpoints_staged.json
```

设备检查和示教读取不会自动上电、使能或运动；需要运动的机械臂动作由操作者在现场明确控制，采集工具只在到位后保存 RGB 图像。

数据集采集与正式巡检采集完全分开。数据集会话使用 `yubei/data/dataset_<timestamp>/`；正式巡检使用 `runtime/runs/run_<timestamp>/`。

