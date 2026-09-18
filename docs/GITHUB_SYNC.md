# Wenshi 上传 GitHub 操作手册

本项目只使用 `main`。平时只在 `/home/ubuntu/jaka/wenshi` 工作，不使用
`/home/ubuntu/jaka/past`，也不把 `past` 中的文件上传到 GitHub。

## 最短流程

每次修改完成后，依次执行：

```bash
cd /home/ubuntu/jaka/wenshi
git status
PYTHONPATH=app:. python3 -m pytest -q
git add -A
git status
git diff --cached --name-only
git commit -m "说明本次修改内容"
git push origin main
```

看到测试只有 `passed`、没有 `failed`，并且 `git push` 最后显示
`main -> main`，才表示本次上传完成。

## 每天开始工作

先进入正式项目并确认分支：

```bash
cd /home/ubuntu/jaka/wenshi
git branch --show-current
git status
```

第一条命令必须显示：

```text
main
```

如果 `git status` 没有显示待提交文件，再同步 GitHub：

```bash
git pull --ff-only origin main
```

如果存在未提交修改，先不要执行 `git pull`，先完成或确认这些修改。

## 上传一次修改

### 1. 查看改了什么

```bash
git status
git diff
```

`git status` 中：

- `M` 表示已经修改的文件；
- `??` 表示还没有加入 Git 的新文件；
- 没有列出文件表示当前没有修改。

### 2. 运行测试

```bash
PYTHONPATH=app:. python3 -m pytest -q
```

测试失败时停止，不要提交和上传。

### 3. 选择全部项目修改

```bash
git add -A
git status
git diff --cached --name-only
```

`git add -A` 会选择所有没有被 `.gitignore` 排除的项目文件，包括新脚本、新配置、源码、文档和测试。

提交前必须查看文件列表。发现不该上传的文件时，使用：

```bash
git restore --staged "文件路径"
```

该命令只取消选择，不删除本机文件。

### 4. 提交并上传 main

```bash
git commit -m "说明本次修改内容"
git push origin main
```

`git commit` 只保存到本机，`git push origin main` 才会上传到 GitHub。

## 上传后检查

打开：

`https://github.com/bidou-ai/wenshi`

确认页面分支为 `main`，并确认最新提交说明与刚才的 `git commit` 一致。

也可以在本机检查：

```bash
git status
```

正常结果应包含：

```text
位于分支 main
您的分支与上游分支 'origin/main' 一致
```

## 哪些内容不会上传

`.gitignore` 会自动排除：

- `runtime/` 中的运行照片和日志；
- `models/` 中的模型权重；
- `datasets/`、`yubei/data/` 中的数据集；
- `yubei/training/` 中的训练结果；
- Python、pytest 缓存和 `*.log`；
- 本机误生成的临时文件。

这些内容不是源码备份的一部分。模型、数据集和现场运行记录需要单独备份。

绝对不要上传密码、GitHub Token、SSH 私钥、验证码或 `.env` 密钥文件。

## 常见错误

### `non-fast-forward` 或 `fetch first`

表示 GitHub 的 `main` 出现了本机还没有的提交。确认本机修改已经提交后执行：

```bash
git pull --rebase origin main
PYTHONPATH=app:. python3 -m pytest -q
git push origin main
```

如果出现冲突，停止操作，不要使用 `git push --force`。

### `Could not resolve hostname github.com`

这是虚拟机网络或 DNS 故障，不是 Git 权限问题。先检查：

```bash
nmcli device status
ip route
getent hosts github.com
```

网络恢复后重新执行：

```bash
git push origin main
```

### `Permission denied (publickey)`

执行：

```bash
ssh -T git@github.com
```

该错误表示当前电脑的 SSH 公钥没有正确关联 GitHub 账号。

## 关于分支

当前日常流程不创建开发分支，也不要求 Pull Request。以后只有多人同时修改项目、需要代码审查时，才考虑重新使用独立分支。
