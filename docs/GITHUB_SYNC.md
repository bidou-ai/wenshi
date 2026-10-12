# Wenshi 上传 GitHub 操作手册

本项目只使用 `main`，正式工作目录是 `/home/ubuntu/jaka/wenshi`。

## 上传范围

以下内容全部纳入 GitHub：

- Python、Shell、HTML、JavaScript 等源码；
- YAML、RViz、地图和其他正式运行配置；
- 单元测试、测试夹具和 GitHub Actions；
- README、操作手册、设计说明和有效的项目文档；
- 原始图片、标注、整理后的训练集和 Windows 标注包；
- 训练结果、指标图、模型权重和模型说明；
- `datasets/`、`models/`、`calibration/` 和 `yubei/` 中的项目资产。

以下内容不上传：

- `runtime/` 中的现场运行照片、临时状态和日志；
- Python、pytest 缓存和测试运行临时目录；
- `.venv/`、`venv/` 等本机 Python 环境；
- `.idea/`、`.vscode/` 和操作系统临时文件；
- `.env`、密码、Token、SSH 私钥、证书私钥等敏感环境文件；
- 普通 `*.log`、编辑器交换文件和误生成的临时文件；
- `.superpowers/sdd/` 中的代理执行过程记录。

判断原则：能够复现代码、配置、数据处理、训练或正式使用的内容上传；只记录某次本机运行过程、缓存、密钥或临时状态的内容不上传。

## 每次上传的完整流程

### 1. 进入正式项目并确认 main

```bash
cd /home/ubuntu/jaka/wenshi
git branch --show-current
git status
```

分支必须显示 `main`。不要从 `/home/ubuntu/jaka/past` 上传。

### 2. 查看修改

```bash
git status
git diff
```

`M` 表示已修改；`??` 表示尚未加入 Git 的新文件。

### 3. 运行测试

```bash
PYTHONPATH=app:. python3 -m pytest -q
```

只有测试没有 `failed` 才继续。

### 4. 检查上传范围和安全限制

```bash
python3 scripts/check_github_upload.py
```

检查器统计所有已跟踪文件和未忽略的新文件，并阻止：

- 超过 GitHub 100 MiB 单文件限制的文件；
- `.env`、私钥等敏感路径；
- 检测到 GitHub Token 或私钥内容的文件。

出现“错误”时不要提交，先处理报告中的文件。

### 5. 暂存全部项目内容并复核

```bash
git add -A
git status
git diff --cached --name-only
```

这里应能看到新数据集、模型、训练结果、源码、配置和文档。缓存、日志、运行目录和本机环境不应出现。

发现不该上传的文件时，只取消暂存：

```bash
git restore --staged "文件路径"
```

该命令不会删除本机原文件。

### 6. 提交并推送 main

```bash
git commit -m "说明本次修改内容"
git push origin main
```

上传大量数据时，压缩和传输需要较长时间。不要中途关闭终端；看到 `main -> main` 才表示推送完成。

### 7. 上传后确认

```bash
git status
git log -1 --oneline
```

正常状态应表明本地 `main` 与 `origin/main` 一致。再打开
`https://github.com/bidou-ai/wenshi`，确认最新提交和文件列表。

## 每天开始工作

工作区没有未提交修改时执行：

```bash
cd /home/ubuntu/jaka/wenshi
git pull --ff-only origin main
```

如果已经有本机修改，先不要拉取，先检查并提交当前修改。

## 数据和模型注意事项

- GitHub 拒绝超过 100 MiB 的单个文件；出现这种文件时先停止，再决定是否采用 Git LFS。
- 数据集和模型会让仓库越来越大。不要重复复制没有价值的临时版本，但保留已经用于训练、验证和交付的版本。
- 删除工作区中的已跟踪数据，下一次 `git add -A` 会把删除也提交；操作前先确认确实不再需要。
- GitHub 是项目版本库，不替代原始数据的离线备份。

## 常见错误

### `non-fast-forward` 或 `fetch first`

确认本机修改已经提交后执行：

```bash
git pull --rebase origin main
PYTHONPATH=app:. python3 -m pytest -q
git push origin main
```

出现冲突时停止，不要使用 `git push --force`。

### `Could not resolve hostname github.com`

这是虚拟机网络或 DNS 故障。检查：

```bash
nmcli device status
ip route
getent hosts github.com
```

网络恢复后重新执行 `git push origin main`。

### `Permission denied (publickey)`

```bash
ssh -T git@github.com
```

该错误表示当前电脑的 SSH 公钥没有正确关联 GitHub 账号。
