# Windows D435 服务

第一次在直连 D435 的 Windows 工控机上安装依赖：

```powershell
py -m pip install -r requirements-windows.txt
```

之后日常只需双击：

```text
start_camera_server.bat
```

Ubuntu 端应检查 `http://192.168.192.203:18080/health`，而不是只 ping `192.168.192.203`。
`/health` 可访问且 `ok=false` 表示 Windows 服务存在但 D435 没有有效帧；连接被拒绝则表示
该 IP:端口没有监听服务，需检查 Windows 进程、防火墙、网卡和服务启动日志。
