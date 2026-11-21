# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例，3 份材料（正式版文本 2 页 / 预印本网页剪藏 / 无文本层扫描件）+ 8 条批注（覆盖 高亮/划线/页边/手写/表外类型；黄绿红蓝橙 + 表外色 purple）+ 2 个检索问题。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（**不是手写答案**）。

```bash
cd answer/047_阅读批注与知识沉淀
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

预期：`validate.py` 输出 `PASS`；重复运行除 `generated_at` 外逐字节一致。

示例输出要点：

| 项 | 值 |
|---|---|
| 锚点 `verified` | A1、A2、A4、A6（A6 类型为表外 `scribble` → `unknown_type`） |
| `page_mismatch` | A3：声明页 4，实际页 3，校正 `char_range=[91,104]` |
| `not_found` | A8：引文「我们提出了一种新的反应中心表示」在原文中不存在 |
| `unverifiable` | A5：扫描件无文本层 |
| 未知颜色 | A4 `purple` → `color_meaning=null`，进 `uncertainty` |
| 笔记 | 8 条，维度 conclusion/method/reusable/caveat/question；A5 → `excerpt` + `R_ASPECT_UNRESOLVED` |
| 关联 | 7 条保留（含 1 条 `same_quote_across_versions`），7 对抑制/截断并留记录 |
| 检索演示 | BM25 + 摘录式回答：LQ1「低数据条件下准确率…」命中 N004/N003…（回链带文件与页码）；LQ2「预印本与正式版的命名差异」命中 N006/N007 |
| 交接清单 | 输出目录另写 `handoff_manifest.json` / `handoff_manifest.docx`（内联模式即空库盘点 + 恢复建议） |
| `status.overall` | `partial`（1 页不一致 + 1 找不到 + 1 不可校正 + 1 材料无文本层） |

示例数据全部为合成，不代表任何正式案例的真实结论。
