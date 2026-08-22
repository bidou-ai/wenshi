# Wenshi 温室巡检

这是 Wens1 温室的独立巡检项目。正式路线固定为 `LM1 -> LM4 -> LM3 -> LM2`，由
`config/wenshi.yaml` 和 `map/wenshi.smap` 共同定义。

项目按硬件客户端、路线控制、机械臂固定示教路径、D435 相机桥和纯视觉模块分层。视觉默认
关闭，且没有运动控制权限；未完成现场验证前，固定抵近和倒车同样默认关闭。

## 启动

正式巡检只需要一个入口。脚本会自动加载 ROS2、执行离线配置检查和 AGV/JAKA/D435
真实连通检查，全部通过后才创建本次运行目录并启动组件：

```bash
./scripts/start_wenshi.sh
```

只检查而不启动组件使用 `./scripts/start_wenshi.sh --check`。进入控制台后，`start` 执行一圈，
`start loop` 连续巡检。当前配置仍安全关闭视觉目标任务、固定抵近和倒车；没有模型、八点示教与
现场安全验收时，不得把基础路线测试当成完整视觉 Demo。

控制台命令、硬件前置条件和故障处理见 [操作手册](docs/OPERATIONS.md)；完整的软件、控制和数据流程见
[用户手册](docs/USER_MANUAL.md)。巡检结果后台在 Ubuntu 本机用 `./scripts/start_dashboard.sh` 一键启动并自动打开浏览器。

联网布局、Ubuntu 双网卡、外部 SSH 和 ToDesk 的边界见用户手册第 4、9 节：机器人设备保持内部网，只有 Ubuntu 通过 Wi-Fi/NAT 上公网。

准备工具全部位于独立的 `yubei/`，统一从 `./yubei/start_yubei.sh` 进入，可单独用于相机/网络/设备检查、YOLO 数据集采集、框选标注、数据集验证和训练，
不会被正式巡检运行时导入。

明日现场逐项验收顺序和当前阻断条件见 [现场测试清单](docs/FIELD_TEST_CHECKLIST.md)。

Ubuntu 的 Python 依赖见 `requirements-ubuntu.txt`；Windows D435 服务的依赖见
`windows/requirements-windows.txt`。模型、数据集和标定文件不纳入源码仓库。

GitHub 私有仓库的首次配置、日常同步、换电脑恢复和错误处理见
[GitHub 同步操作手册](docs/GITHUB_SYNC.md)。
