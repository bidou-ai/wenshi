"""Build a portable Windows folder for offline dataset annotation."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


README_TEXT = """# Wenshi Windows 标注包

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
"""


BAT_TEXT = r"""@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  py -3 yubei\label_server.py --session dataset --host 127.0.0.1 --port 8090 --open-browser
) else (
  python yubei\label_server.py --session dataset --host 127.0.0.1 --port 8090 --open-browser
)
pause
"""


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def package_windows_labeler(session: Path, output: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    source_session = Path(session).expanduser().resolve()
    if not (source_session / "images").is_dir() or not (source_session / "labels").is_dir():
        raise ValueError("session must contain images/ and labels/")

    destination = Path(output).expanduser().resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"output directory is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    shutil.copytree(source_session, destination / "dataset")
    _copy_file(root / "yubei" / "label_server.py", destination / "yubei" / "label_server.py")
    _copy_file(root / "yubei" / "paths.py", destination / "yubei" / "paths.py")
    shutil.copytree(root / "yubei" / "label_ui", destination / "yubei" / "label_ui")
    (destination / "start_label_windows.bat").write_text(BAT_TEXT, encoding="utf-8", newline="\r\n")
    (destination / "README_WINDOWS_LABELING.md").write_text(README_TEXT, encoding="utf-8")
    return destination


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="打包可复制到 Windows 的 Wenshi 标注文件夹")
    parser.add_argument("session", type=Path, help="例如 yubei/data/dataset_20260826_120000")
    parser.add_argument("--output", type=Path, default=Path("yubei/windows_labeler_package"))
    args = parser.parse_args(argv)
    output = package_windows_labeler(args.session, args.output)
    print(f"Windows 标注包已生成: {output}")
    print("复制整个文件夹到 Windows，双击 start_label_windows.bat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
