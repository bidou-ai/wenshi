# Wenshi Windows 标注包

这个文件夹可以复制到另一台 Windows 电脑上标注，不需要连接机器人、不需要 D435、不需要 ROS。

## 启动

1. 确认 Windows 已安装 Python 3.10 或更新版本。
2. 双击 `start_label_windows.bat`。
3. 浏览器打开 `http://127.0.0.1:8090/` 后开始框选。
4. 标注完成后关闭命令行窗口，或在窗口按 `Ctrl+C`。

## 标注规则

- `rice`：框完整植株可见地上部分。
- `flower`：只框可见花部，不要把整株标成 flower。
- 无目标、质量差、重复图：点“无目标/质量差/重复图”。
- 严重交叠、无法判断归属：点“标记歧义”。

## 带回 Ubuntu

标注结果写在 `dataset/labels/`：

- `*.json` 是网页可继续编辑的标注状态。
- `*.txt` 是 YOLO 训练标签，只会为已标注图片生成。

把这个包里的整个 `dataset/labels/` 文件夹复制回 Ubuntu 原始会话的 `labels/`，覆盖同名文件即可。
