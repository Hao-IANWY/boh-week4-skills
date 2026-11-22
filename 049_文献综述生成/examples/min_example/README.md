# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例，9 条题录（含同 DOI 预印本 P2、无关催化文献 P9、多步路线规划 P7），研究问题覆盖表示学习 / 模板无关 / 低数据泛化 / 评测口径。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（**不是手写答案**）。同次运行还会生成 `review_draft.docx`（可编辑草稿）。

```bash
cd answer/049_文献综述生成
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
python3 -c "from docx import Document" 2>/dev/null || python3 -c "
import sys;sys.path.insert(0,'scripts')
from office_min import read_docx_text
print('docx 段落数:', len(read_docx_text('/tmp/demo/review_draft.docx')))"
```

预期：`validate.py` 输出 `PASS`；重复运行除 `generated_at` 外逐字节一致。

示例输出要点：

| 项 | 值 |
|---|---|
| 筛选 | 纳入 8 / 排除 1（P2 = 同 DOI 的预印本 → `R_DUP_DOI`，`dedupe_of=P1`）；无协议 → 回退口径，逐条 `fallback=true` |
| 协议状态 | `missing_fallback_declared`（`unmet` 记 `R_PROTOCOL_MISSING`，`plan.json` 先落盘） |
| 主题 | 8 个由证据聚类生成（`topic_source=evidence_clustered`，词表 24 项）：`accuracy×top-1`(6) / `retrosynthesis`(6) / `single-step×uspto-50k×reaction`(6) / `benchmark×splits`(3) / `data×low`(3) / `model`(3) / `evaluation`(2) / `template-based`(2) |
| 证据表 | 27 行；引文为原文整句 + `char_span`（管线内精确子串校验全过）+ `sha256_quote` |
| 冲突 | 1 组 `C01`（`numeric_conflict`，E001×E005：同 USPTO-50k + message passing，52.3% vs 49.0%，双列呈现）→ `also_in_topics=["T03"]` |
| 草稿 | 方法节 + 8 个主题节 + 分歧冲突节 + 覆盖声明节；29 句实质句全部挂 `[E###]`，`sentences_without_evidence=[]` |
| 产物 | `plan.json`（先落盘）+ `review_draft.docx`（111 段，回读自检通过） |
| `status.overall` | `partial`（协议缺失） |

示例数据全部为合成，不代表任何正式案例的真实结论。
