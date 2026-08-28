# Wenshi 温室表型巡检

Wenshi 当前只面向水稻**株高**和**有效穗数**。正式设计为 `A`、`B-L`、`B-R`、`C` 四排各八株，共 32 株；左右通道各八个停车观测组，共 16 点。每点处理两株，每株保留 `left`、`center`、`right` 三视角 RGB-D 证据，自动候选必须经人工复核。

## 当前状态与边界

- `phenotyping.enabled` 为 `false`。32 个 Tag 映射、Tag 实际尺寸与朝向、卡槽到水面高度、16 点路线参数、表型姿态、相机与手眼标定均未完成，预检应拒绝正式表型任务。
- 已有离线配置/调度、单株存储、Tag 适配、株高弧长计算、有效穗复核数据模型和只读后台；正式表型运动适配器尚未完成现场验收。
- 旧 `rice` 识别、倒车、J5 跟随和固定抵近是历史原型，不属于当前表型功能。
- 自动测试不连接或驱动 AGV、JAKA、D435；本次文档更新也没有执行硬件操作。

## 现行文档

| 类型 | 文档 |
| --- | --- |
| 操作 | [操作手册](docs/操作/操作手册.md) |
| 操作 | [现场验收清单](docs/操作/现场验收清单.md) |
| 操作 | [安全约束](docs/操作/安全约束.md) |
| 操作 | [标签映射与现场确认](docs/操作/标签映射与现场确认.md) |
| 操作 | [株高复核规范](docs/操作/株高复核规范.md) |
| 操作 | [有效穗复核规范](docs/操作/有效穗复核规范.md) |
| 技术 | [系统架构](docs/技术/系统架构.md) |
| 技术 | [表型数据结构](docs/技术/表型数据结构.md) |
| 技术 | [项目状态](docs/技术/项目状态.md) |
| 技术 | [初步硬件设计](docs/技术/初步硬件设计.md) |
| 技术 | [现场测试记录](docs/技术/现场测试记录.md) |
| 技术 | [标定说明](docs/技术/标定说明.md) |

## 安全入口

```bash
./scripts/start_wenshi.sh phenotype --help
PYTHONPATH=app:. python3 -m wenshi_patrol.phenotype_controller --check --config config/wenshi.yaml
./scripts/start_field_test.sh --help
./scripts/start_dashboard.sh --help
```

`phenotype` 仅可进行预检或模拟，当前配置应安全退出。现场教学、路线和机械臂测试是独立流程，不能代替 32 株表型验收；后台只用于查看和导出结果。

## 已有现场证据

`field_test_20260828_034122` 的日志记录了 8/8 教学点保存、6 次教学查询超时后成功、机械臂 8/8 点完成且控制响应为 `errorCode: 0`、首次路线未执行、第二次路线完成 8 段并一次阻挡恢复。完整边界见[现场测试记录](docs/技术/现场测试记录.md)：这些事实不构成 32 株、16 点表型现场验收。

## 验证

```bash
PYTHONPATH=app:. python3 -m pytest -q tests/unit/test_delivery_files.py
PYTHONPATH=app:. python3 -m pytest -q
```
