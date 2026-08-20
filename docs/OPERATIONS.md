# 操作手册

先在 Ubuntu 加载 ROS2 环境，并确认 AGV、JAKA 已由现场人员完成上电、使能和急停检查。
项目不会执行这些动作。

```bash
cd /home/ubuntu/jaka/wenshi
./scripts/check_environment.sh
./scripts/check_hardware_links.sh
./scripts/start_wenshi.sh
```

控制台命令：

| 命令 | 含义 |
|---|---|
| `status` | 读取 AGV、JAKA、D435 和当前状态 |
| `start` | 从 LM1 执行一次 `LM1 -> LM4 -> LM3 -> LM2` |
| `start loop` | 重复同一条正式路线 |
| `stop` | 立即停止底盘与机械臂动作 |
| `goto home` | 仅通过已知示教回撤通道回到 `camera` |
| `test arm` | 在底盘停止时做 J5 巡视测试 |
| `test fixed right` / `left` | 固定示教抵近，默认被配置禁用 |
| `test forward 0.2` | 不超过配置上限的低速前进测试 |
| `test back 0.2` | 倒车测试，默认拒绝 |
| `collect` | 保存最新合格彩色图及可同步深度图 |
| `detect` | 已验证模型启用后执行检测；默认明确拒绝 |
| `logs` | 显示当前和历史运行目录 |

`start` 要求底盘位于 LM1 附近、AGV 定位新鲜且无报警、JAKA 可读关节并处于巡视姿态。
`goto home` 不是通用避障规划，只能从已知示教回撤通道或近端姿态执行。

网络布局必须保持为“两条路径”：Ubuntu 网卡 A 连接机器人内部网，Ubuntu 网卡 B 通过工作站 Wi-Fi 上公网。
AGV、JAKA 和机器人上的 Windows 相机电脑不进入公网；网卡 A 不设置默认网关，默认路由只放在网卡 B。

外部 SSH 不是由“Ubuntu 能上网”自动产生的。要从温室外 SSH，公网路由器还必须把端口转发到 Ubuntu；如果 Wi-Fi 没有公网 IPv4 或运营商使用 CGNAT，则普通公网 SSH 不成立。ToDesk 可用于远程查看桌面，机器人设备不安装 ToDesk。
Wi-Fi 密码只保护 Wi-Fi 加入权限；外部 SSH 是否能连取决于公网地址、端口转发和 Ubuntu 的 SSH 服务。

正式目标任务在 `config/wenshi.yaml` 的 `patrol_target.enabled` 下单独受控。启用前必须同时满足视觉模型存在、
固定抵近示教已验证、J5 跟随和相机画面方向已现场确认。运行时顺序为：远端 rice 初筛 -> 锁定一株并记录路线段/左右侧/前后顺序
-> 低速目标对齐 -> 当前近拍姿态连拍并只保留质量最高的一张 -> 继续原路线。普通巡检路线段不允许倒车；
目标对齐阶段的慢速后退是独立状态，仍受 `vision.target_reverse_speed_mps`、`vision.target_reverse_limit_m` 和停止条件约束。

结果查看后台：

```bash
./scripts/start_dashboard.sh
```

脚本会在 Ubuntu 本机启动后台并尝试自动打开浏览器，地址为 `http://127.0.0.1:8088/`；终端保持运行，按 `Ctrl+C` 停止。
后台没有运动控制接口；管理员 PIN 只用于软删除目标/运行和重置本次去重标记。
