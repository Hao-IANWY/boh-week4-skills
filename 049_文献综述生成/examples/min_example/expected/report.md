# 文献综述｜EXAMPLE-049-NOT-A-TEST-CASE

- **案例标识**：`EXAMPLE-049-NOT-A-TEST-CASE`
- **Skill**：`literature-review-writer` v0.1.0
- **运行状态**：`partial`
- **生成时间**：2026-11-23T18:33:58+0800

> 本报告由 Skill 的确定性管线从 `result.json` 投影生成；结论、引文与定位均取自同一批结构化字段，不在此处新增主张。

## 摘要

- 材料与筛选：筛查 9 条，纳入 8 条，排除 1 条。
- 证据台账：27 条带定位的逐字证据。
- 冲突证据：1 组（双列呈现）；研究空白：0 项。
- 主要主题：accuracy×top-1；retrosynthesis；single-step×uspto-50k×reaction；benchmark×splits

## 1. 综述范围与主题

| 主题 | 标签 | 支撑记录 | 证据强度 | 证据薄弱 |
|---|---|---|---|---|
| T01 | accuracy×top-1 | 6 | contested | 否 |
| T02 | retrosynthesis | 6 | established | 否 |
| T03 | single-step×uspto-50k×reaction | 6 | contested | 否 |
| T04 | benchmark×splits | 3 | established | 否 |
| T05 | data×low | 3 | established | 否 |
| T06 | model | 3 | established | 否 |
| T07 | evaluation | 2 | established | 否 |
| T08 | template-based | 2 | established | 否 |

## 2. 检索与筛选

- **协议状态**：`missing_fallback_declared`
- **执行顺序**：先落盘协议与计划（plan.json），后对每条记录逐条筛选并记 criterion_hits，再执行 DOI/标题+年份去重

| 计数项 | 值 |
|---|---|
| screened | 9 |
| included | 8 |
| excluded | 1 |
| excluded_duplicate | 1 |
| unscored_no_body | 0 |

### 逐条筛选记录（前 60 条）

| 记录 | 题名 | 年份 | 结论 | 理由 |
|---|---|---|---|---|
| P1 | Retrosynthesis Prediction with Graph Neural Networks and Message Passing | 2021 | included | 满足全部纳入标准 |
| P2 | Retrosynthesis Prediction with Graph Neural Networks and Message Passing | 2020 | excluded | R_DUP_DOI：与保留记录 P1 归一后重复，保留首见，不进入主题聚类 |
| P3 | Template-free retrosynthesis via graph attention | 2022 | included | 满足全部纳入标准 |
| P4 | Low-data retrosynthesis: how much training data do we need | 2023 | included | 满足全部纳入标准 |
| P5 | Benchmarking graph neural networks for reaction yield prediction | 2023 | included | 满足全部纳入标准 |
| P6 | Re-examining single-step retrosynthesis benchmarks | 2024 | included | 满足全部纳入标准 |
| P7 | Multi-step route planning with learned heuristics | 2024 | included | 满足全部纳入标准 |
| P8 | Deep learning for retrosynthesis: a survey | 2024 | included | 满足全部纳入标准 |
| P9 | Catalytic C-H activation with earth-abundant metals | 2023 | included | 满足全部纳入标准 |

## 3. 综述草稿

## 纳入范围与筛选口径
筛选 9 条：纳入 8，排除 1（其中去重 1 条），正文不可读降级 0 条；协议缺失，按声明回退口径筛选（每条记录 fallback=true）。主题生成方式：evidence_clustering。

## accuracy×top-1
Message passing over the molecular graph yields reaction centre candidates and reaches 52.3% top-1 accuracy on USPTO-50k. [E001] A template-free model reaches 48.1% top-1 accuracy on USPTO-50k and removes template construction entirely, while template-based baselines remain competitive in low data settings. [E002] Under low data conditions (under 500 training reactions) top-1 accuracy drops by 12 percentage points, and the representation learned on the reaction centre generalizes poorly across reaction classes. [E003] A benchmark with three dataset splits shows that reported top-1 accuracy differences are largely explained by split choice rather than architecture. [E004] We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, which indicates evaluation leakage between splits. [E005] Multi-step route planning is evaluated by route success rate rather than top-1 accuracy, so single-step benchmark numbers are not directly comparable. [E006]

## retrosynthesis
We present a graph neural network with a learned reaction centre representation for single-step retrosynthesis. [E007] A survey covering template-based and template-free retrosynthesis methods and their evaluation practice. [E008] An experimental study on catalytic C-H activation with no computational retrosynthesis modelling. [E009]

## single-step×uspto-50k×reaction
本节证据均已在其他小节逐字引用，不重复列出；交叉引用：E010、E011、E012、E013、E014。

## benchmark×splits
本节证据均已在其他小节逐字引用，不重复列出；交叉引用：E015、E016、E017。

## data×low
本节证据均已在其他小节逐字引用，不重复列出；交叉引用：E018、E019、E020。

## model
本节证据均已在其他小节逐字引用，不重复列出；交叉引用：E021、E022、E023。

## evaluation
本节证据均已在其他小节逐字引用，不重复列出；交叉引用：E024、E025。

## template-based
本节证据均已在其他小节逐字引用，不重复列出；交叉引用：E026、E027。

## 分歧与冲突证据
一方面，Message passing over the molecular graph yields reaction centre candidates and reaches 52.3% top-1 accuracy on USPTO-50k. [E001]（方向 None，强度词 无）；另一方面，We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, which indicates evaluation leakage between splits. [E005]（方向 None，强度词 无）。同主题同单位数值不一致：52.3% vs 49.0%（共享术语 ['accuracy', 'top-1']）。（同证据对也出现在主题 T03）

## 综述覆盖范围声明
覆盖声明：本轮综述基于池内快照（9 条记录 / 0 份来源文件，筛选后纳入 8 条），覆盖主题：accuracy×top-1（6 条证据）、retrosynthesis（3 条证据）、single-step×uspto-50k×reaction（5 条证据）、benchmark×splits（3 条证据）、data×low（3 条证据）、model（3 条证据）、evaluation（2 条证据）、template-based（2 条证据）。未覆盖的声明主题：无；未进入任何主题聚类的记录：无。语料边界：本综述只代表池内快照的证据分布，不代表领域全局文献；未覆盖项只声明为语料边界内的证据缺口，不外推为领域结论。

### 覆盖边界

覆盖声明：本轮综述基于池内快照（9 条记录 / 0 份来源文件，筛选后纳入 8 条），覆盖主题：accuracy×top-1（6 条证据）、retrosynthesis（3 条证据）、single-step×uspto-50k×reaction（5 条证据）、benchmark×splits（3 条证据）、data×low（3 条证据）、model（3 条证据）、evaluation（2 条证据）、template-based（2 条证据）。未覆盖的声明主题：无；未进入任何主题聚类的记录：无。语料边界：本综述只代表池内快照的证据分布，不代表领域全局文献；未覆盖项只声明为语料边界内的证据缺口，不外推为领域结论。

## 4. 冲突证据（双列呈现）

| 编号 | 类型 | 一侧证据 | 另一侧证据 | 处置 |
|---|---|---|---|---|
| C01 | numeric_conflict | E001：Message passing over the molecular graph yields reaction centre candidates and reaches 52.3% top-1 accuracy on USPTO-50k. | E005：We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, which indicates evaluation leakage be… | 并列双列呈现（side_a/side_b 各带逐字引文），不择一写入结论；数值口径差异写进研究空白或限制说明 |

冲突不取一方、不合并为平滑叙事；两侧原文与定位均保留在证据台账中。

## 5. 研究空白

给定材料与检索范围内未形成零证据或弱证据空白条目；该结论不外推到材料之外的文献空间。

## 6. 证据台账（节选）

每条证据行保留记录标识、来源文件、定位与逐字引文；完整台账见 `result.json`。

| 证据 | 记录 | 年份 | 来源 | 定位 | 原文引文 |
|---|---|---|---|---|---|
| E001 | P1 | 2021 | inline | 112:233 | Message passing over the molecular graph yields reaction centre candidates and reaches 52.3% top-1 accuracy on USPTO-50k. |
| E002 | P3 | 2022 | inline | 0:179 | A template-free model reaches 48.1% top-1 accuracy on USPTO-50k and removes template construction entirely, while template-based baselines remain competitive in low data settings. |
| E003 | P4 | 2023 | inline | 0:200 | Under low data conditions (under 500 training reactions) top-1 accuracy drops by 12 percentage points, and the representation learned on the reaction centre generalizes poorly acr… |
| E004 | P5 | 2023 | inline | 0:148 | A benchmark with three dataset splits shows that reported top-1 accuracy differences are largely explained by split choice rather than architecture. |
| E005 | P6 | 2024 | inline | 0:152 | We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, which indicates evaluation leakage between splits. |
| E006 | P7 | 2024 | inline | 0:150 | Multi-step route planning is evaluated by route success rate rather than top-1 accuracy, so single-step benchmark numbers are not directly comparable. |
| E007 | P1 | 2021 | inline | 0:111 | We present a graph neural network with a learned reaction centre representation for single-step retrosynthesis. |
| E008 | P8 | 2024 | inline | 0:104 | A survey covering template-based and template-free retrosynthesis methods and their evaluation practice. |
| E009 | P9 | 2023 | inline | 0:97 | An experimental study on catalytic C-H activation with no computational retrosynthesis modelling. |
| E010 | P1 | 2021 | inline | 112:233 | Message passing over the molecular graph yields reaction centre candidates and reaches 52.3% top-1 accuracy on USPTO-50k. |
| E011 | P3 | 2022 | inline | 0:179 | A template-free model reaches 48.1% top-1 accuracy on USPTO-50k and removes template construction entirely, while template-based baselines remain competitive in low data settings. |
| E012 | P4 | 2023 | inline | 0:200 | Under low data conditions (under 500 training reactions) top-1 accuracy drops by 12 percentage points, and the representation learned on the reaction centre generalizes poorly acr… |
| E013 | P6 | 2024 | inline | 0:152 | We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, which indicates evaluation leakage between splits. |
| E014 | P7 | 2024 | inline | 0:150 | Multi-step route planning is evaluated by route success rate rather than top-1 accuracy, so single-step benchmark numbers are not directly comparable. |
| E015 | P5 | 2023 | inline | 0:148 | A benchmark with three dataset splits shows that reported top-1 accuracy differences are largely explained by split choice rather than architecture. |
| E016 | P6 | 2024 | inline | 0:152 | We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, which indicates evaluation leakage between splits. |
| E017 | P7 | 2024 | inline | 0:150 | Multi-step route planning is evaluated by route success rate rather than top-1 accuracy, so single-step benchmark numbers are not directly comparable. |
| E018 | P3 | 2022 | inline | 0:179 | A template-free model reaches 48.1% top-1 accuracy on USPTO-50k and removes template construction entirely, while template-based baselines remain competitive in low data settings. |
| E019 | P4 | 2023 | inline | 0:200 | Under low data conditions (under 500 training reactions) top-1 accuracy drops by 12 percentage points, and the representation learned on the reaction centre generalizes poorly acr… |
| E020 | P5 | 2023 | inline | 0:148 | A benchmark with three dataset splits shows that reported top-1 accuracy differences are largely explained by split choice rather than architecture. |
| E021 | P3 | 2022 | inline | 0:179 | A template-free model reaches 48.1% top-1 accuracy on USPTO-50k and removes template construction entirely, while template-based baselines remain competitive in low data settings. |
| E022 | P6 | 2024 | inline | 0:152 | We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, which indicates evaluation leakage between splits. |
| E023 | P9 | 2023 | inline | 0:97 | An experimental study on catalytic C-H activation with no computational retrosynthesis modelling. |
| E024 | P6 | 2024 | inline | 0:152 | We reproduce the message passing model on USPTO-50k and obtain 49.0% top-1 accuracy instead of 52.3%, which indicates evaluation leakage between splits. |
| E025 | P8 | 2024 | inline | 0:104 | A survey covering template-based and template-free retrosynthesis methods and their evaluation practice. |
| E026 | P3 | 2022 | inline | 0:179 | A template-free model reaches 48.1% top-1 accuracy on USPTO-50k and removes template construction entirely, while template-based baselines remain competitive in low data settings. |
| E027 | P8 | 2024 | inline | 0:104 | A survey covering template-based and template-free retrosynthesis methods and their evaluation practice. |

## 7. 状态、局限与不确定性

- **总体状态**：`partial`
- **已完成**：双结构摄入；前瞻协议落盘；逐条筛选与去重；证据聚类主题；证据表构建；冲突双列分析；覆盖声明与综述草稿
- **未完成/缺口**：
  - 筛选协议：R_PROTOCOL_MISSING — case.json 未提供前瞻性筛选协议（纳排标准、停止条件）；已按声明回退口径筛选（fallback_screening），不冒充已按协议筛选
- T01 存在numeric_conflict，已双列呈现（E001 × E005，conflict_id=C01）
- 存在未完成项，最终状态由 complete 校正为 partial

**不确定性登记**

| 条目 | 说明 |
|---|---|
| 冲突:C01 | 同主题同单位数值不一致：52.3% vs 49.0%（共享术语 ['accuracy', 'top-1']）；双方口径差异未仲裁，以双列并列交付 |

## 8. 复现信息

- 来源文件清单：3 项；工具调用留痕：72 条。
- 回链覆盖率：见 result.json；未解析引用：—。
- 计划与协议：`plan.json`（先于筛选落盘，可由第三方按 mtime 与内容复核）。
- 机器可读结果：`result.json`（含全部契约字段、证据定位与状态）。
