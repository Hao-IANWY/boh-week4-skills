# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例。5 个文件的迷你资料库（1 份 md 正文含 2 页、1 份同哈希副本、1 份 v1 旧版、1 个 P-09 项目文件、1 份无文本层扫描件）+ 2 条带硬过滤的提问。F1/F2/F3 带同一 `doc_id`，构成一个版本组（canonical=v2）。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（**不是手写答案**）。

```bash
cd answer/044_科研文件智能检索
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

预期：`validate.py` 输出 `PASS`；重复运行除 `generated_at` 外逐字节一致（含 `ops_log.json`）。

示例输出要点：

| 项 | 值 |
|---|---|
| `status.overall` | `partial`（Q2 无命中，缺口已声明） |
| Q1 命中 | 3 条：F1（canonical v2，第 12 页印刷页 12，章节 `3 实验方法 › 3.2 裂解缓冲液配方`，页内第 3–5 行）、F3（v1 旧版，带 `stale_version` 提示）、F1 次相关 chunk |
| Q1 首选 `quote_check.exact_match` | `true`（在规范化文本中逐字复核通过） |
| 副本处置 | `F2` → `R_DUP_FILE`，`alias_of=F1`，不进证据池；Q1 的 `duplicate_copies_of_hits` 给 `duplicate_copy` 提示，不作独立计数 |
| 版本组 | `裂解缓冲液体系` 组 canonical=v2（F1），F3(v1) 命中带 `stale_version`，写入 `evidence.dedupe.version_groups` |
| Q2 命中 | 0 条，`gaps[0].code = R_FILTER_BLOCKED_HITS`（库内有相关段落，但文件属 P-09 被项目过滤拦下，被拦候选单列） |
| 无文本层 | `F5`（扫描件 pdf）→ `R_NO_TEXT_LAYER`，并在 `unmet`/`evidence.unreadable` 列名 |
| 库报告 | `library_report`：逐文件提取方式/文本哈希/字符数；`ops_log`：只读建议；`integrity.hashes_unchanged=true` |

示例数据全部为合成，不代表任何正式案例的真实结论。
