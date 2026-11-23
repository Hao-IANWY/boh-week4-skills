# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例，6 个文件（含 1 份同内容副本、1 份 v1 旧版本、1 份无文本层扫描件）+ 3 个问题（分别覆盖 冲突并列 / 有据 / 库内无据）。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（**不是手写答案**）。

```bash
cd answer/046_科研知识库问答
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

预期：`validate.py` 输出 `PASS`；重复运行除 `generated_at` 外逐字节一致。

示例输出要点：

| 问题 | verdict | 不确定性 | 证据与要点 |
|---|---|---|---|
| Q1 Triton X-100 终浓度 | `conflicting` | partially_supported | A1（v2，1% v/v）与 A4（v1，0.5% v/v）并列，`numeric_conflict`（同单位 % 不同值）；A4 标 `superseded=true`（版本序 final>v(n)>draft，不按字符串序） |
| Q2 共聚焦曝光时间 | `answered` | partially_supported | A1 `direct` →「曝光时间设置为 200 ms…」，2024 年参数文件；`毫秒`库内未出现 → 关键词未全覆盖，如实降级声明 |
| Q3 冻存 DMSO 浓度 | `insufficient` | insufficient_evidence | `unmatched_keys = [dmso, 胞冻, 冻存, 存时, 时使]`；最接近句子覆盖不足，只作检索证据，不用常识补答 |

其它：

| 项 | 值 |
|---|---|
| 副本 | K2 与 K1 同内容 → 1 个 chunk 记 `R_DUP_CONTENT`，只保留 K1 来源（canonical 优先） |
| 不可读 | K6（扫描件 pdf，库内无路径可解析）→ `R_NO_TEXT_LAYER`，进 `unmet`/`uncertainty` 与 `ops_log` 修复建议 |
| `library_report.mode` | `inline`（示例材料内联，不走磁盘扫描；仍给别名/版本组与逐文件台账字段） |
| `ops_log` | `repair_file_gap`（K6 补 OCR 建议）+ `rebuild_index`（以 canonical 为键重建索引），全部 `input_readonly=true` |
| `status.overall` | `partial`（Q3 缺证） |
| 引用定位 | `citations[]` 带 file_id/path/sha256/version/version_basis/evidence_origin/page/page_label/section_path/line_from/line_to/char_range/strength |

真实文件库模式（目录扫描 + SHA-256 + PDF/DOCX 自提 + 权限检查 + 只读证明）的端到端样例见 `review/046_realtest/`（4 案例，synthetic 材料）。

示例数据全部为合成，不代表任何正式案例的真实结论。
