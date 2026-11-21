# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**稿件（城市绿化与心理健康的横断面研究），故意埋入六类逻辑缺陷：循环论证、引言承诺未交付、结论强度越界、相关当因果（观察性设计 + 因果动词）、跨章节数值不一致、因果目标 vs 观察性设计；另含 1 句转述既往研究（用于验证不会被误判为作者自相矛盾）与 1 句讨论内自述局限（用于验证句内自述不判缺陷）。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（不是手写答案）。

```bash
cd answer/056_论文逻辑与论证审查
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
diff <(python3 -c "import json;d=json.load(open('/tmp/demo/result.json'));d.pop('generated_at');print(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1))") \
     <(python3 -c "import json;d=json.load(open('examples/min_example/expected/result.json'));d.pop('generated_at');print(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1))")
```

预期：`validate.py` PASS，`diff` 无输出（幂等）。

示例输出要点（与基线逐字节一致，忽略 `generated_at`）：

| 项 | 值 |
|---|---|
| `status.overall` | `complete` |
| 覆盖 | `inline_text`，619 字符，19 句 / 6 章节，`truncated=false` |
| 论证图 | 9 主张节点 / 11 条边 / 1 个环（DFS） |
| 分型发现 | 10 条全部 major：T3 循环论证 ×1、T4 承诺未交付 ×1（成本效益，覆盖率 0.1）、T5 强度越界 ×2（"证明了"）、T6 相关当因果 ×3、T7 数值不一致 ×1（准确率 0.82 vs 0.78）、T9 方法无法回答问题 ×2 |
| 来源分类 | author 8 / reported 1 / assumption 0（转述句未按作者主张判缺陷） |
| 抑制 | 1 条：`G_LIMITATION_SECTION`（讨论内"需要注意的是…无法排除反向因果"，句内自述局限） |
| facets | 示例任务未点名维度 → 默认全量 7 维 executed，模式记录在 `evidence.facets_selection` |
| design_kind | `observational`（方法章节"横断面问卷设计"） |

复算方式：

- 边：`overlap = |claim_tokens ∩ premise_tokens| / |claim_tokens|`（`kit.tokens` 归一化后去重）；断裂点按 `edges` 图结构判定（无 supports 入边 / 全为 assumption 入边 / DFS 找环）；
- 交付判定：目标子句去句式前缀（`本研究旨在` 等）→ 内容词（滤英文虚词与中文助词二元组）在**结果章节**的覆盖率 ≥ 0.25 且命中 ≥ 3 个内容词；
- quote：`quote.strip("…")` 必须是稿件文本的精确子串，且 `text[char_span[0]:char_span[0]+len(quote)] == quote`；
- 严重度：`rules/severity.json` 三问矩阵，判据串写入每条 `severity_basis`。

示例数据全部为合成样例，**不代表任何正式案例的真实结论**。真实全文场景（英文 3500+ 词全文、中文全文、facets 定向、PDF/截断/扫描件）见 `../../../../review/056_realtest/` 与 `scripts/test_pipeline.py`。
