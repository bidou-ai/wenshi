# yubei 数据采集、标注与训练

## 采集

采集数据集和正式巡检是两套目录。采集工具只保存 RGB JPG：

```bash
python3 yubei/dataset_capture.py --output yubei/data --url http://192.168.192.203:18080 --preview
```

窗口显示当前 RGB，按回车保存一张，期间可以人工移动机械臂，输入 `q` 结束。一个会话在 `yubei/data/dataset_<时间>/` 下生成。

## 标注

```bash
python3 yubei/label_server.py --session yubei/data/dataset_<时间>
```

浏览器打开命令输出的地址。首版只画 `rice`；`flower` 保留为第二类。严重交叠选择歧义，不把两株合成一框。

## 检查与划分

网页保存 YOLO TXT 后执行：

```bash
python3 yubei/dataset_validate.py yubei/data/dataset_<时间>
```

检查图像、标签、类别 ID 和归一化框。只有报告 `ok: true` 才进入训练。

## 训练

在有 NVIDIA GPU 的物理 Windows 工作站或具备 CUDA 的训练环境安装 `ultralytics`，Ubuntu 虚拟机暂时按 CPU 入口保留：

```bash
python3 yubei/train_yolo.py --data yubei/yolo_data.yaml --device cpu --epochs 100
```

训练输出在 `yubei/training/<时间>/`，不会自动覆盖正式模型。GPU 型号和 VM 直通方案记录在 `liuyi666.md`，等现场提供后再确定。

## 发布

```bash
python3 yubei/publish_model.py yubei/training/<时间>/weights/best.pt --models models --source-run dataset_<时间>
```

发布会校验 `.pt`、计算 SHA256、保存 `models/rice_demo.json`，并将旧模型放到 `models/archive/`。
