# GitHub 上传操作手册

本文只说明一件事：怎样把 `/home/ubuntu/jaka/wenshi` 的项目文件上传到
`https://github.com/bidou-ai/wenshi`。

## 先记住三个命令

```bash
git add 文件路径
git commit -m "本次修改说明"
git push
```

- `git add`：选择这次要上传的文件。
- `git commit`：把选择的文件保存为一次 Git 记录。
- `git push`：把已经提交的记录上传到 GitHub。

只运行 `git push`，不会上传还没有执行 `git add` 和 `git commit` 的文件。

## 每次上传的完整步骤

### 1. 进入项目

```bash
cd /home/ubuntu/jaka/wenshi
git status
```

`git status` 中常见的标记：

- `M`：这个文件修改过，但还没有提交。
- `??`：这是新文件，Git 还没有管理它。
- 没有列出文件：当前没有需要提交的修改。

### 2. 选择要上传的项目文件

上传当前全部项目源码、脚本、配置和文档，可以运行：

```bash
git add -A
git status
git diff --cached --name-only
```

最后两个命令用于确认即将提交的文件。发现误生成文件、密码、密钥、数据集、模型或运行照片时，先取消该文件的暂存：

```bash
git restore --staged "文件路径"
```

取消暂存不会删除本机文件。

### 3. 运行测试

```bash
PYTHONPATH=app:. python3 -m pytest -q
```

看到 `passed` 且没有 `failed` 后再继续。测试失败时不要提交和上传。

### 4. 提交

```bash
git commit -m "说明本次修改内容"
```

例如：

```bash
git commit -m "补齐 9.18 Demo 启动脚本和配置"
```

### 5. 上传当前分支

第一次上传这个分支：

```bash
git push -u origin HEAD
```

以后继续上传同一个分支：

```bash
git push
```

看到 `[new branch]` 或分支更新信息，才表示提交已经上传。

### 6. 合并到 main

上传开发分支后，打开 GitHub 提示的 Pull Request 地址，将开发分支合并到
`main`。只有完成合并，GitHub 默认显示的 `main` 才能看到新文件。

如果文件已经上传到开发分支，但在 GitHub 首页找不到，先在 GitHub 左上角的分支选择器中切换到当前开发分支。

## 本次 9.18 文件为什么没有上传

`wenshi.sh` 已经提交并上传到了 `codex/32-phenotyping-docs`，但该分支还没有合并到 `main`。

下面三个文件之前显示为 `??`，说明它们没有进入任何提交，所以 `git push` 不会上传它们：

```text
9.18.sh
config/9.18.yaml
config/9.18.rviz
```

把它们加入一次提交的命令是：

```bash
git add 9.18.sh config/9.18.yaml config/9.18.rviz
git status
git commit -m "补齐 9.18 Demo 启动脚本和配置"
git push
```

## 哪些内容不上传

`.gitignore` 会排除这些本机产物：

- `runtime/` 中的现场运行照片和日志；
- `models/` 中的模型权重；
- `datasets/` 和 `yubei/data/` 中的数据集；
- `yubei/training/` 中的训练结果；
- Python 和 pytest 缓存；
- `*.log` 日志。

这些内容不是项目源码，体积可能很大，也可能包含现场数据。GitHub 用来保存源码、脚本、配置、文档和测试；现场数据另行备份。

绝对不要上传 GitHub Token、SSH 私钥、密码、验证码或 `.env` 密钥文件。

## 常见问题

### `rejected` 或 `non-fast-forward`

不要使用强制推送。先执行：

```bash
git status
git fetch origin
git merge origin/main
PYTHONPATH=app:. python3 -m pytest -q
git push
```

如果出现冲突，停止上传，先处理冲突并重新测试。

### `Permission denied (publickey)`

执行：

```bash
ssh -T git@github.com
```

该错误表示当前电脑的 SSH 公钥没有正确关联 GitHub 账号。

### 换电脑下载项目

```bash
mkdir -p ~/jaka
cd ~/jaka
git clone git@github.com:bidou-ai/wenshi.git
cd wenshi
```

数据集、模型、运行日志和现场照片不会随源码仓库下载。
