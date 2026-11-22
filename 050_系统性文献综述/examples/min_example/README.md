# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例，8 条题录（含 1 条同 DOI 预印本 R2、2 条无全文的 R5/R8、2 条无关文献 R7/R8），研究问题覆盖准确率水平 / 低数据泛化 / 评测口径。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（**不是手写答案**）。同次运行还会生成 `prisma_flow.xlsx`（四个工作表）。

```bash
cd answer/050_系统性文献综述
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

预期：`validate.py` 输出 `PASS`；重复运行除 `generated_at` 外逐字节一致。

示例输出要点：

| 项 | 值 |
|---|---|
| 检索式 | 4 个要素块合成 224 字符的布尔表达式；限制：local-corpus / 2019–2026 / en,zh |
| 识别 | 8 条 → 去重 1（R2 `R_DUP_DOI`）→ 7 |
| 题录级筛选 | 排除 1：R7（`C4_OBJECT` 对象要素未命中） |
| 全文级合格性 | 排除 2：R5、R8（`C5_FULLTEXT` 全文不可得） |
| 纳入 | 4：R1、R3、R4、R6；计数闭合 `included_check=true` |
| 质量 | high 3 / moderate 1 / low 0（R6 未显式给研究目标 → 0.8 → moderate） |
| 证据矩阵 | 9 行；span = `record_id#field` + 整句 + `sha256_quote` |
| 冲突 | 1 组 `M001 × M004`（同 USPTO-50k + gnn，52.3% vs 49.0%），跨 `TH_PERFORMANCE` / `TH_EVALUATION` |
| 结论 | K01 conflicting / K02 moderate / K03 conflicting |
| 产物 | `prisma_flow.xlsx`：PRISMA_flow / exclusions / included / quality 四表 |
| `status.overall` | `partial`（存在冲突证据） |

示例数据全部为合成，不代表任何正式案例的真实结论。
