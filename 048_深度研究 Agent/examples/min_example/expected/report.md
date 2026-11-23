# 深度研究报告｜EXAMPLE-048-NOT-A-TEST-CASE

- **案例标识**：`EXAMPLE-048-NOT-A-TEST-CASE`
- **Skill**：`deep-research-agent` v0.1.0
- **运行状态**：`partial`
- **生成时间**：2026-11-20T11:25:42+0800

> 本报告由 Skill 的确定性管线从 `result.json` 投影生成；结论、引文与定位均取自同一批结构化字段，不在此处新增主张。

## 摘要

- 材料与筛选：筛查 6 条，纳入 6 条，排除 0 条。
- 证据台账：7 条带定位的逐字证据。
- 冲突证据：1 组（双列呈现）；研究空白：1 项。
- We report single-step retrosynthesis top-1 accuracy of 52.3% on USPTO-50k using a message passing graph neural network with reaction centre classification. [E001] Template-free single-step retrosynthesis reaches 48.1% t…
- We report single-step retrosynthesis top-1 accuracy of 52.3% on USPTO-50k using a message passing graph neural network with reaction centre classification. [E007]
- Multi-step route planning is evaluated by route success rate rather than top-1 single-step accuracy, so results are not directly comparable across the two settings. [E006]

## 1. 研究问题与拆解

- **研究问题**：消息传递图神经网络在单步逆合成预测中的 top-1 准确率上限受数据限制到什么程度，与多步路线规划的评测口径有何差别，现有基准证据能否支撑其已达上限？
- **拆解来源**：`facet_templates`

| 子问题 | 内容 | 依赖 | 检索键 |
|---|---|---|---|
| S_OBJECT_METRIC | 单步逆合成预测 在 top-1 准确率 上的现有水平是多少 | — | accuracy, retrosynthesis, single-step, top-1, top-1 准确率, top1, yield, 产率, 准确, 准确率, 单步, 单步逆合成预测, 反应, 反应预测, 合成, 应预, 成预, 步逆, 确率, 逆合, 逆合成, 预测 |
| S_METHOD | 消息传递图神经网络 在该任务中的具体贡献与消融结果是什么 | S_OBJECT_METRIC | center, centre, gnn, graph, graph neural network, message, message passing, network, neural, passing, reaction, reaction center, reaction centre, 中心, 传递, 反应, 反应中心, 图神, 图神经网络, 应中, 息传, 消息, 消息传递, 消息传递图神经网络, 神经, 经网, 网络, 递图 |
| S_COMPARISON | 多步路线规划 与单步预测的评测口径差异在哪里 | — | multi-step, planning, route, route planning, versus, 多步, 多步路线规划, 对比, 步路, 线规, 规划, 路线, 路线规划 |
| S_EVIDENCE | 现有证据能否支撑「单步逆合成预测 已达性能上限」的判断 | S_OBJECT_METRIC | benchmark, reproduction, reproduction study, retrosynthesis, single-step, study, 准数, 划分, 单步, 单步逆合成预测, 原始, 原始实验或基准数据, 反应, 反应预测, 合成, 基准, 始实, 实验, 应预, 成预, 或基, 据划, 据集, 数据, 数据划分, 数据集, 步逆, 证据, 逆合, 逆合成, 预测, 验或 |

## 2. 检索与筛选

- **协议状态**：`missing_fallback_declared`
- **执行顺序**：先落盘协议与计划（plan.json），后对每条记录逐条筛选并记 criterion_hits

| 计数项 | 值 |
|---|---|
| screened | 6 |
| included | 6 |
| excluded | 0 |
| unscored_no_body | 0 |

### 逐条筛选记录（前 60 条）

| 记录 | 题名 | 年份 | 结论 | 理由 |
|---|---|---|---|---|
| R1 | Retrosynthesis Prediction with Graph Neural Networks and Message Passing |  | included | 满足全部纳入标准 |
| R2 | Template-free retrosynthesis via graph attention |  | included | 满足全部纳入标准 |
| R3 | Low-data retrosynthesis: how much training data do we need |  | included | 满足全部纳入标准 |
| R4 | Multi-step route planning with learned heuristics |  | included | 满足全部纳入标准 |
| R6 | Re-examining single-step retrosynthesis benchmarks |  | included | 满足全部纳入标准 |
| R5 | Deep learning for retrosynthesis: a survey |  | included | 满足全部纳入标准 |

## 3. 结论与证据

### K01（S_OBJECT_METRIC）

We report single-step retrosynthesis top-1 accuracy of 52.3% on USPTO-50k using a message passing graph neural network with reaction centre classification. [E001] Template-free single-step retrosynthesis reaches 48.1% top-1 accuracy on USPTO-50k; [E002] Under low data conditions (under 500 training reactions) single-step top-1 accuracy drops by 12 percentage points, which limits the achievable accuracy of message passing models. [E003]

- 支持强度：`conflicting`；证据：E001, E002, E003, E004, E005；冲突：C01
- 适用边界：结论强度按 evidence 的 key_coverage 与数量给定，不外推到未检索材料

### K02（S_METHOD）

We report single-step retrosynthesis top-1 accuracy of 52.3% on USPTO-50k using a message passing graph neural network with reaction centre classification. [E007]

- 支持强度：`moderate`；证据：E007；冲突：—
- 适用边界：结论强度按 evidence 的 key_coverage 与数量给定，不外推到未检索材料

### K03（S_COMPARISON）

Multi-step route planning is evaluated by route success rate rather than top-1 single-step accuracy, so results are not directly comparable across the two settings. [E006]

- 支持强度：`moderate`；证据：E006；冲突：—
- 适用边界：结论强度按 evidence 的 key_coverage 与数量给定，不外推到未检索材料

### K04（S_EVIDENCE）

- 支持强度：`no_evidence`；证据：—；冲突：—
- 适用边界：该子问题在现有语料内没有达阈值证据，不写结论

## 4. 冲突证据（双列呈现）

| 编号 | 类型 | 一侧证据 | 另一侧证据 | 处置 |
|---|---|---|---|---|
| C01 | numeric_conflict | E001：We report single-step retrosynthesis top-1 accuracy of 52.3% on USPTO-50k using a message passing graph neural network with reaction centre… | E004：We reproduce the message passing model on USPTO-50k and obtain single-step top-1 accuracy of 49.0%, lower than the originally reported valu… | 并列呈现，不择一写入结论；差异写进研究空白或限制说明 |

冲突不取一方、不合并为平滑叙事；两侧原文与定位均保留在证据台账中。

## 5. 研究空白

| 子问题/主题 | 类型 | 缺口内容 | 对应检索计划 |
|---|---|---|---|
| S_EVIDENCE | R_NO_EVIDENCE | 需要覆盖多个数据集/多种划分的复现研究，单篇结果不足以下结论 | P04 |

空白只从“零证据/仅弱证据”的检索缺口推导，不把“未检索到”写成“不存在”。

## 6. 证据台账（节选）

每条证据行保留记录标识、来源文件、定位与逐字引文；完整台账见 `result.json`。

| 证据 | 记录 | 年份 | 来源 | 定位 | 原文引文 |
|---|---|---|---|---|---|
| E001 | R1 | 2021 | inline | 0:155 | We report single-step retrosynthesis top-1 accuracy of 52.3% on USPTO-50k using a message passing graph neural network with reaction centre classification. |
| E002 | R2 | 2022 | inline | 0:83 | Template-free single-step retrosynthesis reaches 48.1% top-1 accuracy on USPTO-50k; |
| E003 | R3 | 2023 | inline | 0:178 | Under low data conditions (under 500 training reactions) single-step top-1 accuracy drops by 12 percentage points, which limits the achievable accuracy of message passing models. |
| E004 | R6 | 2023 | inline | 0:176 | We reproduce the message passing model on USPTO-50k and obtain single-step top-1 accuracy of 49.0%, lower than the originally reported value, which suggests evaluation leakage. |
| E005 | R4 | 2024 | inline | 0:164 | Multi-step route planning is evaluated by route success rate rather than top-1 single-step accuracy, so results are not directly comparable across the two settings. |
| E006 | R4 | 2024 | inline | 0:164 | Multi-step route planning is evaluated by route success rate rather than top-1 single-step accuracy, so results are not directly comparable across the two settings. |
| E007 | R1 | 2021 | inline | 0:155 | We report single-step retrosynthesis top-1 accuracy of 52.3% on USPTO-50k using a message passing graph neural network with reaction centre classification. |

## 7. 状态、局限与不确定性

- **总体状态**：`partial`
- **已完成**：问题拆解；前瞻协议落盘；逐条筛选留痕；检索计划；多步执行与记录；证据表构建；结论回链；冲突与空白分析
- **未完成/缺口**：
  - 筛选协议：R_PROTOCOL_MISSING — case.json 未提供前瞻性筛选/检索协议（纳排标准、检索顺序、停止条件）；已按声明回退口径筛选（fallback_screening），不冒充已按协议筛选
  - 子问题缺口:S_EVIDENCE：R_NO_EVIDENCE — 子问题「现有证据能否支撑「单步逆合成预测 已达性能上限」的判断」在给定语料内无达阈值证据
- S_OBJECT_METRIC 存在numeric_conflict，已双列呈现（E001 × E004，conflict_id=C01）

**不确定性登记**

| 条目 | 说明 |
|---|---|
| 冲突:C01 | 同一数据集与同一方法口径下指标不一致：52.3% vs 49.0%；双方口径差异未仲裁，以双列并列交付 |

## 8. 复现信息

- 来源文件清单：3 项；工具调用留痕：30 条。
- 回链覆盖率：1.0；未解析引用：—。
- 计划与协议：`plan.json`（先于筛选落盘，可由第三方按 mtime 与内容复核）。
- 机器可读结果：`result.json`（含全部契约字段、证据定位与状态）。
