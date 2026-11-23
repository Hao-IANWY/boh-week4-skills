---
name: research-kb-qa
description: 在真实文件库或内联知识库上做带引用问答：目录扫描逐文件 SHA-256/提取建档 → BM25 检索 → 只抽原文整句作断言并逐句挂文件/版本/页/行/字符区间回链；副本别名合并、版本组 canonical 归一、冲突并列、库内无据判 insufficient_evidence 并说明缺什么，恢复/索引操作只出只读建议。
---

# 科研知识库问答（research-kb-qa）

答案不是"读起来通顺的一段话"，而是**一串能回指原文的断言**：每句都来自库内整句原文、挂在具体文件与版本的具体页与字符区间上；库内没有依据时给 `insufficient_evidence`，并写清缺哪些词、缺哪类文件。支持两种材料形态：**文件库模式**（case 声明 `materials_root` 等库目录，逐文件扫描建档）与**内联模式**（case 直接给 `files[]` 正文）。

## 何时使用

- 用户在一个含重复副本与历史版本的多格式真实资料库（PDF/DOCX/MD/TXT/扫描件+OCR 侧车）上提问，要求**答案可逐句回链**；
- 需要区分"有据 / 证据冲突 / 库内无据"三态，并对无据问题说明缺什么；
- 需要把同一问题的不同版本、不同副本、互相矛盾的证据**并列呈现**而不是择一；
- 工单要求恢复/索引操作（合并重复、版本归档、索引重建）时：只出建议与依据，**绝不改写输入库**，并用前后哈希对照留证。

不适用：开放网络检索、单篇论文精读与综述写作、跨库元数据核对。

## 输入

| 位置 | 内容 |
|---|---|
| `/input/case.json` | `case_id`、`task_instruction`、`output_contract`、材料（**文件库模式**：`materials_root`/`library_path` 等目录字符串；**内联模式**：`files[]`（或 `documents`/`knowledge_base`/`materials`）正文）、`questions[]`（或 `asks`/`queries`）、可选 `version_notes`/`admin_notes`/`versions`（管理性版本说明） |
| `task.md` | 任务说明；`questions` 缺失时从正文首句兜底生成 1 条问题 |
| 文件条目字段（容错） | `file_id`、`path`、`file_type`、`metadata{year,version,project_id,permission,batch}`、正文 `pages[]` / `text` |
| 问题字段（容错） | `question_id`、`text`/`question`、可选 `keys`（关键词）与 `scope` |

文件库模式的文本获取链（逐文件记录 `extraction_method` + 字符数 + 文本哈希）：内联正文 → 侧车 `.txt`/`.ocr.txt`（可带 `.ocr.json` 置信度）→ `scripts/pdf_min.py` 提取 PDF 文本层 → 纯标准库 zip+xml 读 DOCX 段落 → 纯文本直读 → 诚实 gap。权限不足 / 加密 / 损坏 / 无文本层 / 无提取器分别记码，**绝不静默跳过**。

## 流程（6 步）

1. **文件库盘点与建库**：目录扫描（跳过隐藏/侧车文件）→ 逐文件 SHA-256+大小+类型+权限 → 切页（物理页 + 印刷页标签）→ 分块（4 行窗口 + 标题树）→ 台账。无文本层文件记 gap 码，不当作已检索。
2. **证据源归一**：同 SHA-256 异名 → `alias_of` 合并（canonical 优先非 copy/backup/latest 命名）；管理性版本说明/元数据/文件名模式 → 版本组，`canonical`=版本序最大者（final>v(n)>draft）。chunk 正文归一哈希相同者只保留一个来源（canonical 文件 > 非别名 > 年份新 > 版本高），其余记 `R_DUP_CONTENT`——**副本与旧版不重复计独立证据**。
3. **检索取块**：`chunk_score = 0.6·(bm25/max_bm25) + 0.4·term_match`（BM25 词频保留，k1=1.2、b=0.75），取分数与词命中双阈值以上的前 K 块。
4. **取句分级**：只从候选块抽**原文整句**（逐字，英文句号只在后接空白/大写/结尾时切分，1.0 mL 不被切碎）；按问题关键词的 **IDF 覆盖率**给 `direct / indirect / background`，并设双闸门：`idf_coverage ≥ 0.5` **或**（命中问题中最稀有的一批关键词 且 `idf_coverage ≥ 0.3`）。两闸都不满足的句子只作检索证据，不进答案。
5. **仲裁与不确定性分级**：同一主题（共享 ≥1 关键词）的两条断言若出现**同单位不同数值** → `numeric_conflict`；极性相反 → `polarity_conflict`。两条都列出、都不删、都不替作者择一；跨版本证据给 `superseded=true`（版本新旧按管理性版本序，不按字符串序）；命中副本/旧版给 `duplicate_copy`/`stale_version` 提示。每题输出不确定性三态：**`supported`**（全部作答断言 direct、来自 canonical、关键词全覆盖）/ **`partially_supported`**（冲突、含间接证据、证据来自副本/旧版或关键词未全覆盖，原因写进 `uncertainty.reason`）/ **`insufficient_evidence`**（无达闸证据，不借常识补答，不做向量兜底）。
6. **缺证声明与只读操作**：无达闸断言 → `insufficient_evidence`，给 `unmatched_keys`、`keys_present_elsewhere`、`best_partial_overlap` 与 `what_is_missing`，并列出未检索的不可读文件。恢复/索引操作（合并重复、版本归档、修复缺口、以 canonical 为键重建索引）只写建议与依据到 `ops_log`（`input_readonly=true`），绝不改写输入库；流程结束时逐文件重算 SHA-256 与扫描时对照（`evidence.library.integrity`，权限不足文件列 `unchecked`，不冒充已验证）。

## 输出（`result.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id` / `skill` / `generated_at` | 与输入逐字一致 |
| 主结果 | `answers[]`、`insufficient[]`、`conflicts[]`、`coverage[]` | 每题含 `verdict`（answered/conflicting/insufficient）、`answer_text`（原文整句 + `[A#]` 标记拼接）、`citations[]`、`claims[]`、`conflicts[]`、`version_flags[]`、`evidence_hints[]`（duplicate_copy/stale_version）、`uncertainty{level,reason}`、`missing` |
| 文件库盘点 | `library_report` | 模式/根目录、逐文件哈希+大小+提取方式+缺口、别名组、版本组（canonical）、近重复提示、gap 清单 |
| 恢复操作 | `ops_log` | 只建议的合并/归档/修复/索引重建动作，全部 `input_readonly=true` |
| 证据记录 | `evidence.kb` / `library` / `retrieval` / `citation_rules` / `coverage` / `uncertainty_policy` / `run_log` / `contract` | 台账、chunk 表、检索 trace、去重、前后哈希对照、引用规则 |
| 状态说明 | `status.overall`、`status.completed`、`status.unmet`、`status.notes`、`uncertainty[]` | 缺证、冲突、不可读逐条列明 |

每条 `claim.citation` 含 `file_id/path/sha256/file_sha256/size/extraction_method/alias_of/doc_key/version/version_basis/evidence_origin/page/page_label/label_source/section_path/line_from/line_to/char_range`；`answer_text` 只是这些整句加标记的拼接，**不做改写式发挥**。

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 无文件或无问题 | `answers=[]`、`status.overall=empty`，分别声明缺库或缺问题 |
| 全部文件无文本层 | `empty` + `unmet`，不假装检索过 |
| 权限不足 / PDF 加密 / 文件损坏 / 无提取器 | 分别记 `R_NO_PERMISSION` / `R_FILE_ENCRYPTED` / `R_FILE_CORRUPT` / `R_FILE_NO_EXTRACTOR`：诚实跳过、列名、进 `ops_log` 修复建议；哈希列入 `unchecked` |
| 某问题无达闸证据 | `insufficient_evidence` + 缺证明细（含未检索文件清单）；`status.overall=partial` |
| 证据互相矛盾 | `conflicting` + 双方并列 + 版本/口径说明；不写成"尚存争议"了事 |
| 问题关键词在库内从未出现 | 该词进 `unmatched_keys`，并在 `what_is_missing` 写明需要补哪类文件 |
| 契约字段名不在本流程产物中 | 按 `rules/contract_projection.json` 投影并标注 `projection_of`（文件库/操作类字段排在证据类之前）；仍无对应则 `unmet` |

## 自查

```bash
cd answer/046_科研知识库问答
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/o/result.json
python3 -B scripts/test_pipeline.py   # min_example 基线 + realtest 4 案例回归（8 用例）
```

- `validate.py` 必须 PASS；两次运行除 `generated_at` 外逐字节一致（已用不同输出目录验证）。
- 抽查任一条 `claim.text`：必须是其 `citation.chunk_id` 对应 chunk 正文的逐字子串，且落在 `char_range` 切片内。
- 抽查 `answer_text`：去掉 `[A#]` 标记后应等于所引 claim 文本的拼接，不得出现未挂引用的句子。
- 抽查 `conflicting` 的题：`conflicts[].claims` 的两个 ID 都要出现在 `used_claims` 里。
- 文件库模式下跑完逐文件重算 SHA-256：必须与扫描时一致（`evidence.library.integrity.hashes_unchanged=true`）。

## 方法来源

| 借鉴对象 | 借了什么 | 本 skill 的改动 |
|---|---|---|
| `run-llama/llama_index`（MIT，仅借 schema） | `CitationQueryEngine` 的引用结构：source node id + 片段 + 元数据；node postprocessor 剔除重复/旧版本 | 引用结构落地为 `claims[].citation`（文件+双哈希+版本+页+行+字符区间+提取方式+evidence_origin）；重复剔除改为"chunk 归一哈希 + canonical 优先"的确定性规则 |
| `khoj-ai/khoj`（AGPL-3.0，仅借行为） | 检索置信不足时的拒答与"未找到相关内容"结构化回执 | 把拒答拆成三态不确定性（supported/partially_supported/insufficient_evidence）+ 缺证明细（缺哪些词、缺哪类文件、哪些文件未检索） |
| `paperless-ngx`（GPL-3.0，仅借字段模型） | 台账字段：sha256、路径、版本、权限 | 仅借字段命名，不引代码；扩展为逐文件提取方式/字符数/文本哈希 |
| 本仓库 `047_阅读批注与知识沉淀` | 文件库扫描、别名/版本组 canonical、纯标准库 PDF 提取器（`pdf_min.py` 整文件复用） | 与问答闭环（BM25+摘录+回链+缺证+ops 只读）组合；无批注流程 |
| 本仓库 `001_学术文献检索` | `kit.py` 的 `Result/Clock/bm25_score/tokens` 与三段式契约 | 从"候选排序"改为"断言级问答 + 冲突仲裁 + 缺证判据" |
| 本仓库 `044_科研文件智能检索` | 切页/分块/位置字段（同一套坐标口径） | 加 IDF 覆盖双闸门与数量邻近冲突检测（同单位不同数值；单位匹配忽略大小写并对齐 norm 文本） |

授权与许可：MIT / AGPL-3.0 / GPL-3.0；本目录**未复制任何第三方代码**，实现为纯标准库原创（`pdf_min.py` 为本仓库 047 题作品内复用）。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd answer/046_科研知识库问答
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
```

示例输入：6 个合成文件（含 1 份同内容副本、1 份旧版本 v1、1 份无文本层扫描件）+ 3 个问题。示例输出：

| 问题 | verdict | 不确定性 | 要点 |
|---|---|---|---|
| Q1 Triton X-100 终浓度 | `conflicting` | partially_supported | v2「1%（v/v）」与 v1「0.5%（v/v）」并列，`numeric_conflict`；A4（v1）标 `superseded=true`，仍保留不删 |
| Q2 共聚焦曝光时间 | `answered` | partially_supported | 「曝光时间设置为 200 ms…」direct，挂到 2024 年参数文件；`毫秒`一词库内未出现 → 覆盖不全已声明 |
| Q3 冻存 DMSO 浓度 | `insufficient` | insufficient_evidence | `unmatched_keys=[dmso, 冻存…]`，最接近句子覆盖不足，不给答案、不用常识补 |

`status.overall = partial`（Q3 缺证）。`library_report.mode = inline`（示例材料内联）；`ops_log` 含 K6 扫描件修复建议与索引重建建议，全部只建议。示例数据全部为合成样例，不代表任何正式案例的真实结论。
