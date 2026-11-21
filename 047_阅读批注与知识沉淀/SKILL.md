---
name: annotation-knowledge-distiller
description: 把真实文件库（PDF/DOCX/MD/TXT/扫描件）上的高亮、划线、页边批注与手写标记做成可回原文的闭环：扫描目录逐文件 SHA-256 建档，同哈希识别别名/完全重复，按管理性版本说明归组 canonical；文本经侧车/PDF 文本层/DOCX 段落提取；引文逐字校正锚点，失败降级模糊匹配并如实标注；沉淀为带来源定位的笔记，BM25 检索逐题回答并回链原文；恢复操作只出建议，输入库只读留前后哈希对照。
---

# 阅读批注与知识沉淀（annotation-knowledge-distiller）

批注的价值是"这一句为什么重要"，一旦离开原文件就只剩散句。本 skill 保证四件事：**每条批注能回到原文的具体页与字符区间**、**每条笔记论断能指到支撑它的原文片段（含文件哈希、提取方式、版本口径）**、**沉淀结果能被真实问题 BM25 检索到并回链**、**修复真实文件库时只动建议不动文件**（ops_log + 前后哈希对照证明输入只读）。

## 何时使用

- 用户给一个**真实文件库目录**（PDF、DOCX、MD/TXT、扫描件+OCR 侧车，可能含别名/重复文件、跨文件多版本）与批注清单，要求**修复+沉淀+检索**一次闭环；
- 需要盘点资料库：逐文件 SHA-256、识别**同哈希别名/完全重复**、提示**同归一化标题近重复**、按管理性版本说明（`version_notes`/`admin_notes`/`versions` 或 `metadata.version`）划分**版本组并标 canonical**；
- 需要处理锚点错位（声明页≠实际页）、双栏页阅读顺序、OCR 漂移（逐字失败→模糊匹配如实降级）、OCR 低置信字段、加密/损坏文件诚实跳过；
- 需要把批注按 问题/方法/结论/可复用要点/待验证疑点 结构化、在笔记间建立**有依据**的关联并抑制过度连接、对给定检索问题逐条给出**可回链的回答**。

不适用：文献检索与综述写作、逐句精读判断（本 skill 只搬运读者自己的判断）、需要真实解密的加密文件（不做破解，诚实跳过）。

## 输入

1. `/input/case.json` + `task.md`；材料两种形态皆支持，可混合：
   - **文件库模式**：`materials_root` / `library_path` / `library_dir` 等给出库目录（相对路径按 case 目录 → 祖先 `input/` → work → cwd → `/input` 顺序回退解析，全部失败时报告完整尝试路径）；目录递归扫描（跳隐藏文件与侧车伴生件）；
   - **内联模式**：`materials[]`（或 `files`/`documents`/`readings`）直接给 `pages[]`/`text`，兼容旧版行为。
2. 管理性版本说明 `version_notes`（或 `admin_notes`/`versions`）：每条 `{match: 文件名, doc, version, note}`，按路径后缀匹配；版本序 final > v(n) > draft，并列时**非别名副本优先**做 canonical。
3. 批注字段（容错）：`annotation_id`、`file_id`/`path`、`page`、`page_label`、`type`/`kind`、`color`、`quote`/`selected_text`、`comment`/`note`、`position`/`rects`、`tags`。
4. 检索问题 `queries[]`（或 `questions`）：`{query_id, text}`。
5. OCR 侧车：`<f>.ocr.txt`（正文，支持 `[[page:N|label]]` 页标记）+ 可选 `<f>.ocr.json`（`engine`、`mean_confidence`、`low_confidence_spans[]`），驱动 OCR 降级判定。

## 文本获取链（逐文件记录 `extraction_method`，全失败诚实 gap）

内联正文 → 侧车 `.txt`/`.ocr.txt` → `scripts/pdf_min.py` 提取 PDF 文本层（zlib Flate + BT/ET Tj 解析，含双栏阅读顺序聚类；检测 `/Encrypt` 拒绝提取；流解压失败逐条记 error）→ DOCX 用 zip+xml 读段落 → 纯文本直读 → gap 分码：`R_FILE_ENCRYPTED` / `R_FILE_CORRUPT` / `R_FILE_NO_EXTRACTOR` / `R_NO_TEXT_LAYER`。每文件记录 `sha256`（字节级）、大小、`n_chars`、文本哈希、`ocr_confidence`。

## 流程（6 步闭环）

1. **文件库盘点**：扫描 → 哈希清单；同 SHA-256 不同名 → `alias_or_duplicate` 组（canonical 优先非 copy/backup/latest 命名）；同归一化标题不同哈希 → 近重复提示；版本说明/元数据/文件名模式（启发式，标 `basis`）→ 版本组。**全程只读**：扫描时与结束时各取一次哈希，一致才记 `hashes_unchanged=true`。
2. **批注抽取**：类型先归一（`highlight / underline / margin_note / handwritten / sticky_note / region`），表外类型标 `unknown_type` 不并入；颜色按 `rules/annotation_taxonomy.json` 解释语义，表外颜色留空并记 `uncertainty`。
3. **锚点校正（六态）**：引文在提取文本中**逐字定位**（命中后自检 `char_span` 切片=引文）；逐字失败降级 difflib 模糊匹配，覆盖率 ≥`fuzzy_min_ratio` 如实标 `fuzzy_matched`（不作字符级锚点），否则 `not_found`；配合 `verified / page_mismatch / ambiguous / unverifiable`。双栏页给阅读顺序提示；OCR 低置信（平均置信 <`ocr_confidence_floor` 或引文落在 `low_confidence_spans`）→ 降级标注并乘置信系数；批注挂在非 canonical 版本/副本 → `version_confusion_hints`（副本/旧版分 `kind`，旧版引文在新版已改写时强制保留版本口径）。
4. **笔记结构化**：线索词优先在批注文字里找，归入维度且**可多维度**；一个不命中保留为 `excerpt`。每条笔记 `source` 带文件哈希、提取方式、版本与 `doc_key`；`confidence = 锚点权重 × 类型系数 × 引文系数 × OCR 降级系数`（公式见 `rules/distill_rules.json`）。
5. **知识关联**：共享 ≥2 非平凡术语才连边；同引文跨版本/跨副本按 `same_quote_across_versions`/`same_quote_duplicate_file` 合并不重复计数；共享 1 个术语一律抑制并留 `R_LINK_SUPPRESSED` 记录；上限按强度贪心截断。
6. **检索闭环与交接**：BM25（`kit.bm25_score`，文档侧保留词频）召回 top-5，逐问题给**摘录式回答**（只引笔记库内容，不外加结论）+ 原文回链（文件/页/字符区间）；无命中给缺词清单与召回不足说明。恢复操作只写 `ops_log`（合并重复候选、版本归并、补 OCR、缺元数据——全部标注"建议，未执行"）。输出目录写 `handoff_manifest.json` + `handoff_manifest.docx`（office_min 生成、zip 时间戳归一保证逐字节可复现）。

## 输出（`result.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id` / `skill` / `generated_at` | 与输入逐字一致 |
| 主结果 | `annotations[]`、`notes[]`、`links[]`、`suppressed_links[]`、`index`、`lookups`、`orphan_annotations[]` | 批注含 `type_canonical`/`color_meaning`/六态 `anchor`；笔记含 `source.{file_sha256, extraction_method, version, doc_key}`、`ocr_degraded`、`usable`、`confidence` |
| 文件库 | `library_report`、`ops_log` | 哈希清单、别名组、近重复、版本组、版本串味提示、缺口分码、只读完整性核验、恢复建议 |
| 证据记录 | `evidence.materials` / `library` / `handoff` / `type_color` / `anchors` / `notes` / `links` / `retrieval` / `ops_log` / `run_log` / `contract` | 提取链、锚点逐条校正（含 fuzzy 相似度与 char_span 自检）、BM25 参数、检索演示、交接清单哈希 |
| 状态说明 | `status.overall`、`status.completed`、`status.unmet`、`status.notes`、`uncertainty[]` | 锚点缺口、文件缺口（损坏/加密/无文本层）、OCR 降级、版本串味、未知颜色/类型逐条列明 |

`index` 含 `by_aspect / by_tag / by_file（别名归一到规范文件）/ by_page / by_version_group / term_index / adjacency`。空批注时 `annotations=[]`、`status.overall=empty`，文件库盘点与恢复建议照常交付。

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 无批注 | `annotations=[]`、`overall=empty`、`unmet=["批注清单"]`；文件库盘点照常 |
| 库目录不存在 | 按尝试路径逐条报告 `unmet`，不猜内容 |
| 引文逐字+模糊都找不到 | `not_found` + 模糊最高相似度证据，不硬挂位置，笔记 `usable=false` |
| OCR 漂移（模糊命中） | `fuzzy_matched` + 相似度 + 归一化窗口，明示"不作字符级锚点"，状态转 partial |
| 批注页与实际页不一致 | `page_mismatch` + 校正页/字符区间/行区间 + `char_span_checked` |
| 双栏页 | `two_column_hint` + 阅读顺序说明；跨栏引文走模糊匹配并如实标注 |
| OCR 低置信 | `ocr_degraded=true`、confidence×0.8、`usable=false`、进 `uncertainty` |
| 批注挂在旧版本/重复副本 | `version_confusion_hints` 分 `stale_version`/`duplicate_copy`，只提示不迁移 |
| 加密 PDF（`/Encrypt`） | `R_FILE_ENCRYPTED` 诚实跳过，不冒充已解密 |
| 损坏/截断 PDF、坏 DOCX | `R_FILE_CORRUPT`（流级错误逐条保留），诚实跳过 |
| 无文本层且无侧车 | `R_NO_TEXT_LAYER`，坐标沿用批注自带值并声明 |
| 未知扩展名/空文件 | `R_FILE_NO_EXTRACTOR` |
| 表外颜色/类型 | 含义留空、类型不合并，写进 `uncertainty` |
| 契约字段名不在产物中 | 按 `rules/contract_projection.json` 关键词投影并标 `projection_of`；仍无对应则 `unmet` |

## 自查

```bash
# 从仓库根目录运行
python3 -B answer/047_阅读批注与知识沉淀/scripts/test_pipeline.py
python3 answer/_shared/verify.py --only 047
```

最小示例必须满足：`validate.py` PASS、输出与 `examples/min_example/expected/result.json` 一致（忽略 `generated_at`）、两次运行逐字节一致（含交接清单 docx）。realtest 4 案例必须满足：别名组/版本组识别正确、quote 逐字命中且 `char_span_checked=true`、`fuzzy_matched`/`not_found`/`page_mismatch`/`ocr_degraded` 按设计出现、损坏+加密+无文本层+未知格式四类缺口分码诚实跳过、`ops_log` 全部只建议、`integrity.hashes_unchanged=true`、幂等。人工抽查：任一 `verified` 批注的 quote 应能在 `source.path` 对应页正文逐字找到且区间一致。

## 方法来源

| 借鉴对象 | 借了什么 | 本 skill 的改动 |
|---|---|---|
| `0xabu/pdfannots`（MIT，仅借 schema/思路） | 批注三元组「页 / 类型 / 引文」 | 不依赖其解析器；自研 `scripts/pdf_min.py` 纯标准库文本层提取，新增六态锚点与 char_span 自检 |
| `zotero/zotero`（AGPL-3.0，仅借字段模型） | annotation 的 `type`/`color`/`position.rects`/`pageLabel`/`tags` | 类型与颜色语义表化（表外不猜）；`position` 缺失降级字符区间并标注 |
| `hypothesis/client`（仅借思路） | TextQuoteSelector 的"引文锚定"思想 | 逐字→模糊两级匹配，模糊永不冒充逐字 |
| `khoj-ai/khoj`（AGPL-3.0，仅借思路） | "检索→带引用"形态 | BM25 + 摘录式回答 + 原文回链；召回不足给缺词清单，不做向量兜底 |
| 本仓库 `060_科研数据可视化` | 真实输入接入 + 输出字节归一 + 多案例回归模式 | 库目录扫描与哈希只读对照；docx zip 时间戳归一（同 normalize_pdf 思路）；`scripts/test_pipeline.py` 覆盖 4 个真实仿真案例 |

授权与许可：MIT / AGPL-3.0；本目录**未复制任何第三方代码**，实现为纯标准库原创。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd answer/047_阅读批注与知识沉淀
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

示例输入：3 份合成材料（内联模式：正式版 PDF 文本 2 页、预印本网页剪藏、无文本层扫描件）+ 8 条批注 + 2 个检索问题。输出要点：

| 批注 | 锚点结果 | 说明 |
|---|---|---|
| A1 / A2 / A4 | `verified` | 引文逐字命中，`char_span_checked=true`，给页/字符区间/行区间 |
| A3 | `page_mismatch` | 声明第 4 页，实际第 3 页 → 校正页 3 与 `[91,104]` |
| A8 | `not_found` | 引文在原文不存在（原文是「…反应中心的新表示」）→ 不硬挂 |
| A5 | `unverifiable` | 扫描件无文本层，锚点不可校正 |
| A6 | 类型 `unknown_type` | `scribble` 不在类型表 → 不并入高亮 |

- 笔记 8 条（`conclusion / method / reusable / caveat / question`，A5 → `excerpt`）；关联 7 条（含 1 条 `same_quote_across_versions`），7 对抑制/截断留记录；
- `lookups`：BM25 召回 + 摘录式回答（LQ1 命中 N004/N003…，LQ2 命中 N006/N007），回链带文件与页码；
- 输出目录同时产出 `handoff_manifest.json` / `handoff_manifest.docx` 交接清单（内联模式为空库盘点）；
- `status.overall = partial`（1 页不一致 + 1 找不到 + 1 不可校正 + 1 材料无文本层）。

示例数据全部为合成样例，不代表任何正式案例的真实结论；realtest 4 案例的构建与预期见 `review/047_realtest/`（`build_cases.py` 与 `REPORT.md`）。
