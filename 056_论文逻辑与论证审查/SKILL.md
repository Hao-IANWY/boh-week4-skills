---
name: argument-and-logic-review
description: 对论文全文做逻辑与论证审查：stdlib 提取 PDF/DOCX/侧车文本并如实报覆盖率，中英文章节切分与句级索引，审查维度按任务指令动态绑定，产出分型发现（类型/严重度依据/句级 char_span/原文子串校验/可执行修改建议），跨章节一致性检查与对照/假设/转述白名单抑制，截断与扫描件诚实降级。
---

# 论文逻辑与论证审查 Skill（argument-and-logic-review）

把"本文论证较充分"换成**可核对的分型发现**：全文获取（含 PDF/DOCX 回退）→ 章节切分与句级索引 → 审查维度动态绑定 → 主张抽取与来源三分类 → 论证图断裂点 → 跨章节一致性 → 白名单抑制 → 句级问题清单（quote 精确子串校验 + char_span + 可执行建议）。每条结论都能回到原句，每条严重度都带规则化判据。

## 何时使用

- 输入是**完整论文全文**（中英文、txt/md/tex/PDF/DOCX），需要审查论点、证据、推理链、章节衔接四类逻辑问题；
- 任务指令点名了具体审查维度（事实核验/引用支持/方法设计/统计报告/论证链/章节衔接/学术规范），要求"点名才查、不点名不越界"；
- 需要压住三类经典误报：把**有意设置的对照**、**明确标注的假设情形**、**转述他人观点**判成作者自相矛盾；
- 需要严重度分层与可解释依据（影响主结论→major；局部表述→minor；纯建议→suggestion）、句级定位与"改哪句/补什么证据/结论降档"的可执行建议；
- 材料是 PDF 无文本层（自研 stdlib 提取回退）或扫描件（诚实 gap）、长文被截断（覆盖率如实、状态不写 complete）。

不适用：文字润色与语言表达、查重与学术不端判定、参考文献格式、原始数据重分析与统计复算（走 055）、投稿策略。引用语义核验（"所引文献是否支持该句"）离线不可核，只做文内相反证据检查并标注待复核。

## 输入

| 位置 | 内容 |
|---|---|
| `/input/case.json` | `case_id`、`manuscript`（全文内联）或 `materials[]`/`manuscript_path` 指向 txt/md/tex/PDF/DOCX；可选 `review_facets`（结构化工单点名维度）、`truncated`/`original_total_chars`（截断声明）、`figures`/`tables`、`provenance` |
| `task.md` | 任务说明；审查维度也可由指令文本点名（线索词典见 `rules/facets.json`） |
| 章节角色 | 由标题识别（中英文词典 + 编号/罗马/汉字序号）：方法/结果 = 证据章节；摘要/讨论/结论 = 解释章节；未命中的编号子章节从最近非 meta 章节继承角色；参考文献/附录/致谢等 meta 章节不参与检测 |

全文获取链（`scripts/pipeline.py#collect_manuscript`）：内联文本 → 侧车 `.txt`/`.ocr.txt` → 自研 stdlib PDF 提取（`scripts/pdf_min.py`，zlib FlateDecode + BT/ET Tj/TJ，按 `/Length` 精确切流）→ DOCX zip+xml 段落 → 诚实 gap。相对路径按 case 目录、work、`/input` 多基目录解析，全部尝试路径记录在 `evidence.extraction.materials_audit`。

## 流程

1. **全文获取与覆盖率**：每条获取路径记录 `extraction_method` + 源文件 SHA-256 + 审计后文本 SHA-256 + 总字符数；平台声明总字符数时给出 `char_coverage`；截断标记/声明标志/核心章节缺失三种证据判定 `truncated`，如实进 `evidence.coverage`。
2. **章节切分与句级索引**：中英文标题词典 + 编号模式切章（`split_sections`）；段落与句子带全局 `sent_id`、章节、段内序号、`char_span`（指向审计文本的绝对偏移）；切句保护小数点与 `et al.`/`i.e.` 等缩写；句索引整体进入 `sentence_index` 供评审核对。
3. **审查维度动态绑定**（`rules/facets.json`）：`case.review_facets` > 任务指令线索词 > 默认全量（如实记录模式）；未点名维度不执行也不给结论，在 `facets_status` 标 `unchecked` 并写明原因，`overall` 不因维度被裁剪而虚降。
4. **主张抽取与来源三分类**：解释/证据/引言章节的陈述句（线索词 + 统计标记；ASCII 线索词按词边界 + 屈折变体匹配，避免 improve 误中 prove）；`assumption`（hypothesize/assume/推测）→ `reported`（et al/reported that/文献报道）→ `author`。
5. **论证图与断裂点**：`overlap = |主张∩前提| / |主张|`；证据章节→解释章节 ≥0.25 建支持边；解释章节内部双向需 ≥0.45（捕捉循环论证）；含对比词且重合度较低建攻击边；每节点 ≤3 条边。断裂点：`T1` 无支撑入边、`T2` 仅假设支撑、`T3` 支持环（DFS）、`T8` 有攻击边但结论未限定。
6. **跨章节一致性**：`T4` 引言承诺 → 结果章节交付对照表（目标句去句式前缀、内容词过滤后按覆盖率 + ≥3 命中判定交付）；`T9` 因果/机制目标 vs 观察性设计；`T7` 指标数值跨章节冲突（`rules/argument_rules.json.metric_terms`，兼容中英语序）；`T12` 同语境系数方向冲突；`T13` 摘要与结论强度方向；`T14` 方法声明指标结果未报告；`T11` 图表引用与题注/声明对账。
7. **分型发现（typed findings）**：每条 = `finding_id` + `detector`（类型体系外置 rules，8 大类 category）+ `severity`（`rules/severity.json` 三问矩阵 + `severity_basis` 判据串）+ `location`（章节/段/句 id/char_span/locator）+ `quote`（管线内做原文子串校验，`quote_verified`）+ `evidence`（判断依据：命中线索、数值、图结构）+ `revision`（`revision_for`：改哪句/补什么证据/把结论降到什么强度）。
8. **白名单抑制**：候选先过 guard（`G_INTENDED_CONTROL`/`G_EXPLICIT_ASSUMPTION`/`G_REPORTED_VIEW`/`G_LIMITATION_SECTION`，含句内自述局限），命中进 `suppressed` 列表（guard_id、原因、原文引用、句子定位、quote 校验），不出现在 findings——评审质疑"该报未报"时可直接核对。
9. **建议与修改计划**：`fix_plan` 按严重度排序输出逐条动作；禁止"建议加强逻辑"式空话。

## 输出（`result.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id`、`skill`、`generated_at` | 与输入逐字一致 |
| 主结果 | contract 字段 + `研究产物`（`coverage`/`facets_status`/`claims`/`sentence_index`/`argument_graph`/`cross_section_matrix`/`coherence`/`findings`/`suppressed`/`summary`/`fix_plan`） | 契约键按 `rules/contract_routing.json` 路由；带 facet 的规则把 findings 按维度裁剪后绑定 |
| 证据记录 | `evidence.extraction` / `coverage` / `facets_selection` / `facets_status` / `graph_params` / `edges` / `guards` / `design_kind` / `rules_hashes` | 每条边带 overlap 与两端定位；规则文件哈希进证据 |
| 状态说明 | `status` + `uncertainty[]` | 截断/章节缺失/维度未检查/边判定口径全部显式声明 |

`status.overall` 口径：`complete` = 全文覆盖（无截断、引言+方法+结果齐全）且点名维度全部执行；`partial` = 截断、核心章节缺失、点名维度未执行或论证图不足以判定；`empty` = 无可提取文本层（扫描件），不编造结论。

## 异常处理

| 情况 | 行为 |
|---|---|
| PDF/DOCX 二进制 | 先找侧车 `.txt`/`.ocr.txt`；缺失则自研 stdlib 提取（`pdf_stdlib_flate`/`docx_stdlib_xml`），证据注明版面顺序不保证、定位以重建文本偏移为准 |
| 扫描件无文本层 | `status.overall=empty` + `R_NO_TEXT_LAYER`，`unmet` 说明需平台 pdf-text-tool/OCR，不产出任何审查结论 |
| 老版 `.doc`、文件缺失 | `unparsed` 记录原因与全部尝试路径，不猜测内容 |
| 文本截断 | 标记/声明/结构三种证据判 `truncated`，`coverage` 给总字符数与（有声明时）覆盖率，`status` 不写 complete |
| 任务点名未知维度 | `unmet("审查维度")` 列出无法识别的名称，按可识别维度执行 |
| 任务点名维度但零发现 | 契约字段给 `no_findings` 结构（非空、非 not_produced），不冒充未执行 |
| 主张过少/无边/无解释章节 | `unmet` + 降级口径；`uncertainty` 登记"未建边 ≠ 无支撑" |
| 转述/假设/对照/局限 | guard 命中 → `suppressed`（含原因与原文引用），findings 不出现 |
| 引用语义支持被点名 | 只查文内相反证据处理情况，`uncertainty` 声明离线边界 |

## 自查

```bash
# 从仓库根目录运行：结构/预算/可运行/契约/幂等验收门
python3 answer/_shared/verify.py --only 056
# 全量回归：最小示例 + realtest 6 案例（种子命中/抑制/facets 裁剪/截断口径/quote 精确/幂等）
python3 -B answer/056_论文逻辑与论证审查/scripts/test_pipeline.py
```

最小示例必须：`validate.py` PASS、与 `examples/min_example/expected/result.json` 一致（忽略 `generated_at`）、两次运行逐字节幂等。抽查任意 finding：`quote` 是原文精确子串且 `text[char_span[0]:char_span[0]+len(quote)] == quote`；`severity_basis` 能对应 `rules/severity.json.matrix`；被抑制句能在 `suppressed` 里找到 guard 与原因。

## 方法来源

| 借鉴对象 | 借了什么 | 改动点 |
|---|---|---|
| `recap-utr/argument-graph-mining`（Apache-2.0，文本→论证图） | 主张-前提图范式与可复算边 | 边由 token 重合度阈值决定（`rules/argument_rules.json.graph`），节点与边带句级定位串 |
| `UKPLab/acl2017-neural_end2end_am` / `UKPLab/naacl18-multitask_argument_mining` | claim/premise 标签语义 | 判定改为线索词 + 章节角色继承，不训练模型 |
| `allenai/scifact`（SUPPORT/REFUTE/NEI） | 证据强度要匹配表述强度 | 落成 `T5`（证据 hedge vs 结论强断言）与 `T13`（摘要/结论强度方向） |
| SkillHub `claimify`（leegonzales） | 主张来源三分类与隐含假设 | `source_type` + `T2`；转述/假设进白名单 `suppressed` |
| SkillHub `consistency-checker-across-manuscript`（aipoch） | 跨章节一致性矩阵 | 引言承诺-交付对照（内容词过滤 + 命中数下限）、`T14` 方法-结果指标口径、`T11` 图表对账 |
| 本仓库 `answer/055`/`053` | 严重度三问矩阵与理由码口径 | `rules/severity.json` 增加 suggestion 档与 override 规则 |
| 本仓库 `answer/060` realtest 模式 | 真实输入仿真 + 回归门 | `review/056_realtest/build_cases.py` 纯 stdlib 构造 6 案例（含手写最小 PDF writer） |

复算边界：边是词面重合判定，同义改写会漏建边，`T1`（无支撑）须视为"需人工确认的候选"（`uncertainty` 已登记）；承诺交付是内容词覆盖 + 命中数下限的保守判定；PDF 提取是 best-effort 文本层扫描，不做字距/列版面/CID 还原。循环论证只报"支持图存在环"，不推断作者主观意图。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd answer/056_论文逻辑与论证审查
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo056
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo056/result.json
```

示例为**合成**稿件（横断面关联研究，故意埋入：循环论证、承诺未交付、强度越界、相关当因果、数值不一致、因果目标 vs 观察性设计）。示例输出要点（基线见 `examples/min_example/expected/result.json`）：

- 主张节点 9 / 支持攻击边 11 / 循环论证 1；
- 10 条分型发现全部 major：`T3` 循环论证、`T4` 承诺未交付（成本效益，覆盖率 0.1）、`T5` 强度越界 ×2（"证明了"）、`T6` 相关当因果 ×3（横断面设计 + 改善/提升）、`T7` 准确率 0.82 vs 78%、`T9` 因果目标 vs 观察性设计 ×2；
- 来源分类 author 8 / reported 1（转述既往研究未被判作者矛盾）；1 条命中 `G_LIMITATION_SECTION`（句内自述"无法排除反向因果"）进 `suppressed`；
- `status.overall=complete`，全量维度 executed（示例任务未点名维度，默认全量并在 `evidence.facets_selection` 记录）。

示例数据全部为合成样例，**不代表任何正式案例的真实结论**。
