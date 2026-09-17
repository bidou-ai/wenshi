# Wenshi 温室表型巡检

Wenshi 当前只面向水稻**株高**和**有效穗数**。现场株高测试登记四排共 32 株，其中 `A`、`B-L`、`B-R` 共 24 株进入自动检测，`C-01..08` 只保留 Tag/setup 记录并明确排除检测；左右通道各八个停车观测组，共 16 点。每点处理主动检测株，每株保留 `left`、`center`、`right` 三视角 RGB-D 证据，自动候选必须经人工复核。

## 当前状态与边界

- `phenotyping.enabled` 为 `false`。32 个 Tag 映射、Tag 实际尺寸与朝向、卡槽到水面高度、16 点路线参数、表型姿态、相机与手眼标定均未完成，预检应拒绝正式表型任务。
- 已有离线配置/调度、单株存储、Tag 适配、株高弧长计算、有效穗复核数据模型和只读后台；正式表型运动适配器尚未完成现场验收。
- 旧 `rice` 识别、倒车、J5 跟随和固定抵近是历史原型，不属于当前表型功能。
- 自动测试不连接或驱动 AGV、JAKA、D435；本次文档更新也没有执行硬件操作。

当前已完成两套第一版 CPU 训练：`rice_plant` 使用 20 张整株图，最终 mAP50 为 0.995；`panicle` 使用 90 张稻穗图（83 张已标注、7 张歧义），最终 mAP50 为 0.499、mAP50-95 为 0.155。由于验证植株少、稻穗仍有漏检/重复框，它们仅用于流程验证和错误分析，尚未达到正式发布条件；模型发布必须按类型明确执行。

## 现行文档

| 类型 | 文档 |
| --- | --- |
| 操作 | [操作手册](docs/操作/操作手册.md) |
| 操作 | [现场验收清单](docs/操作/现场验收清单.md) |
| 操作 | [安全约束](docs/操作/安全约束.md) |
| 操作 | [标签映射与现场确认](docs/操作/标签映射与现场确认.md) |
| 操作 | [株高复核规范](docs/操作/株高复核规范.md) |
| 操作 | [有效穗复核规范](docs/操作/有效穗复核规范.md) |
| 操作 | [标注与模型训练流程](docs/操作/标注与模型训练流程.md) |
| 技术 | [系统架构](docs/技术/系统架构.md) |
| 技术 | [表型数据结构](docs/技术/表型数据结构.md) |
| 技术 | [项目状态](docs/技术/项目状态.md) |
| 技术 | [初步硬件设计](docs/技术/初步硬件设计.md) |
| 技术 | [现场测试记录](docs/技术/现场测试记录.md) |
| 技术 | [标定说明](docs/技术/标定说明.md) |
| 技术 | [项目全面分析报告](docs/技术/项目全面分析报告.md) |
| 技术 | [正式代码引用与整理报告](docs/技术/正式代码引用与整理报告.md) |
| 现场资源 | [现场资源与图片放置](docs/现场资源/README.md) |
| 操作 | [双人协作与错峰调试手册](docs/操作/双人协作与错峰调试手册.md) |

## 安全入口

```bash
./scripts/start_wenshi.sh phenotype --help
./scripts/start_wenshi.sh phenotype --check
./scripts/start_field_test.sh --help
./scripts/start_dashboard.sh --help
./scripts/start_height_test.sh --help
```

## 专家展示模式

专家展示使用独立入口 `./wenshi.sh`：`LM1 -> LM4 -> LM3 -> LM2` 只作为地图闭环转弯参考；每一圈从真实的 `left-01..08`、`right-01..08` 中随机选择 3-5 个停车组，只有选中的水稻停车点才停稳并让 JAKA 执行 `home_safe -> left -> center -> right -> center -> home_safe`。RViz 显示地图、路线标记、AGV 位姿和 D435 画面。它**不运行 YOLO，不计算株高或有效穗，不写 `runtime/runs`，也不连续保存 RGB-D**。

启动前必须存在包含 16 个真实水稻停车点和经过现场确认的 `home_safe`/三视角示教点的 `runtime/height_tests/field_height_setup.json`。不会回退使用只有机械臂点位的旧 `viewpoints.json`：

```bash
./wenshi.sh --dry-run
./wenshi.sh
./wenshi.sh --no-rviz
./wenshi.sh --photos runtime/demo
./wenshi.sh --viewpoints runtime/height_tests/field_height_setup.json
```

进入控制台后可输入 `pause`、`start`、`status`、`photo`、`stop` 或 `q`。只有输入 `photo` 时才在 `runtime/demo/`（或 `--photos` 指定目录）保存一张 JPEG。`--no-camera` 只用于没有 D435 的路线/机械臂展示，会同时禁用拍照。

## 一键株高测试

新工具只写 `runtime/height_tests/`，不启用正式 `phenotyping` 运动流程，也不改写训练集或历史现场证据。现场先完成登记，再运行单站或全路线：

```bash
./scripts/start_height_test.sh setup --interactive
./scripts/start_height_test.sh arm-only --group left-01 --confirm-motion
./scripts/start_height_test.sh full-route --confirm-motion
./scripts/start_height_test.sh replay runtime/height_tests/run_<timestamp>
./scripts/start_height_test.sh report runtime/height_tests/run_<timestamp>
```

`setup --interactive` 会先提示拍摄 ID 0 标定板，再按现场固定表登记 32 个 Tag（A=1..8、B-L=16..9、B-R=17..24、C=32..25），每株回车直接从 D435 保存照片；随后将 AGV 驶到每个真实水稻停车组，停稳后回车读取当前 `x/y/angle` 并保存停车点照片。ID 0 不分配给植株，LM1~LM4 不在 16 个停车组内。

一次运行目录包含 `run.json`、`events.jsonl`、逐株 `plants/<plant_id>/views/<view>/color.jpg`、`depth.png`、`overlay.jpg`、`frame.json`、`detections.json`、`results.json`，以及 `summary.json`、`report.csv`、`report.html`、`report.txt`。报告分开给出 `camera_pass`、`plant_model_pass`、`panicle_model_pass`、`height_feasibility_pass`，任何一层失败都只能标记 `needs_review`。

`phenotype` 仅可进行预检或模拟，当前配置应安全退出。现场教学、路线和机械臂测试是独立流程，不能代替 32 株表型验收；后台只用于查看和导出结果。

## 每日入口

```bash
./yubei/start_yubei.sh daily-check
```

该检查只读验证依赖、数据、训练副本和模型产物，不连接硬件。采集、标注、准备、训练的完整顺序见[标注与模型训练流程](docs/操作/标注与模型训练流程.md)。

## 已有现场证据

`field_test_20260828_034122` 的日志记录了 8/8 教学点保存、6 次教学查询超时后成功、机械臂 8/8 点完成且控制响应为 `errorCode: 0`、首次路线未执行、第二次路线完成 8 段并一次阻挡恢复。完整边界见[现场测试记录](docs/技术/现场测试记录.md)：这些事实不构成 32 株、16 点表型现场验收。

## 验证

```bash
PYTHONPATH=app:. python3 -m pytest -q tests/unit/test_delivery_files.py
PYTHONPATH=app:. python3 -m pytest -q
```
