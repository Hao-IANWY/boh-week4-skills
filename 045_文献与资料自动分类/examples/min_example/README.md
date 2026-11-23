# 最小调用示例（示例，非正式测试案例）

- `input/case.json`：**合成**案例，9 份文件（期刊论文、同题预印本、会议论文、数据集、补充材料、学位论文、组会分工记录、同哈希副本、无文本层扫描件），全部正文内联，不依赖磁盘。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（**不是手写答案**）。

```bash
cd answer/045_文献与资料自动分类
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

预期：`validate.py` 输出 `PASS`；重复运行除 `generated_at` 外逐字节一致。

示例输出要点：

| 项 | 值 |
|---|---|
| `status.overall` | `partial`（1 未分类 + 1 低置信 + 1 不可读） |
| high 置信归类 | 7 份：`journal_article`(D1,D8) / `preprint`(D2) / `conference_paper`(D3) / `dataset`(D4) / `supplementary`(D5) / `thesis`(D6)，每份带 `hits[]` 依据 |
| 低置信单列 | D7 `conference_paper` 0.5556（`R_LOW_CONFIDENCE`，入 `manual_review` 需人工确认） |
| 未分类单列 | D9（无文本层扫描件），`best_candidate.category=null`，并进 `unprocessed[]` |
| 版本族 | `doi:10.1000/example.2021.001`：D1 正式版（primary）、D2 预印本、D5 补充材料、D8 副本 |
| 副本 | D8 `is_duplicate=true`、`duplicate_of=D1`、`kind=text_duplicate` → 操作 `archive_duplicate` |
| 操作计划 | 8 条（7 `move_rename` + 1 `archive_duplicate`），每条带 `basis`/`idempotency_key`/`rollback`；内联模式默认 dry-run |
| 命名降级 | D7 缺作者 → `zz-author-...` 且 `degraded=true`；D9 缺标题 → `untitled` |
| 库证据 | 内联模式 `library_report.mode=inline`；文件库模式另见 `evidence.library.integrity`（前后哈希对照） |

`--apply` 只对工作区内真实存在的文件生效；示例材料是内联文本，`--apply` 下会逐条记 `R_SKIP_MISSING` 而不报错，计划仍完整留痕。文件库模式（case 声明 `library_path` 等库目录）下 `--apply` 一律拒绝（`R_APPLY_READONLY`），操作仅为建议。

真实文件库仿真见 `review/045_realtest/build_cases.py`（4 案例：9 文件基准库 / 版本与去重 / 元数据缺口与命名冲突 / 鲁棒性），回归入口 `python3 -B scripts/test_pipeline.py`。

示例数据全部为合成，不代表任何正式案例的真实结论。
