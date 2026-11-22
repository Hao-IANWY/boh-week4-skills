---
name: research-idea-novelty-assessment
description: 把候选研究选题推进成可决策的逐方向评估：可证伪问题陈述＋先验工作五维比较矩阵＋池内新颖性判定（novelty_scope=pool_observed，池内未见≠全球首创，并给池外核验建议）＋前提三分源（task_given／record_evidence 带 evidence_id／explicit_assumption）的条件式可行性＋冲突双列风险与有界建议。协议先落盘、证据逐字可复核、评分外置可复算。适用于开题、选题会、基金预评审前的创新性与可行性判断。
---

# 科研选题与创新性评估 Skill（research-idea-novelty-assessment）

把「这个题目不错/有创新」换成**可复核的逐候选方向评估**：候选方向字段化 → 前瞻协议先落盘 →
逐条筛选 → 逐方向取证（证据逐字可回链）→ 池内新颖性判定（pool_observed）→ 前提三分源的
条件式可行性 → 冲突双列与风险 → 有界建议 → 四维并置评分。每个分数都带原始计数，可按
`rules/scoring.json` 重算；新颖性判定一律限定在 case 记录池内（`rules/novelty_types.json#novelty_scope`）。

## 何时使用

- 有 1-3 个候选研究方向（含对象、变量、基线、预期、可证伪条件），要在给定先验记录池上判断**每个方向是否已被做过、创新属于哪一类、以现有条件能不能做成**；
- 需要区分「创新点被覆盖」「部分覆盖」「本 case 池内未见」三种情况，且不允许把第三种写成「全球首创/尚无人研究」，同时要给池外核验建议；
- 需要把可行性写成**条件式结论**：哪些前提是任务给定、哪些来自池内记录证据（带 `evidence_id` 回链）、哪些是显式假设（「若 P 成立则…」）；
- 证据存在方向冲突时，需要**双列并列**（骑在冲突证据上的方向，风险条目标注两侧逐字引文），而不是择一或写「尚存争议」；
- 研究建议必须**被 constraints 硬约束**：逐条引用约束、不声称完成实验；constraints 缺失时只给方向级建议并声明「无约束可依」。

不适用：替代作者做研究结论、宣称已完成实验、文献综述写作、单篇论文的方法学审查（走 053/055）、
全局完备性或全球首创性声明（本 skill 离线只在池内判定）。

## 输入

| 位置 | 内容 |
|---|---|
| `/input/case.json` | `case_id`、`candidate_directions[]`（1-3 个候选方向：`direction_id`/`title`/`description`/对象·变量·基线·预期·可证伪·方法·数据字段/`claims[]`/`keys[]`；缺省回退 `idea` 单方向）、`sources[]`（来源文件清单，`source_id`+`path`）、`records[]`（10 条级记录，经 `source_path`/`source_id` 指到来源）、`protocol`（前瞻筛选协议：inclusion/exclusion 判据 + 停止条件 + 检索顺序）、`constraints`（数据/算力/周期/团队/伦理/应用等）、可选 `proposal_request` |
| `task.md` | 方向缺失时兜底抽文本 |
| 记录字段（容错） | `title`/`abstract`/`year`/`venue`/`doi`/`type`，可选显式五维 `task`/`method`/`data`/`evaluation`/`conclusion` |

来源正文获取优先级（复用 048 已验证机器）：内联 text → 内联 abstract → 侧车 `.txt`/`.ocr.txt` →
自研 stdlib PDF 提取（`scripts/pdf_min.py`）→ DOCX zip+xml → 文本直读 → 诚实 missing；
逐条记录 `extraction_method` + SHA-256 + 字符数。先验工作未给五维字段时按句式规则抽取，
在 `dimension_sources` 里逐格标注 `explicit / abstract_inferred / title_as_task / missing`——不把推断当原文。

## 流程

1. **摄入**：`sources[]`/`records[]` 双结构建模；正文按上述优先级取数；去重（`R_DUP_TITLE`）。
2. **方向字段化**：显式字段优先，缺项按句式规则抽取并记 `missing_fields`（不猜内容）；每方向取 `claims[]`（缺省按线索句抽取）与检索键 `keys`。
3. **前瞻协议**：纳排标准/检索顺序/停止条件**先写入 `work/plan.json`**，再逐条筛选（每条记录记 `criterion_hits`）；仅 included 记录进入检索、新颖性比对与前提取证；协议缺失记 `R_PROTOCOL_MISSING` + 声明回退口径（`fallback=true`），不冒充「已按协议筛选」。
4. **逐方向取证**：按方向键做 BM25 + idf 覆盖检索（键先做 token 展开，CJK 二元组不退化）；证据表每行 `evidence_id` + `direction_id` + `record_id` + `source_file` + `char_span` + 逐字 quote，管线内做精确子串校验（失败标 `quote_mismatch`）；题名不进引文。
5. **池内新颖性判定（pool_observed）**：每条主张按 `rules/novelty_types.json` 四分类（方法/数据/场景迁移/结论修正，触发词并列或全不命中判 `undetermined`）；`claim_coverage = |主张 token ∩ 记录 token| / |主张 token|`，≥0.5 覆盖、≥0.25 部分覆盖、否则 `not_found_in_scope`——**措辞固定为「池内未见（pool_observed），不构成全球首创判定」**，并附池外核验建议（检索式 + 数据库建议，`executed=false` 声明未执行全局检索）；每条主张给最近邻记录 + 五维逐维重合点（`status`/`shared_terms`）+ 逐字重合引文（回链证据表）。
6. **前提三分源与条件式可行性**：6 条固定前提（`rules/premise_checklist.json`）逐条标 `source=task_given`（case 约束字段）| `record_evidence`（池内证据 cue 命中，**必须带 `evidence_id` 回链证据表**）| `explicit_assumption`（无来源）；结论按 `premise_strength`（1.0/0.7/0.0）降级：全部有来源 → 「在 X 均有来源前提下可行」；有假设 → 「若 P 成立则可行；启动前需先补…」；constraints 缺失 → `unconstrained`，不给「可行」结论。
7. **冲突与风险**：方向词典（`rules/direction_lexicon.json`）对同方向共享键的证据成对比对，`direction_conflict`/`zero_vs_direction` **双列**（side_a/side_b 各带逐字引文）；骑在冲突证据上的方向按 `evidence_conflict` 记风险并标注两侧引文；假设前提、被覆盖主张、部分覆盖分别计权（`rules/scoring.json#risk_weights`）。
8. **有界建议与四维评分**：建议逐条引用 `constraints` 且 `not_claimed`（不声称已完成实验、不越范围）；constraints 缺失只给方向级并声明「无约束可依」；novelty/feasibility/value/risk 四维**并置不合成单一总分**，全部按 `rules/scoring.json` 外置参数线性组合可复算。

## 输出（`result.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id`、`skill`、`generated_at` | 与输入逐字一致 |
| 主结果 | `candidate_assessments[]`（逐方向：problem_statement / novelty / feasibility / value / risk / four_dimension）+ `problem_statement` / `prior_art_matrix` / `novelty_assessment` / `feasibility_assessment` / `value_assessment` / `risk_assessment` / `four_dimension_assessment` / `decision_package` | 每方向带「可以启动的前提 / 启动前必须补齐 / 必须先改的表述 / 必须先复核的冲突」 |
| 证据 | `evidence_table[]`（`use=retrieval/novelty_overlap/premise_evidence`）+ `screening`（criterion_hits 逐条）+ `conflicts`（双列）+ `proposals`（有界建议） | 每条判定可回指主张、记录与来源文件字符区间 |
| 证据记录 | `evidence.sources_manifest` / `protocol` / `execution`（工具调用轨迹）/ `evidence_table_map` / `novelty` / `premises` / `backlink` / `scope` | 回链覆盖率、引文校验计数、可复算口径 |
| 状态说明 | `status`（overall/completed/unmet/notes）+ `uncertainty[]` | 缺口逐条列明；契约字段按 `rules/contract_routing.json` 路由，未命中显式记 `unmet` |

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 不可读 | `status.overall=input_error`，退出码 2 |
| 既无方向又无记录 | `status.overall=empty` + `unmet`，不做推断 |
| 无纳入记录（池空） | 主张判 `undetermined(R_NO_POOL)`，并声明「不得据此声称新颖」 |
| 协议缺失 | `input_gap(R_PROTOCOL_MISSING)` + 声明回退口径，筛选记录标 `fallback=true` |
| 记录正文不可读 | `unscored_no_body` 诚实降级，不参与纳排/比对，不编造内容 |
| 主张过短（<5 token） | 判 `undetermined(R_SHORT_CLAIM)` 并计入 uncertainty |
| 结论修正类主张 | 判 `undetermined(R_DIRECTION)`：方向关系词面判不了，转人工 |
| 前提无来源 | 标 `explicit_assumption` + 条件式降级 + 进风险分，不给「可行」结论 |
| constraints 缺失 | `unconstrained`：不给「可行」结论，建议只到方向级并声明「无约束可依」 |
| 输入含「全球首创/尚无人研究」类表述 | 登记 uncertainty；输出统一改写为「池内未见（pool_observed），不构成全球首创判定」 |
| 证据方向冲突 | 双列呈现（两侧逐字引文），方向风险如实标注，不仲裁不合并 |

## 自查

```bash
cd answer/052_科研选题与创新性评估
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/o/result.json
python3 -B scripts/test_pipeline.py
python3 answer/_shared/verify.py --only 052
```

- `validate.py` PASS；同输入跑两次除 `generated_at` 外逐字节一致（`scripts/test_pipeline.py` 含幂等断言）。
- 抽查任一主张：`(claim_tokens ∩ record_tokens)/|claim_tokens|` 应等于 `coverage.max_similarity`（阈值 0.5/0.25）。
- 抽查前提：`record_evidence` 行的 `evidence_id` 必须能在 `evidence_table` 解析且 `quote_verified=true`；
  `feasibility.score = Σstrength/total`（strength 1.0/0.7/0.0）。
- 抽查新颖性措辞：所有 `not_found_in_scope` 的 `basis` 含「池内未见（pool_observed），不构成全球首创判定」，
  且 `pool_external_check.executed=false`。
- 抽查证据：`quote` 对来源文件（PDF/DOCX 为自研提取文本）按 `char_span` 切片逐字相等；`backlink.unresolved_ids` 为空。

## 最小调用示例（**示例，非正式测试案例**）

```bash
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
```

示例（`examples/min_example/input/case.json`）为**合成**材料：2 个候选方向、前瞻协议、6 项约束、
10 条池内记录。要点：D1-C1（方法）`covered`（最近邻 REC01，重合引文回链）；D1-C2（数据）、
D2-C1（场景迁移）`partially_covered`；P_COMPUTE 等 4 条前提 `task_given`、P_DATA/P_BASELINE
`record_evidence`（带 `evidence_id`）；REC07 被协议 IN1 剔除；`status.overall=complete`。
详见 `examples/min_example/README.md`。4 案例真实仿真（冲突骑乘/跨域零重合/前提链/双缺失）见
`review/052_realtest/REPORT.md`。示例与仿真数据全部为合成样例，不代表任何正式案例的真实结论。

## 方法来源

| 借鉴对象 | 借了什么 | 改动点 |
|---|---|---|
| `LengFeng00/research-gap-finder`（MIT） | 「先验工作 → 空白 → 假设」的三段推进与 SKILL 骨架 | 输出改为逐候选方向的四条硬字段（问题陈述/比较矩阵/池内新颖性/条件式可行性），每步强制挂 `record_id`/`evidence_id` 与计数依据 |
| `cxcscmu/IdeaTaste`（MIT） | idea 必须与真实已发表工作对照 | 对照落成可复算 `claim_coverage` + 三档判定 + 最近邻逐字引文；「池内未见」与「无人研究」分开 |
| `ulab-uiuc/GraphEval`（MIT） | 多维度 + 可解释聚合 | 改为四维**并置**不聚合；前提强度（1.0/0.7/0.0）外置 `rules/scoring.json` 可复算 |
| `SakanaAI/AI-Scientist` | 新颖性检查独立成步 | 去掉 LLM 自评；池内覆盖 + `pool_external_check`（检索式建议、`executed=false`），绝不输出全球首创 |
| SkillHub `novelty-vs-feasibility-assessor`（aipoch） | 新颖性与可行性同时给结论 | 可行性落成 6 条固定前提清单，每条 `task_given/record_evidence/explicit_assumption` 三选一，结论随强度降级 |
| `wanshuiyin` 的 `patent-novelty-check` | 「现有技术」对照口径 | 比较矩阵五维（任务/方法/数据/评价/结论）逐格标注来源，缺失维度标「不可直接比较及原因」 |
| 本仓库 `answer/048_深度研究 Agent` | sources/records 双结构摄入、前瞻协议先落盘、逐字证据表 + char_span 校验、方向词典冲突双列、CJK 键 token 展开检索、`pdf_min.py` 自研 PDF 提取 | 只读复用后改造为选题评估：检索单位从子问题改为候选方向；证据按用途分 `retrieval/novelty_overlap/premise_evidence` 三类；冲突接入方向风险 |

复算边界：不联网，新颖性只在 case 记录池内判定（`novelty_scope=pool_observed`）；`claim_coverage`
是词面度量，同义改写与跨语言工作会低估相似度（已登记 uncertainty），因此 `not_found_in_scope`
只作「池内未见」的证据；池外核验建议未执行；不声称完成实验，也不声称全局完备性或全球首创。
