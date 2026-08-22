# yubei 数据采集、标注与训练

## 采集

只采照片、不启动 AGV/JAKA 控制时，先运行：

```bash
./yubei/start_yubei.sh camera-check
```

确认 D435 健康、连续帧和分辨率正常后，再启动采集。

采集数据集和正式巡检是两套目录。采集工具只保存 RGB JPG：

```bash
./yubei/start_yubei.sh capture --focus flower
```

窗口显示当前 RGB，按回车保存一张，期间可以人工移动机械臂，输入 `q` 结束。输入 `f` 切换开花批次、`r`
切换水稻批次、`n` 切换普通批次。一个会话在 `yubei/data/dataset_<时间>/` 下生成；每张图会记录帧号、批次、清晰度、曝光和重复图提示。

采集结束先审计：

```bash
./yubei/start_yubei.sh audit
```

审计只报告问题，不删除照片；确认后再进入标注。

## 标注

```bash
./yubei/start_yubei.sh label
```

浏览器打开命令输出的地址。可以按“开花批次”筛选照片；新增框选择 `rice` 或 `flower`，选中已有框后可直接改类别。
支持拖动已有框、撤销/重做、复制上一张框和“保存并下一张”。严重交叠选择歧义，不把两株合成一框。

## 检查与划分

网页每次保存会同步生成 YOLO TXT；歧义/跳过状态会删除可训练 TXT。执行：

```bash
./yubei/start_yubei.sh prepare
```

检查图像、标签、类别 ID 和归一化框，并生成独立的 `train/val/data.yaml`。只有报告 `ok: true` 才进入训练。

## 训练

在有 NVIDIA GPU 的物理 Windows 工作站或具备 CUDA 的训练环境安装 `ultralytics`，Ubuntu 虚拟机暂时按 CPU 入口保留：

```bash
./yubei/start_yubei.sh train
```

入口自动选择最近生成的 `data.yaml`。训练输出在 `yubei/training/<时间>/`，不会自动覆盖正式模型。GPU 型号和 VM 直通方案记录在 `liuyi666.md`，等现场提供后再确定。

## 发布

```bash
./yubei/start_yubei.sh publish-model yubei/training/<时间>/weights/best.pt --confirm
```

发布会校验 `.pt`、计算 SHA256、保存 `models/rice_demo.json`，并将旧模型放到 `models/archive/`。
