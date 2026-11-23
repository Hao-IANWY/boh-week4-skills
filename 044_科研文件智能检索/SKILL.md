---
name: research-file-retrieval
description: 在真实文件库（目录扫描、PDF/DOCX/TXT/侧车提取、别名与版本组）上做确定性检索：词表驱动的跨语言语义对齐、硬过滤不放宽、章节/页码/段落级回链自检，恢复索引只出只读建议并留哈希对照证据。
---

# 科研文件智能检索（research-file-retrieval）

文件已经在库里，缺的不是"能搜"，而是"搜到的位置就是原文那一段、过滤条件没被悄悄放宽、别人拿记录能复核到同一处"。本 skill 把这件事做成一条可复算的流水线：**盘点 → 切页 → 分块 → 去重/版本组 → 硬过滤 → 检索打分（含词表扩展）→ 回链自检 → 恢复/索引建议（只读）**。

## 何时使用

- 用户给出一个真实文件库（库目录路径或带路径的文件清单，含副本、旧版本、扫描件、DOCX、PDF）并提出**指向某一段**的问题，例如"这个体系在哪个项目做过""当时的缓冲液配方写在哪一版哪一页"；
- 需要按 年份 / 项目号 / 文件类型 / 批次 / 版本 / 权限 / 是否扫描件 做**硬过滤**，条件不满足时必须如实报告而不是放宽；
- 需要**跨文件语义对齐**：中文提问命中英文 PDF、缩写（PCR/CTD/PAM）命中全称、上下位词命中（eDNA ↔ metabarcoding）；
- 需要给出**可逐字验证**的定位：文件路径 + 哈希 + 版本 + 印刷页与物理页 + 章节路径 + 行号 + 字符区间 + 段落首句 + 原文片段。

不适用：开放网络文献检索（属文献检索课题）、单篇论文全文精读与综述写作、存储层权限治理（本 skill 只识别并如实标记无权限文件，绝不绕过）。

## 输入

| 位置 | 内容 |
|---|---|
| `/input/case.json` | `case_id`、`task_instruction`、`output_contract`，以及下列任一材料形态 + `queries[]`（或 `questions`/`asks`） |
| **文件库模式** | `library_path` / `materials_root` / `library`（字符串路径或 `{"root": ...}`）→ 递归扫描目录，逐文件 SHA-256 + 大小 + 类型 + 读权限；`files[]` 条目可按 `path` 对上库内文件补充元数据（year/project_id/version/permission/scanned/batch） |
| **内联模式** | `files[]`（或 `documents`/`materials`/`corpus`）直接给正文：`pages[]`（`page`/`page_label`/`text`）或 `text` |
| 管理性版本说明 | `version_notes[]` / `admin_notes[]` / `versions[]`：`{match, doc, version, note}`，按路径后缀归一匹配 |
| `task.md` | 任务说明；`queries` 缺失时从正文首段兜底生成 1 条提问 |

字段名不统一由 `first_present()` 依次尝试别名，无需改代码。

## 文本获取链（逐文件记录 extraction_method / 字节哈希 / 字符数）

1. 内联正文（`inline_case_json`）；
2. 侧车 `.txt` / `.ocr.txt`（可带 `.ocr.json` 置信度，低于 0.85 给降级警告，`sidecar_ocr_text`/`sidecar_text`）；
3. 自研 stdlib PDF 文本层提取（`pdf_textlayer`，`scripts/pdf_min.py`：Flate 流解压、Tm/Tj 定位、x 间距双栏聚类与阅读顺序、`/Encrypt` 拒绝）；
4. DOCX 纯 stdlib zip+xml 段落读取（`docx_xml`）；
5. 纯文本直读（`plain_text`）；
6. 全部失败 → 诚实 gap，**分码不静默**：`R_FILE_ENCRYPTED`（加密）、`R_FILE_CORRUPT`（损坏/截断）、`R_NO_TEXT_LAYER`（扫描件无文本层）、`R_FILE_NO_EXTRACTOR`（未知格式）、`R_FILE_NO_PERMISSION`（无读权限，绝不绕过）。

## 流程（8 步，每步留痕）

1. **盘点**：登记 `file_id / path / 扩展名 / 大小 / 元数据 / 读权限`；问题文件记码列名，不进入语料、不得记为"已检索"。
2. **切页**：`pages[]` 与 `[[page:N|label]]` 标记优先；物理页（`page`）与印刷页（`page_label`）分开记，缺标签写 `label_source="absent"`，**不拿物理页冒充印刷页**。
3. **分块**：按标题树（markdown / 第X章 / 第X节 / 数字编号 / 标准章节名）+ 定长窗口切 chunk，保留 `section_path`、页内行号区间、规范文本字符区间、段落首句、双栏标记。
4. **去重与版本**：同 SHA-256 异名归并为单一证据源（`alias_of`，canonical 优先非 copy/backup/latest 命名）；版本说明/元数据/文件名模式归版本组，canonical=版本序最大者（final>v(n)>draft）。命中非 canonical 版本给 `stale_version` 提示、命中文件的同哈希副本给 `duplicate_copy` 提示，**副本与旧版不作独立命中计数**。
5. **硬过滤**：`rules/filter_dictionary.json` 规定每个过滤字段的取值来源与比较方式；逐文件逐条判定，**元数据缺失即视为不满足**；相关但被判出的命中单列 `filtered_but_relevant`，绝不放宽。
6. **检索打分**：BM25（文档侧保留词频）+ 概念级 `term_match`（原始词命中率与扩展组覆盖率取均值，跨语言对齐不被表面词元稀释）+ 邻近窗口 + 标题命中，四因子加权（权重、窗口、阈值全在 `rules/retrieval_rules.json`）。查询扩展由 `query_expansion` 词表驱动：词表任一词是查询子串或其词元被查询覆盖即触发该组，组内词元进 BM25 词元集合并按概念计覆盖；每个扩展词带 `via=expansion:<group>` 出处，**逐条可解释，不引入向量库**。
7. **回链自检**：每条命中输出片段，并在规范化文本里逐字复核（`quote_check.exact_match`）；双栏页附阅读顺序提示；复核失败即降级为不确定。
8. **恢复/索引建议（只读）**：按工单生成索引（文件清单+哈希清单）与恢复建议写入 `ops_log`（同时落盘 `ops_log.json` 到输出目录），**全部为建议、绝不执行**；流程前后逐文件 SHA-256 对照，证明输入库未被改写。

## 输出（`result.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id` / `skill` / `generated_at` | `case_id` 与输入逐字一致 |
| 主结果 | `matches[]`（每问一组命中）、`query_views[]`（问题视角）、`gaps[]`、`excluded_files[]`、`library_report`、`ops_log`、`query_plan` | 每条命中含 `rank`、`score`、`score_breakdown`、`matched_keys`（含扩展出处）、`covered_groups`、`file{path,sha256,version,project_id,year,extraction_method}`、`location{page,page_label,label_source,section_path,line_from,line_to,char_range,two_column_hint,read_order}`、`paragraph_first_sentence`、`quote`、`quote_check`、`version_hint` |
| 证据记录 | `evidence.index` / `retrieval` / `filters` / `dedupe` / `unreadable` / `library`（含只读核验）/ `ops_log` / `canonical_text` / `run_log` / `contract` | 索引参数、chunk 边界表、检索 trace（含扩展词与触发组）、过滤判定表、别名组/版本组、逐文件提取方式与字符数 |
| 状态说明 | `status.overall`（complete/partial/empty/input_error）、`status.completed`、`status.unmet`、`status.notes`、`uncertainty[]` | 缺口逐条列明，不补造 |

`gaps[]` 理由码：`R_FILTER_BLOCKED_HITS`（相关命中被硬过滤拦下，见 `filtered_but_relevant`）、`R_FILTERS_EXCLUDE_ALL`、`R_NO_HIT_IN_SCOPE`（附 `coverage_note`：区分"可检索语料内无支撑"与"部分文件无权限/加密/损坏不可判定"）。

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 库目录声明但不存在 | 字符串路径记 `unmet`；`{"root":...}` 装饰字段解析失败静默回退内联模式 |
| 无文件库 | `matches=[]`、`status.overall=empty`、`unmet=["文件库"]`，注明"空结果≠库内无该内容" |
| 全部文件不可检索 | `status.overall=empty`，逐条给 `R_FILE_*` / `R_DUP_FILE` 理由码 |
| 加密/损坏/无文本层/无权限 | 分码记 `excluded_files` + `evidence.unreadable`，建议人工修复，不伪造文本、不绕过权限 |
| 某问命中为空 | `status.overall=partial` + `gaps[]` 理由码 + `coverage_note`（无支撑 vs 无权限），**不放宽条件** |
| 命中落在副本/旧版 | 副本不入语料；旧版命中给 `stale_version` 提示并写入 `uncertainty`，不冒充 canonical |
| 缺印刷页标签 | `page_label=""`、`label_source="absent"`，并写入 `uncertainty` |
| 契约字段名不在产物中 | 按 `rules/contract_projection.json` 关键词投影并标注 `projection_of`（文件库/操作投影排在证据前）；仍无对应则 `status.unmet` |

## 自查

```bash
cd answer/044_科研文件智能检索
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/o/result.json
python3 -B scripts/test_pipeline.py          # 8 用例：基线 + realtest 4 案例 + 幂等
python3 -B scripts/pdf_min.py                # PDF 写入→读回 round-trip 自检
```

- `validate.py` 必须 PASS（`case_id`、contract 字段、`status`、`evidence`、无 NaN、无占位串）。
- 同一输入跑两次，除 `generated_at` 外逐字节一致（含 `ops_log.json`；已用不同 `PYTHONHASHSEED` 交叉验证）。
- 抽查命中：`quote` 必须是该文件规范文本的逐字子串（`quote_check.exact_match=true`）。
- 抽查任一命中：`score` 按其 `score_breakdown` 与 `rules/retrieval_rules.json` 的权重手算，误差 ≤1e-3。
- 真实文件库仿真：`python3 review/044_realtest/build_cases.py` 构建 4 案例（9 文件基准/版本串味/定位漂移/鲁棒），由 `test_pipeline.py` 全量回归。

## 方法来源

| 借鉴对象 | 借了什么 | 本 skill 的改动 |
|---|---|---|
| `infiniflow/ragflow`（Apache-2.0） | 把"分块 + 位置元数据 + 引用"当一等公民：chunk 带 doc/chunk/position/page 信息；metadata filter 做硬过滤 | 位置字段改为可离线复算的 `page/page_label/section_path/line_from/line_to/char_range`；过滤改为规则表驱动的逐条判定 + 理由码，不满足不放宽 |
| `paperless-ngx`（GPL-3.0，仅借设计） | 台账层用 sha256 + 原始路径 + 标签做"文件与版本"的事实来源 | 只借字段模型；副本判定为字节/正文 SHA-256 相同，版本组按管理性版本说明 + 元数据 + 文件名模式三级归并 |
| `TagStudioDev/TagStudio`（GPL-3.0，仅借判据） | 用哈希区分"副本"与"独立文件" | 副本不进证据池，记 `alias_of`；命中副本时给 `duplicate_copy` 提示防重复计数 |
| `sfrangulov/minirag-mcp`（MIT，仅借思路） | 本地 hybrid 检索：BM25 兜底语义漏召回，适合术语/编号类查询 | 用 `kit.bm25_score` 自实现 + 词表驱动概念扩展（缩写/同义/上下位），去掉向量层以保证纯标准库、可离线、可复算 |
| 本仓库 `047_阅读批注与知识沉淀` | 文件库扫描/哈希清单/别名/版本组/提取链/ops_log 只读证明的工程形态（同赛制真实文件库 workorder） | 检索侧改造为 chunk 级 BM25 + 硬过滤 + 回链自检；锚点校正换为段落首句 + char_range 逐字复核 + 双栏阅读顺序提示 |
| 本仓库 `001_学术文献检索` | `kit.py` 的 `Result/Clock/bm25_score/snippet` 与三段式 result 契约、理由码写法 | 从"题录级排序"改为"chunk 级定位"，新增切页/分块/回链自检/只读 ops 四块 |

授权与许可：以上仓库许可分别为 Apache-2.0 / GPL-3.0 / MIT；本目录**未复制任何一行第三方代码**，仅借鉴字段模型与流程结构，实现全部为本仓库原创（纯标准库）。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd answer/044_科研文件智能检索
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
```

示例输入：5 个合成文件（含 1 份同哈希副本、1 份 v1 旧版、1 个 P-09 项目文件、1 份无文本层扫描件），2 条提问带硬过滤。示例输出（`examples/min_example/expected/result.json`）：

- Q1 命中 3 条：`F1`（canonical v2）第 12 页（印刷页 12）、章节 `3 实验方法 › 3.2 裂解缓冲液配方`、页内第 3–5 行，片段逐字复核通过，同哈希副本 `F2` 已合并并在 `duplicate_copies_of_hits` 给 `duplicate_copy` 提示；`F3`（v1 旧版）带 `stale_version` 提示，版本口径保留；
- Q2 命中 0 条，理由码 `R_FILTER_BLOCKED_HITS`：库内有相关段落，但其文件属 P-09，被 `project_ids=["P-17"]` 硬过滤拦下 —— **不放宽条件凑结果**，被拦候选单列；
- `F5` 记 `R_NO_TEXT_LAYER`（扫描件无文本层），不进证据池；
- `library_report` 给逐文件提取方式与哈希，`ops_log` 给只读建议，`integrity.hashes_unchanged=true`；
- `status.overall = partial`，缺口即 Q2。

示例数据全部为合成样例，不代表任何正式案例的真实结论。
