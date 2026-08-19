# Wenshi 温室巡检

这是 Wens1 温室的独立巡检项目。正式路线固定为 `LM1 -> LM4 -> LM3 -> LM2`，由
`config/wenshi.yaml` 和 `map/wenshi.smap` 共同定义。

项目按硬件客户端、路线控制、机械臂固定示教路径、D435 相机桥和纯视觉模块分层。视觉默认
关闭，且没有运动控制权限；未完成现场验证前，固定抵近和倒车同样默认关闭。

## 启动

在 ROS2 环境已加载、AGV/JAKA/D435 均已按现场流程检查后运行：

```bash
./scripts/check_environment.sh
./scripts/start_wenshi.sh
```

控制台命令、硬件前置条件和故障处理见 [操作手册](docs/OPERATIONS.md)。

Ubuntu 的 Python 依赖见 `requirements-ubuntu.txt`；Windows D435 服务的依赖见
`windows/requirements-windows.txt`。模型、数据集和标定文件不纳入源码仓库。
