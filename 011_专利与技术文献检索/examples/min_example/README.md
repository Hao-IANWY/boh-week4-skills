# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例。6 条记录（电池主题授权号/申请公开号/WO OCR 件、1 篇期刊技术文献、
  1 条 mRNA 专利、1 条缺种类码的号码）+ 4 条查询（`Q1`/`Q2` 号码型，`Q3`/`Q4` 主题型）。
- `input/materials/`：随案例的合成材料——`US10123456B2.pdf`（影印件，无侧车 → 走 `pdf_min`
  stdlib 提取）、`WO2022123456A1.ocr.txt`（OCR 侧车，含合并号码边界）、
  `garnet_comparison_review.txt`（对比文献全文，供主题型查询做正文命中）。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（不是手写答案）。

```bash
cd answer/011_专利与技术文献检索
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
diff <(python3 -c "import json;d=json.load(open('/tmp/demo/result.json'));d.pop('generated_at');print(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1))") \
     <(python3 -c "import json;d=json.load(open('examples/min_example/expected/result.json'));d.pop('generated_at');print(json.dumps(d,ensure_ascii=False,sort_keys=True,indent=1))")
```

预期：`validate.py` PASS，`diff` 无输出（幂等）。

示例输出要点：

| 项 | 值 |
|---|---|
| `candidates` | 6 条（P1 0.60 / P2 0.60 / P5 0.50 / P3 0.43 / P6 0.25 / P4 0.23） |
| `patent_families` | 5 族；`P1+P2` 同族（`priority:US62345678`），`scope=observed_not_exhaustive` |
| 号码判定 | `US10123456B2` → `grant`；`US20210123456A1` → `application_publication`；`WO2022123456A1` → `application_publication`（`wo_split=2022/123456`）；`US10234567` → `unknown` + `ambiguous` |
| `legal_status` | 6/6 `unverified`，其中 P1/P3/P5 保留了二手来源的 `observed_value`，每条带 `recheck_path` |
| `claims_table` | P1/P3 权项 `located=true`、`source_ref=inline_field`、quote 为内联文本精确切片且 `ocr_boundary=true`；P1 权项在影印件文本中佐证（`facsimile_corroborated=true`） |
| `evidence_map.facsimiles` | 3 份材料带 SHA-256/字符数/提取方法：`pdf_stdlib_text`（PDF 无侧车）、`ocr_sidecar`（OCR 侧车）、`plain_text`（文献全文） |
| `evidence_map.literature_hits` | `Q3` 对比文献正文命中（如 `solid electrolyte`、`lithium dendrite`），quote 为正文精确切片 + `char_span`，与 records 打分分开呈现 |
| `status.overall` | `partial`（法律状态核验未完成） |

示例数据全部为合成样例，**不代表任何正式案例的真实结论**。
