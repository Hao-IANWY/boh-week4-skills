---
name: literature-auto-classifier
description: 把真实文件库或批次工单整理成可交接台账：目录扫描逐文件 SHA-256 建档，规则优先判定文献类型与主题标签（逐条可解释），按模板命名并降级标注，字节级别名/正文指纹去重与版本角色归组，批量操作全部带幂等键与回滚建议且绝不改写输入库，损坏/加密/无文本层/权限不足如实跳过并列未处理项。
---

# 文献与资料自动分类（literature-auto-classifier）

把"给一段建议清单"换成**真正能进资料库的整理结果**：每份文件的归属、标签、新文件名、重复与版本关系都有依据；操作有计划、有幂等键、有逆操作；判不出类别的文件单独列出来，而不是硬塞进某个目录。支持**真实文件库模式**：给一个库目录路径（或带路径的文件清单），管线会递归扫描、逐文件哈希、自提 PDF/DOCX 文本层，全程只读输入库。

## 何时使用

- 用户给一个**真实文件库目录**（混杂期刊论文、预印本、会议论文、学位论文、补充材料、数据集、扫描件、伴随元数据 CSV、管理性版本说明），要求**归类入目录 + 打标签 + 按模板改名 + 去重 + 批量整理**；
- 或用户给一批文件的**内联条目**（标题/摘要/正文在 case.json 里），要求同样的整理；
- 要求每份文件的归属与标签**能用原文/元数据核对**（规则命中、术语、字段、位置逐条给出）；缺失字段**如实标注不补造**；
- 要求改名/移动/合并/归档**只出建议、可回滚、可幂等重跑、可续跑**，重复下载与预印本/正式版/修订版/补充材料不能当独立文献，也不能误删误覆盖。

不适用：单篇文献精读、全文翻译、写作润色、文献检索发现（属相邻课题）。

## 输入

| 位置 | 内容 |
|---|---|
| `/input/case.json` | `case_id`、`task_instruction`、`output_contract`、材料（两形态见下） |
| 文件库形态 | `library_path`/`materials_root`/`library_root`（字符串路径，相对 case 目录）→ 递归扫描；`metadata_csv`/`metadata_rows` 伴随元数据表；`version_notes`/`admin_notes`/`versions` 管理性版本说明 |
| 内联形态 | `files[]`（或 `documents`/`items`/`batch`）：`doc_id`、`path`/`name`、`title`、`abstract`、`text`、`keywords`、`metadata{authors,year,venue,doi,type,version}` |
| `task.md` | 任务说明；材料全缺时不产出分类结果，改为 `empty` 并声明缺口 |
| 规则表 | `rules/taxonomy.json`（类别与术语权重）、`rules/tag_vocabulary.json`（标签与同义词）、`rules/classification_rules.json`（阈值/权重/命名模板/理由码） |
| 磁盘正文 | 侧车 `<file>.txt`/`<stem>.txt`/`<file>.ocr.txt` 优先 → `pdf_min` 纯标准库 PDF 文本层 → DOCX zip+xml 段落 → 纯文本直读 → 诚实 gap（逐文件记 `extraction_method`+文本哈希+字符数） |

## 流程（7 步）

1. **盘点**：文件库模式递归扫描库目录（跳过隐藏/软链），逐文件 SHA-256+大小+类型+权限（权限不足记 `R_FILE_NO_PERMISSION`，不静默跳过）；空目录如实记录；伴随元数据表（CSV/JSON/内联行）按 path/name 并入各文件元数据，**该表本身登记为辅助文件、不参与分类**；损坏/加密文件显式记码（`R_FILE_CORRUPT`/`R_FILE_ENCRYPTED`/`R_NO_TEXT_LAYER`/`R_FILE_NO_EXTRACTOR`），进 `unprocessed` 清单附建议处置（可续跑）。
2. **去重**：字节级 SHA-256 相同且异名 → 别名/完全重复（`byte_alias`，canonical 优先非 copy/backup/(1) 命名）；正文（标题+摘要+正文归一）指纹相同 → 同文副本（`text_duplicate`）；同归一化标题但内容不同 → 近重复**仅提示**不合并。
3. **版本**：同 DOI 或「标题强归一+第一作者」聚内容版本族；管理性版本说明/元数据/文件名版本词聚文件版本组。canonical=正式版角色优先、版本序最大（final>v(n)>draft>preprint）、并列非副本优先；**全部保留不删除**，预印本/修订版/补充材料区分保留，归档只写建议。
4. **分类**：`taxonomy.json` 规则优先 —— 元数据命中 + 术语命中（`term_weight × field_weight`），减去 negative 术语；`confidence = raw/(raw+k)` 饱和到 0–1；≥`high_confidence` 记 high，≥`review_confidence` 记 review（低置信，入人工确认队列），否则未分类。
5. **标签**：`tag_vocabulary.json` 同义词归并到规范名（`merged_from` 留痕），每篇最多 `max_tags` 个，上位标签不重复计数，词表外术语不臆造新标签。
6. **命名与目录**：`dir_template` / `name_template` 渲染 → 非法字符替换、超长截断、**缺失字段用 null_token 占位并标 `degraded`**（严禁补造作者/年份/期刊/DOI）；目标名冲突 → 版本/角色/指纹短码消歧（`__token` 后缀，**绝不覆盖**）；与库内既有不同内容文件冲突 → 禁止覆盖、转人工确认队列；乱码文件名（替换字符/latin-1 误解码残迹）识别 → 保留原文件、命名以标题为准、入人工确认队列。
7. **操作**：全部操作**作为建议落 `operations`**（move_rename/archive_duplicate/merge_duplicate_candidates/consolidate_versions/manual_resolve_conflict），每条带依据、幂等键与回滚路径；**文件库模式绝不改写输入库**（`--apply` 一律拒绝 `R_APPLY_READONLY`），前后逐文件 SHA-256 对照写入 `evidence.library.integrity` 证明未动；仅内联模式下显式 `--apply` 才移动工作区真实文件（目标冲突不覆盖）。

## 输出（`result.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id` / `skill` / `generated_at` | `case_id` 与输入逐字一致 |
| 主结果 | `classification[]`、`unclassified[]`、`low_confidence[]`、`manual_review[]`、`unprocessed[]`、`operations[]`、`library_report`、`records_view[]` | 每份文件含 `doc_type{primary,confidence,level,labels[].hits}`、`tags[]`、`directory`、`filename{from,to,degraded,degraded_reasons}`、`duplicate{kind}`、`version{family,primary_doc,role}`、`extraction{method,n_chars,text_sha256}`、`file_sha256` |
| 证据记录 | `evidence.rules` / `inventory` / `library` / `label_evidence` / `tag_normalization` / `naming` / `distribution` / `operations` / `run_log` | 阈值、权重、逐条命中、哈希分组、别名组/版本组/近重复、提取链、**前后哈希对照**、命名降级与冲突消歧、操作日志与逆操作 |
| 状态说明 | `status.overall`、`status.completed`、`status.unmet`、`status.notes`、`uncertainty[]` | 未分类/低置信/不可读/乱码/冲突逐条列明 |

`doc_type.labels[].hits[]` 给 `kind`（metadata/term/negative）、`field`、`term`、`normalized_offsets`、`weight` —— 评审可逐条复核为什么判成这一类；`metadata_rules` 命中（如伴随 CSV 给的 `type=journal-article`）同样逐条留痕。

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 无文件 | `classification=[]`、`status.overall=empty`、`unmet=["分类结果"]` |
| 损坏 / 加密 / 无文本层 / 未知格式 / 权限不足 | 分别记 `R_FILE_CORRUPT` / `R_FILE_ENCRYPTED` / `R_NO_TEXT_LAYER` / `R_FILE_NO_EXTRACTOR` / `R_FILE_NO_PERMISSION`，进 `unprocessed[]` 附建议处置（可续跑），**不伪造文本** |
| 全部类别低于 review 阈值 | 落 `unclassified[]`，给最接近候选与缺失字段，不强行归类 |
| 置信度在 review 档 | 落 `low_confidence[]` 与 `manual_review[]`，需人工确认后再入目录 |
| 命名所需字段缺失 | null_token 占位 + `degraded=true` + `missing_fields` 列名，**不补造** |
| 目标名冲突 | `__版本/角色/指纹短码` 消歧；与库内既有不同内容文件冲突 → 禁止覆盖转人工 |
| 乱码文件名 | `R_FILENAME_GARBLE`：保留原文件，命名以标题为准，入人工确认队列 |
| 副本 / 多版本 | 副本只归档建议不覆盖不删除；版本族/版本组给角色与 canonical，全部保留 |
| `--apply` | 文件库模式一律拒绝（`R_APPLY_READONLY`）；内联模式源缺失/目标冲突/系统错误 → `R_SKIP_MISSING` / `R_SKIP_CONFLICT` / `R_SKIP_OSERROR`，计划留痕可续跑 |

## 自查

```bash
cd answer/045_文献与资料自动分类
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/o/result.json
python3 -B scripts/test_pipeline.py   # 8 用例：基线/幂等/pdf_min 自检 + realtest 4 案例
```

- `validate.py` 必须 PASS；同一输入两次运行除 `generated_at` 外逐字节一致（幂等）。
- 抽查任一份文件：把 `doc_type.labels[0].hits` 的 `field/term` 拿到原文里应能找到；按 `confidence_formula` 手算应等于输出值（误差 ≤1e-3）。
- 抽查命名：`filename.to` 去掉扩展名后长度 ≤ `filename_max_chars`，且不含 `\/:*?"<>|`；`degraded=true` 者必有 `missing_fields`/`degraded_reasons`。
- 抽查去重：同 `fingerprint` 的文件只允许一条 `is_duplicate=false`；别名组 canonical 不得是 (1)/copy/backup 命名。
- 文件库模式核对：`evidence.library.integrity.hashes_unchanged=true` 且 `rechecked=files_scanned`；`operations` 全部 `executed=false`、`input_readonly=true`。
- `unprocessed[]` 与 `library_report.gaps` 一一对应，逐条有 `suggested_action`。

## 方法来源

| 借鉴对象 | 借了什么 | 本 skill 的改动 |
|---|---|---|
| `clusterzx/paperless-ai`（MIT，仅借设计） | 「抽取 → 建议 → 落库」两段式，以及按标题/作者/年份/期刊/DOI/类型抽字段的清单 | 换成规则表驱动的确定性打分（可离线、可复算），落库段强制带逆操作与幂等键 |
| `OpenRefine/OpenRefine`（BSD-3，仅借算法思路） | 标签聚类/同义合并抑制标签爆炸 | 用词表 + 同义归并 + `max_tags` 截断实现，`merged_from` 留痕，不引入其代码 |
| `paperless-ngx`（GPL-3.0，仅借字段模型） | sha256 + 原始路径 + 标签作为台账事实来源 | 仅借字段模型；副本判定分「字节级 SHA-256」与「正文归一指纹」两级 |
| `TagStudioDev/TagStudio`（GPL-3.0，仅借判据） | 用哈希区分副本与独立文件 | 副本 `archive_duplicate`，不覆盖不删除 |
| 本仓库 `047_阅读批注与知识沉淀` | 文件库扫描/别名/版本组 canonical/PDF 自提（`pdf_min.py` 整文件复制）/ops 只读建议/前后哈希对照 | 从「批注沉淀」改为「目录归属 + 标签 + 命名 + 批量整理建议」，新增命名冲突消歧与乱码识别 |

授权与许可：以上仓库许可为 MIT / BSD-3 / GPL-3.0；本目录**未复制任何第三方代码**，实现为纯标准库原创（`pdf_min.py` 为仓库内 047 同名模块的整文件复制）。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd answer/045_文献与资料自动分类
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
# 内联模式才真正执行移动（可选；默认 dry-run，文件库模式拒绝 apply）
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo --apply
# 真实文件库仿真（synthetic，4 案例）：
python3 review/045_realtest/build_cases.py
python3 -B answer/045_文献与资料自动分类/scripts/pipeline.py \
  --case /tmp/boh045_eval/runtime/REAL-045-LIBRARY-BASE/input/case.json \
  --out /tmp/boh045_eval/runtime/REAL-045-LIBRARY-BASE/app/output \
  --work /tmp/boh045_eval/runtime/REAL-045-LIBRARY-BASE/app/work
```

示例输入：9 份合成内联资料（期刊论文 + 同题预印本 + 会议论文 + 数据集 + 补充材料 + 学位论文 + 组会分工记录 + 同文副本 + 无文本层扫描件）。示例输出：

- 7 份 high 置信归类（`journal_article / preprint / conference_paper / dataset / supplementary / thesis`），每份带 `hits[]` 依据；
- 1 份落 `low_confidence`（组会分工记录被 conference 弱信号命中，0.5556，需人工确认）；
- 1 份落 `unclassified`（无文本层扫描件，`best_candidate.category=null`），并进 `unprocessed`；
- 版本族 `doi:10.1000/example.2021.001` 含正式版/预印本/补充材料/副本，`primary_doc=D1`；
- 8 条操作建议（7 move_rename + 1 archive_duplicate），每条带 `idempotency_key` 与 `rollback`；
- `status.overall = partial`（未分类、低置信、不可读三项缺口）。

示例数据全部为合成样例，不代表任何正式案例的真实结论。
