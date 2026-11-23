# 最小调用示例（示例，非正式测试案例）

- `input/library/`：**合成**的 6 个可入库文件 + 1 个 0 字节文件：
  - `PROJ-A/2024_raw/exp_run01.csv` 与 `exp_run01 (1).csv` —— **内容完全相同**的别名副本（sha256 一致）；
  - `PROJ-A/2024_analysis/analysis_v1.md` → `analysis_v2.md` —— **同身份不同内容**的版本链；
  - `archive/2023_final_报告.md` —— 另一年份的归档件；
  - `supplement/scan_page.bin` —— 非 UTF-8 字节流；
  - `supplement/empty_placeholder.md` —— 0 字节（应跳过但记录）。
- `input/case.json`：目录模板 `{project}/{year}/{kind}`、命名模板 `{project}_{year}_{kind}_{seq}{ext}`、2 行补充表、3 个请求（检索/重复/版本）。
- `expected/result.json`：由 `scripts/pipeline.py` 真实运行产生，作为回归基线（不是手写答案）。

```bash
cd answer/043_科研资料库与文件管理
rm -rf /tmp/demo && python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

> 复跑前建议清空 `--out` 目录：目标文件已存在且内容相同会记 `noop_already_present`（幂等），内容不同才记 `W_TARGET_EXISTS`。

## 示例输出要点

| 项 | 值 |
|---|---|
| `status.overall` | `partial`（0 字节文件 + 非 UTF-8 提取缺口各一条 issue） |
| `counts` | 源文件 6、入库 6、跳过 1、重复组 1、版本链 1、提取缺口 1、冲突 0 |
| `duplicate_groups` | 规范件 `PROJ-A/2024_raw/exp_run01.csv`，alias `exp_run01 (1).csv`（**同 sha256**） |
| `version_chains` | `analysis_v1.md → analysis_v2.md`，v2 为 latest，带 `lines_added`/`lines_removed` |
| 落库树 | `PROJ-A/2024/raw_data/…_01.csv` + `…_01-alias1.csv`；`PROJ-A/2024/analysis_note/…_01.md`、`…_02.md`；`PROJ-A/2023/analysis_note/…`；`supplement/UNDATED/binary/…` |
| 元数据 | `analysis_v2.md` 由补充表覆盖 `owner/instrument/note`，出处记 `人工给定[PROJ-A/2024_analysis/analysis_v2.md]`；归档件 `project` 按「人工给定 > 目录名解析」取舍（冲突留痕于 `field_conflicts`） |
| `low_fill_rate_files` | `scan_page.bin`（`fill_rate=0.5`，缺 `year`/`title`，提取缺口 `R_FILE_NO_EXTRACTOR`） |
| `requests` | Q1 检索命中 5 条并给分数；Q2 返回重复组；Q3 返回版本链与恢复提示 |
| 归档校验 | `integrity.hashes_unchanged=true`（输入前后全量哈希对照）；`archive_manifest`：`file_count=6`、`total_bytes=767`、`verify.ok=true` |
| ops_log | 全部建议（`input_readonly=true, executed=false`）：入库目标路径、合并重复候选、索引重建、回滚就绪 |
| 回滚 | 全流程只"新建复制"，删 `out/library` 即完全回滚；源库只读、零改动 |

关键点：**重复按哈希判、版本按身份判、跳过有原因码、同名不覆盖、回滚有步骤**。

## 关于示例的 `partial` 状态

示例故意留了 1 个 0 字节文件与非 UTF-8 文件各一条记录，故 `partial`；移除后 `issues` 清空即返回 `complete`。
