# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例，27 条题录（固态电解质界面工程 2019–2025），其中 1 条为同题预印本（演示标题级去重）、1 条无任何年份字段（演示缺失记账）；无前瞻协议字段（演示 `R_PROTOCOL_MISSING` + 声明回退口径）。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（不是手写答案）。
- `expected/report.md`：由 `scripts/render_report.py` 从同一份 `result.json` 投影生成的人类可读研究产物，与机器契约同源；正式运行时写到 `/app/output/report.md`。

```bash
cd answer/051_研究前沿与趋势分析
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
diff <(python3 -c "import json;d=json.load(open('/tmp/demo/result.json'));d.pop('generated_at');print(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1))") \
     <(python3 -c "import json;d=json.load(open('examples/min_example/expected/result.json'));d.pop('generated_at');print(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1))")
```

预期：`validate.py` PASS，`diff` 无输出（幂等）。

示例输出要点：

| 项 | 值 |
|---|---|
| `status.overall` | `partial`（缺协议 → `R_PROTOCOL_MISSING`，按 `rules/screening_rules.json` 回退口径筛选，26 条记录 `fallback=true` 全部纳入） |
| 年度序列（journal 口径） | 2 / 3 / 4 / 7 / 2 / 2 / 5（2019→2025） |
| `cagr` | 0.165（2019 起 2 篇 → 2025 年 5 篇） |
| 拐点 | 2022 峰值型：两侧水平均值 3.5 → 2.0，相对变化 −0.43（阈值 0.4） |
| 证据桶 | 4 桶（2019-2020 / 2021-2022 / 2023-2024 / 2025-2025，桶宽 2 年） |
| 趋势判定 | 8 主题：4 established、3 flat、1 thin（sustainability 仅 1 条 → 只报计数不写趋势句）；无前沿候选（池内无近桶新起主题） |
| 演化事件 | 13 条：`computation`/`cell_integration` 出现、两组主题合并、`characterization` 等 5 个主题衰退 |
| 代表作 | 9 条，`period_coverage=1.0`，首创与高被引口径并置；趋势主张另带逐字代表记录 |
| 空白 | 覆盖缺口 4 / 未收敛问题 2 / 维度缺口 2，全部 `needs_review` 并附 `upgrade_requires` |
| 词频审计 | 24 个关键词达审计阈值，其中 11 个热度坍缩（raw_hits 高但去重后仅单条记录），登记 `uncertainty` |
| 逐字证据表 | 50 行，`quote_verified` 全部通过 |
| 排除记录 | `R_DUP_TITLE ×1`（预印本让位正式版）、`R_YEAR_MISSING ×1` |

复算方式：任一阶段占比 = `matrix[pid].topics[t].count / matrix[pid].denominator`；任一趋势主张的桶计数 = 该桶 `record_ids` 长度；任一代表记录引文 = 来源字段在 `char_span` 处的逐字子串；rising/established/thin 阈值见 `rules/statistics.json.trend_verdict`；CAGR 公式见 `rules/statistics.json.growth`。

示例数据全部为合成样例，**不代表任何正式案例的真实结论**。
