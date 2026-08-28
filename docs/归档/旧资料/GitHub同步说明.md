# GitHub 同步操作手册

本文适用于本机项目 `/home/ubuntu/jaka/wenshi` 和私有仓库
`git@github.com:bidou-ai/wenshi.git`。

## 一、已经完成的首次配置

- GitHub 仓库：`https://github.com/bidou-ai/wenshi`
- 本地分支：`main`
- 远程名称：`origin`
- SSH 公钥已添加到 GitHub。
- 本仓库提交者：`bidou-ai <255785848+bidou-ai@users.noreply.github.com>`。

查看这些配置：

```bash
cd /home/ubuntu/jaka/wenshi
git remote -v
git config user.name
git config user.email
```

## 二、每天开始工作

先进入项目并查看状态：

```bash
cd /home/ubuntu/jaka/wenshi
git status
```

如果工作区没有未提交修改，再同步 GitHub 上的新内容：

```bash
git pull --rebase origin main
```

不要在有未提交修改时随意执行 `pull --rebase`。看到冲突或错误时先停止，把完整提示保存下来再处理。

## 三、保存并上传一次修改

先检查有哪些文件变化：

```bash
git status
git diff
```

运行测试：

```bash
PYTHONPATH=app:. python3 -m pytest -q
```

确认无误后提交：

```bash
git add README.md docs/ app/ tests/
git status
git commit -m "说明本次修改内容"
git push origin main
```

`git add` 后必须再次运行 `git status`，确认没有把数据集、模型、日志或密钥加入提交。不要为了省事使用未知来源的递归删除、强制推送或历史重写命令。

## 四、哪些内容不会上传

`.gitignore` 默认排除：

- `runtime/` 下的巡检照片和日志；
- `models/` 下的模型权重；
- `datasets/` 下的数据集；
- `calibration/` 下的现场标定产物；
- Python 缓存、pytest 缓存和 `*.log`。

这些目录中的 README 或 `.gitkeep` 是说明/占位文件，会正常上传。绝对不要提交 GitHub Token、SSH 私钥、密码、验证码或 `.env` 密钥文件。

## 五、GitHub 自动测试

每次推送到 `main` 后，GitHub Actions 会自动运行离线单元测试和 Python 编译检查。查看方法：

1. 打开 `https://github.com/bidou-ai/wenshi`。
2. 点击顶部 `Actions`。
3. 打开最新的 `Python tests`。
4. 绿色对勾表示通过；红色叉号表示失败，展开 `Run tests` 查看错误。

自动测试不会连接 AGV、JAKA 或 D435，也不能代替现场硬件验收。ROS2 集成测试在无 ROS2 的 GitHub 运行器上会按设计跳过。

## 六、换电脑后下载项目

先在新电脑配置 GitHub SSH 公钥，再运行：

```bash
mkdir -p ~/jaka
cd ~/jaka
git clone git@github.com:bidou-ai/wenshi.git
cd wenshi
git config user.name "bidou-ai"
git config user.email "255785848+bidou-ai@users.noreply.github.com"
```

模型、数据集、运行日志和现场标定文件不会从 GitHub 下载，需要通过单独的受控备份恢复。

## 七、常见错误

### `Permission denied (publickey)`

当前电脑的 SSH 公钥没有添加到 GitHub，或 SSH 使用了错误账号。先测试：

```bash
ssh -T git@github.com
```

### `rejected` 或 `fetch first`

远程分支比本地新。不要强制推送，先执行：

```bash
git pull --rebase origin main
```

如果出现冲突，停止操作并根据冲突文件逐个处理。

### 提交了不该上传的文件

如果还没有 `push`，不要删除原始数据，先用以下命令仅从暂存区移除：

```bash
git restore --staged 文件路径
```

如果已经推送了密码、Token 或私钥，必须立刻在对应平台撤销/轮换该凭据；仅删除文件不能消除 Git 历史中的泄露。

## 八、安全原则

- 仓库保持 `Private`。
- 不向聊天、Issue、提交记录或代码写入密码、Token、验证码和 SSH 私钥。
- 不使用 `git push --force`，除非先评估并明确批准历史重写。
- 上传前始终看一遍 `git status` 和 `git diff --cached`。
- GitHub 只备份源码和文档，不是模型、数据集和巡检结果的备份系统。
