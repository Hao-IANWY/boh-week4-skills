# 科研选题与创新性评估报告｜EXAMPLE-052-POOL-OBSERVED

- **案例标识**：`EXAMPLE-052-POOL-OBSERVED`
- **Skill**：`research-idea-novelty-assessment` v0.1.0
- **运行状态**：`complete`
- **生成时间**：2026-11-22T15:46:10+0800

> 本报告由 Skill 的确定性管线从 `result.json` 投影生成；结论、引文与定位均取自同一批结构化字段，不在此处新增主张。

## 摘要

- 材料与筛选：筛查 10 条，纳入 9 条，排除 1 条。
- 证据台账：11 条带定位的逐字证据。
- 冲突证据：0 组（双列呈现）；研究空白：0 项。
- D1：pool_observed
- D2：pool_observed

## 1. 候选方向与可检验问题

### D1｜不确定性量化机器学习势函数迁移到硫化物电解质界面

- 对象：硫化物固态电解质界面（grain boundary 与 interphase）；关键变量：界面迁移势垒, 不确定性区间宽度, 外推误差
- 比较基线：bulk 数据训练的机器学习势函数；预期结论：带不确定性量化的迁移势函数在界面区外推误差显著低于 bulk 基线
- 可证伪条件：若界面区外推误差相对基线的改善不显著，则该假设不成立

### D2｜预训练视觉 Transformer 用于聚合物电解质膜裂纹分割

- 对象：聚合物电解质膜中的裂纹扩展；关键变量：裂纹分割 IoU, 标注样本量
- 比较基线：从零训练的 U-Net；预期结论：预训练 Transformer 在小样本条件下的裂纹分割 IoU 高于从零训练基线
- 可证伪条件：若 IoU 相对从零训练基线无显著提升，则迁移假设不成立

## 2. 检索与筛选

- **协议状态**：`registered_prospective`（协议编号 `PROTO-EXAMPLE-052`）
- **执行顺序**：先落盘协议与计划（plan.json），后对每条记录逐条筛选并记 criterion_hits；仅 included 记录进入检索、新颖性比对与前提取证

| 计数项 | 值 |
|---|---|
| screened | 10 |
| included | 9 |
| excluded | 1 |
| unscored_no_body | 0 |

### 逐条筛选记录（前 60 条）

| 记录 | 题名 | 年份 | 结论 | 理由 |
|---|---|---|---|---|
| REC01 | Uncertainty-aware machine learning potentials for sulfide interfaces |  | included | 满足全部纳入标准 |
| REC02 | Transfer learning of potentials from bulk to interfaces |  | included | 满足全部纳入标准 |
| REC03 | Operando characterization of sulfide electrolyte interfaces |  | included | 满足全部纳入标准 |
| REC04 | Convolutional segmentation of grain boundaries in solid electrolytes |  | included | 满足全部纳入标准 |
| REC05 | Deep learning grain boundary detection in ceramics |  | included | 满足全部纳入标准 |
| REC06 | Space charge layers dominate sulfide interface resistance |  | included | 满足全部纳入标准 |
| REC07 | Compositional screening of fast ionic conductors |  | excluded | 未满足纳入标准 IN1 |
| REC08 | Benchmarking uncertainty quantification for atomistic simulations |  | included | 满足全部纳入标准 |
| REC09 | Cryo-EM interphase composition in sulfide electrolytes |  | included | 满足全部纳入标准 |
| REC10 | Open benchmark dataset for solid electrolyte conductivity |  | included | 满足全部纳入标准 |

## 3. 创新性、可行性与风险

### D1｜不确定性量化机器学习势函数迁移到硫化物电解质界面

- **新颖性**：范围 `pool_observed`；判定分布 covered 1；partially_covered 1；not_found_in_scope 0；undetermined 0。新颖性判定限定在本 case 记录池内（pool_observed）；池外是否存在同题工作未核验，本轮未执行全局检索
- **可行性**：6/6 项前提有来源支持；未发现未满足前提
- **价值**：0.6（value = 0.5*gap_signal + 0.3*application_signal + 0.2*method_reuse_signal，三个 signal 均为 0/0.5/1 三档）
- **风险**：0.375；主要条目：主张 D1-C1 已被池内工作覆盖（REC01）；主张 D1-C2 被池内工作部分覆盖

### D2｜预训练视觉 Transformer 用于聚合物电解质膜裂纹分割

- **新颖性**：范围 `pool_observed`；判定分布 covered 0；partially_covered 1；not_found_in_scope 0；undetermined 0。新颖性判定限定在本 case 记录池内（pool_observed）；池外是否存在同题工作未核验，本轮未执行全局检索
- **可行性**：6/6 项前提有来源支持；未发现未满足前提
- **价值**：0.65（value = 0.5*gap_signal + 0.3*application_signal + 0.2*method_reuse_signal，三个 signal 均为 0/0.5/1 三档）
- **风险**：0.125；主要条目：主张 D2-C1 被池内工作部分覆盖

## 4. 冲突证据（双列呈现）

本轮未检出同主题方向或数值冲突；该结论仅表示在给定材料与口径下未检出，不表示不存在冲突。

## 5. 研究空白

给定材料与检索范围内未形成零证据或弱证据空白条目；该结论不外推到材料之外的文献空间。

## 6. 证据台账（节选）

每条证据行保留记录标识、来源文件、定位与逐字引文；完整台账见 `result.json`。

| 证据 | 记录 | 年份 | 来源 | 定位 | 原文引文 |
|---|---|---|---|---|---|
| E001 | REC01 | 2023 | inline | 0:142 | We propose a framework for training machine learning potentials with calibrated uncertainty quantification for sulfide electrolyte interfaces. |
| E002 | REC02 | 2022 | inline | 0:112 | We apply transfer learning of machine learning potentials from bulk training data to interface coating problems. |
| E003 | REC08 | 2024 | inline | 0:102 | A benchmark compares uncertainty quantification methods for atomistic simulations of solid interfaces; |
| E004 | REC04 | 2023 | inline | 0:155 | We apply a convolutional segmentation network to electron microscopy images of grain boundaries in solid electrolytes and report reusable grain statistics. |
| E005 | REC01 | 2023 | inline | 0:142 | We propose a framework for training machine learning potentials with calibrated uncertainty quantification for sulfide electrolyte interfaces. |
| E006 | REC10 | 2025 | inline | 0:138 | We release an open benchmark dataset for solid electrolyte ionic conductivity covering 200 compositions with measured conductivity values. |
| E007 | REC10 | 2025 | inline | 0:138 | We release an open benchmark dataset for solid electrolyte ionic conductivity covering 200 compositions with measured conductivity values. |
| E008 | REC02 | 2022 | inline | 113:217 | The transferred potential is compared against a bulk-trained baseline and reduces interface force error. |
| E009 | REC04 | 2023 | inline | 0:155 | We apply a convolutional segmentation network to electron microscopy images of grain boundaries in solid electrolytes and report reusable grain statistics. |
| E010 | REC10 | 2025 | inline | 0:138 | We release an open benchmark dataset for solid electrolyte ionic conductivity covering 200 compositions with measured conductivity values. |
| E011 | REC02 | 2022 | inline | 113:217 | The transferred potential is compared against a bulk-trained baseline and reduces interface force error. |

## 7. 状态、局限与不确定性

- **总体状态**：`complete`
- **已完成**：来源/记录双结构摄入；候选方向字段化；前瞻协议落盘；逐条筛选留痕；逐方向检索取证；证据表与引文校验；池内新颖性评估（pool_observed）；前提三分源与条件式可行性；冲突双列与风险登记；价值与风险并置；四维评分与决策包；有界研究建议
- 方向 D1 前提 P_DATA 来自池内证据（E007），强度低于任务给定前提
- 方向 D1 前提 P_BASELINE 来自池内证据（E008），强度低于任务给定前提
- 方向 D2 前提 P_DATA 来自池内证据（E010），强度低于任务给定前提
- 方向 D2 前提 P_BASELINE 来自池内证据（E011），强度低于任务给定前提
- 契约字段 研究产物 绑定自产物分区 candidate_assessments
- 契约字段 证据矩阵 绑定自产物分区 evidence_table
- 契约字段 不确定性 绑定自三段式 uncertainty
- 契约字段 状态说明 绑定自三段式 status

**不确定性登记**

| 条目 | 说明 |
|---|---|
| 创新性 | ['D1-C1'] 已被池内工作覆盖：需改述贡献边界，不能按原表述投稿 |
| 文献覆盖 | 未联网：新颖性只在 case 记录池内判定（pool_observed），池外同题工作可能已存在；not_found_in_scope 已附池外核验建议（未执行） |
| 相似度口径 | token 覆盖率对同义改写不敏感；跨语言工作可能被低估相似度 |

## 8. 复现信息

- 来源文件清单：3 项；工具调用留痕：59 条。
- 回链覆盖率：见 result.json；未解析引用：—。
- 计划与协议：`plan.json`（先于筛选落盘，可由第三方按 mtime 与内容复核）。
- 机器可读结果：`result.json`（含全部契约字段、证据定位与状态）。
