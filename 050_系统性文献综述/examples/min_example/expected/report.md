# 系统性文献综述报告｜EXAMPLE-050-NOT-A-TEST-CASE

- **案例标识**：`EXAMPLE-050-NOT-A-TEST-CASE`
- **Skill**：`systematic-review-prisma` v0.1.0
- **运行状态**：`complete`
- **生成时间**：2026-11-22T15:44:18+0800

> 本报告由 Skill 的确定性管线从 `result.json` 投影生成；结论、引文与定位均取自同一批结构化字段，不在此处新增主张。

## 摘要

- 证据台账：9 条带定位的逐字证据（校验通过 9/9，年份跨度 2021–2024）。
- 方向分布：正向 0 条、负向 0 条、零效应 0 条、混合 0 条、未判定 9 条。
- 冲突证据：1 组（双列呈现）；研究空白：0 项。
- PRISMA 计数链：识别 8 → 纳入 4；恒等式自检 True。
- 质量分布：high 4 条

> 本摘要只重排 `result.json` 的计数、方向、年份与判定；证据与边界见后续各节，不在此处新增结论。

## 1. 检索式与研究问题

- **研究问题**：图神经网络用于单步逆合成预测时，报告的 top-1 准确率水平如何，低数据条件下的泛化表现如何，不同研究的评测口径是否可比？
- **检索式**：`(逆合成 OR retrosynthesis OR 反应预测 OR reaction prediction OR single-step OR 单步) AND (graph neural network OR 图神经网络 OR gnn OR message passing OR 消息传递 OR deep learning) AND (accuracy OR top-1 OR 准确率 OR yield OR 产率 OR success rate)`
- **数据库/来源**：local-corpus [arxiv], local-corpus [crossref], local-corpus [openreview]；限制：{"databases": ["local-corpus"], "fields": ["title", "abstract", "keywords", "fulltext"], "year_from": 2019, "year_to": 2026, "languages": ["en", "zh"], "document_types": ["journal-article", "conference-paper", "preprint", "review"]}

### PRISMA 计数链

| 阶段 | 计数 |
|---|---|
| identified | 8 |
| duplicates_removed | 1 |
| after_dedup | 7 |
| screened | 7 |
| excluded_title_abstract | 1 |
| assessed_full_text | 6 |
| excluded_full_text | 2 |
| included | 4 |

- 恒等式自检：全部通过（7/7 条）。

## 2. 检索与筛选

- **协议状态**：`registered_prospective`（协议编号 `PROTO-EXAMPLE-050-GNN-RETRO`）
- **判定口径**：两轮筛选（题录级 → 全文级）使用同一套条款表；每条记录评估该阶段全部条款并记 criterion_hits；排除只取规则定义的唯一主因；元数据缺失导致不可判定的条款转人工复核队列，不自动排除；不得在筛选途中放宽或新增条款；重复文献只在识别阶段删除。
### 纳排条款

| 条款 | 阶段 | 判据 | 标签 |
|---|---|---|---|
| C1_YEAR | title_abstract | year_min | 发表年份 ≥ 2019 |
| C2_TYPE | title_abstract | type_in | 文献类型在允许集合内 |
| C3_ABSTRACT | title_abstract | require_abstract | 有摘要 |
| C4_OBJECT | title_abstract | keyword_any | 对象要素命中 |
| C5_FULLTEXT | full_text | require_fulltext | 全文可得 |
| C6_LANG | full_text | language_in | 全文语言在允许集合内 |
| C7_OUTCOME | full_text | outcome_reported | 报告可比结局指标 |

### 逐条两轮筛选（前 60 条）

| 记录 | 题名 | 年份 | 阶段判定 |
|---|---|---|---|
| R1 | Retrosynthesis Prediction with Graph Neural Networks and Message Passing | 2021 | title_abstract:include；full_text:include |
| R3 | Template-free retrosynthesis via graph attention | 2022 | title_abstract:include；full_text:include |
| R4 | Low-data retrosynthesis: how much training data do we need | 2023 | title_abstract:include；full_text:include |
| R5 | Benchmarking graph neural networks for reaction yield prediction | 2023 | title_abstract:include；full_text:exclude |
| R6 | Re-examining single-step retrosynthesis benchmarks | 2024 | title_abstract:include；full_text:include |
| R7 | Multi-step route planning with learned heuristics | 2024 | title_abstract:exclude |
| R8 | Catalytic C-H activation with earth-abundant metals | 2023 | title_abstract:include；full_text:exclude |

## 3. 综合结论

### K01（主题 TH_PERFORMANCE）

We present a graph neural network with a learned reaction centre representation for single-step retrosynthesis and report 52.3% top-1 accuracy on USPTO-50k with three dataset splits. [M001] A template-free model reports 48.1% top-1 accuracy on USPTO-50k and compares against template baselines; we evaluate a single split only. [M002] Under low data conditions (under 500 training reactions) top-1 accuracy drops by 12 percentage points and the learned representation generalizes poorly across reaction classes. [M003]

- 支持强度：`conflicting`；确定性：支撑记录 4 条；质量分布 high 4；moderate 0；low 0；not_assessable 0；证据：M001, M002, M003, M004

### K02（主题 TH_GENERALIZATION）

Under low data conditions (under 500 training reactions) top-1 accuracy drops by 12 percentage points and the learned representation generalizes poorly across reaction classes. [M005]

- 支持强度：`moderate`；确定性：支撑记录 1 条；质量分布 high 1；moderate 0；low 0；not_assessable 0；证据：M005

### K03（主题 TH_EVALUATION）

We present a graph neural network with a learned reaction centre representation for single-step retrosynthesis and report 52.3% top-1 accuracy on USPTO-50k with three dataset splits. [M006] Limitation: no cross-split evaluation, possible leakage cannot be excluded. [M007] We evaluate on three dataset splits and report the accuracy decay curve. [M008]

- 支持强度：`moderate`；确定性：支撑记录 4 条；质量分布 high 4；moderate 0；low 0；not_assessable 0；证据：M006, M007, M008, M009

### 质量评价

纳入研究的总体质量分布：high 4；moderate 0；low 0；not_assessable 0。未报告条目不计入低质量，已在各结论的确定性说明中列明。

## 4. 冲突证据（双列呈现）

| 编号 | 类型 | 一侧证据 | 另一侧证据 | 处置 |
|---|---|---|---|---|
| C01 | numeric_conflict | M001：We present a graph neural network with a learned reaction centre representation for single-step retrosynthesis and report 52.3% top-1 accur… | M004：We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, indicating evaluation leakage between… |  |

冲突不取一方、不合并为平滑叙事；两侧原文与定位均保留在证据台账中。

## 5. 研究空白

给定材料与检索范围内未形成零证据或弱证据空白条目；该结论不外推到材料之外的文献空间。

## 6. 证据台账（节选）

每条证据行保留记录标识、来源文件、定位与逐字引文；完整台账见 `result.json`。

| 证据 | 记录 | 年份 | 来源 | 定位 | 原文引文 |
|---|---|---|---|---|---|
| M001 | R1 | 2021 | inline | 0:182 | We present a graph neural network with a learned reaction centre representation for single-step retrosynthesis and report 52.3% top-1 accuracy on USPTO-50k with three dataset spli… |
| M002 | R3 | 2022 | inline | 0:137 | A template-free model reports 48.1% top-1 accuracy on USPTO-50k and compares against template baselines; we evaluate a single split only. |
| M003 | R4 | 2023 | inline | 0:176 | Under low data conditions (under 500 training reactions) top-1 accuracy drops by 12 percentage points and the learned representation generalizes poorly across reaction classes. |
| M004 | R6 | 2024 | inline | 0:147 | We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, indicating evaluation leakage between splits. |
| M005 | R4 | 2023 | inline | 0:176 | Under low data conditions (under 500 training reactions) top-1 accuracy drops by 12 percentage points and the learned representation generalizes poorly across reaction classes. |
| M006 | R1 | 2021 | inline | 0:182 | We present a graph neural network with a learned reaction centre representation for single-step retrosynthesis and report 52.3% top-1 accuracy on USPTO-50k with three dataset spli… |
| M007 | R3 | 2022 | inline | 40:115 | Limitation: no cross-split evaluation, possible leakage cannot be excluded. |
| M008 | R4 | 2023 | inline | 0:72 | We evaluate on three dataset splits and report the accuracy decay curve. |
| M009 | R6 | 2024 | inline | 0:147 | We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, indicating evaluation leakage between splits. |

## 7. 状态、局限与不确定性

- **总体状态**：`complete`
- **已完成**：前瞻协议落盘；检索式构建；去重与识别计数；两轮纳排筛选（主因唯一）；人工复核边界；质量评价（not_reported 边界）；证据矩阵与冲突双列；PRISMA 计数链与综合结论
- TH_PERFORMANCE 存在数值冲突（M001 × M004，C01），已双列呈现不择一
- 识别阶段删除 1 条重复题录（DOI/标题归一去重）

**不确定性登记**

| 条目 | 说明 |
|---|---|
| 冲突:C01 | 同主题同上下文口径下数值不一致：52.3% vs 49.0%；双方口径差异未仲裁，以双列并列交付 |
| 质量评价受限 | 纳入研究共有 9 项条目因缺字段/缺依据词记 not_reported（不计入低质量）；结论确定性已按证据受限降级，详见 synthesis[].certainty |
| 检索执行时间 | 本轮为离线给定题录集，未记录真实数据库检索时间；正式使用时应写明检索日期与数据库 |

## 8. 复现信息

- 来源文件清单：3 项；工具调用留痕：0 条。
- 回链覆盖率：1.0；未解析引用：—。
- 计划与协议：`plan.json`（先于筛选落盘，可由第三方按 mtime 与内容复核）。
- 机器可读结果：`result.json`（含全部契约字段、证据定位与状态）。
