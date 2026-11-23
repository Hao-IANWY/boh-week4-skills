# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例，6 篇题录（含复现研究 R6、综述 R5、多步路线规划 R4），研究问题含 对象/方法/指标/限制/比较/证据要求 多要素。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（**不是手写答案**）。同目录运行还会生成 `plan.json`（计划 + 执行记录）。
- `expected/report.md`：由 `scripts/render_report.py` 从同一份 `result.json` 投影生成的人类可读研究产物，与机器契约同源；正式运行时写到 `/app/output/report.md`。

```bash
cd "answer/048_深度研究 Agent"
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

预期：`validate.py` 输出 `PASS`；重复运行除 `generated_at` 外逐字节一致。

示例输出要点：

| 项 | 值 |
|---|---|
| 拆解 | 4 条子问题：`S_OBJECT_METRIC` / `S_METHOD`(依赖前者) / `S_COMPARISON`(独立) / `S_EVIDENCE`(依赖前者) |
| 计划 | `P01`+`P03` 并行组 1，`P02`+`P04` 组 2；每条有表达式、来源、停止条件 |
| 执行 | 4 次调用，命中 4/1/1/0；`absent_keys` 列出语料中不存在的键；`stopped_by=no_new_evidence` |
| 证据表 | 6 条 `direct`，引文为 `abstract` 整句（题名不进引文） |
| 冲突 | `E002 × E004`：同 USPTO-50k + message passing，49.0% vs 52.3% → `numeric_conflict`，并列 |
| 研究空白 | `S_EVIDENCE` 无证据 → `R_NO_EVIDENCE`，`missing_evidence` = 需多数据集复现研究 |
| `status.overall` | `partial`（1 条缺口） |

示例数据全部为合成，不代表任何正式案例的真实结论。
