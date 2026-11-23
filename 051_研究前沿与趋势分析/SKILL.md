---
name: research-frontier-trend-analysis
description: 在给定题录池上产出可复算的研究前沿与趋势分析：前瞻协议筛选后，按年份证据桶判定每条趋势主张（rising/established/thin），前沿=近桶新起+基数对比的池内相对增量，空白只来自检索缺口，词频热度只作对照审计；适用于“这个方向现在做到哪、往哪走、哪里还空着”的池内快照趋势任务。
---

# 研究前沿与趋势分析 Skill（research-frontier-trend-analysis）

把"把检索结果前 N 条当前沿、把关键词热度当趋势、把没检索到当空白"换成**可复算的池内快照证据**：sources/records 双结构摄入 → 去重 → 前瞻协议逐条筛选 → 年份口径分序列 → 时间序列与拐点 → 主题归并与演化 → **趋势证据桶与前沿判定** → 逐字证据表与冲突双列 → **词频热度审计** → 代表作多口径 → 空白判据 → 证据矩阵。每个统计量都能按 `rules/statistics.json` 重算。

核心立场（对应审查记录）：**趋势主张必须由证据桶支撑**（年份分桶 + 每桶计数 + 代表记录逐字引文）；**前沿=相对增量**（近桶新起 + 与既有主题的基数对比），不是领域新兴度；**薄证据主题只报计数**，不写趋势句；**空白只来自检索缺口**（零证据子问题）；**词频热度去重后坍缩即如实呈现**，不得冒充趋势；全部结论限定「池内快照」。

## 何时使用

- 给定一个研究主题 + 一批题录（9 来源/10 记录画像：题名、年份、摘要或来源文件），要回答：产出量怎么变、主题如何演化、哪个方向构成前沿、各阶段代表作是谁、哪里还缺证据；
- 需要**前瞻协议先注册后筛选**（纳排标准落盘、逐条 criterion_hits），协议缺失时按声明回退口径执行并显式承认 input_gap；
- 需要**区分 rising / established / thin / no_year_data**，并要求每条趋势主张带分桶计数与逐字代表记录；
- 需要避免"词频高=趋势""只有 1 条记录=前沿""没检索到=领域空白"。

不适用：需要实时跨库检索（本 skill 只在 case 提供的题录池内统计）、全文精读、单篇论文方法学审查（走 053/055）、写作润色。

## 执行（Harbor 运行时，先跑这一条）

正式案例的输入固定在 `/input`，Skill 安装在 `/skills/weekly-skill`，产物写到 `/app/output`。直接运行：

```bash
python3 -B /skills/weekly-skill/scripts/pipeline.py \
  --case /input/case.json --work /app/work --out /app/output
python3 -B /skills/weekly-skill/scripts/validate.py \
  --case /input/case.json --result /app/output/result.json
```

管线同时写出 `result.json`（机器契约与证据台账）、`report.md`（人类可读研究产物）和 `plan.json`（前瞻计划与协议）。若 `/skills/weekly-skill` 不存在，先用 `find /skills /app /opt -maxdepth 5 -name pipeline.py` 定位实际安装目录，再用绝对路径运行；不得因路径不存在而跳过执行，也不得用示例结果冒充正式结果。`validate.py` 返回非零时保留已产出的 `result.json` 与 `report.md`，按 `status.unmet` 说明缺口，不伪造通过。

## 输入

| 位置 | 内容 |
|---|---|
| `/input/case.json` | `case_id`、`topic`、`constraints.year_from/year_to`、`subquestions[]`（子问题+检索键）、`sources[]`（来源文件清单）、`records[]`（题录，`source_path` 指向来源文件）、`protocol`（前瞻筛选协议，可选）、`topic_taxonomy`（可选，覆盖内置词表） |
| `task.md` | 任务说明；`topic` 缺失时从正文兜底抽取 |
| 题录字段（容错） | `title` / `abstract` / `text` / `year` / `online_year` / `preprint_year` / `keywords` / `venue` / `doi` / `type` / `source` / `citations` |

正文获取优先级：内联 `text` → 内联 `abstract` → 侧车 `.txt/.ocr.txt` → 自研 PDF 提取（`scripts/pdf_min.py`）→ DOCX zip+xml → 文本文件直读 → 诚实 `missing`。题录文件另支持 JSON/CSV 引用（`corpus_path` 等）与 `materials[]`。

## 流程

1. **双结构摄入**：`sources[]` 清单逐份核对存在性与 SHA-256；记录按上述优先级取正文，逐条记 `extraction_method` + 字符数，正文不可读的记录不进池、不编造内容。
2. **题录去重**：DOI 归一优先、标题强归一兜底（两轮），预印本让位正式版，记 `R_DUP_*`。
3. **前瞻协议逐条筛选**：协议块先解析落盘（`registered_prospective`），再对每条记录评估纳入/排除标准并记 `criterion_hits`；仅 `included` 记录进入趋势统计池；协议缺失记 `R_PROTOCOL_MISSING` 并按 `rules/screening_rules.json` 的声明回退口径执行，逐条标 `fallback=true`。
4. **年份口径**：`journal / online / preprint` 三序列分别解析；主序列缺失回落 `year` 并标 `year_imputed`；全缺标 `year_missing` 计入缺失率（阈值 0.25）。
5. **时间趋势**：窗内逐年计数 → 同比（分母下限 1）→ CAGR → 拐点判定（滑均二阶差分 + 两侧水平均值 ≥0.4 才判持久拐点，否则记离散波动）。
6. **主题归并与演化**：`rules/topic_taxonomy.json`（或案例自带词表）多标签命中；阶段×主题矩阵 + 出现/增长/回升/衰退/合并/分裂事件；**薄证据主题（<2 条）与年份全缺主题不产生事件**，单列 `evidence_thin` / `year_data_missing`。
7. **趋势证据桶与前沿判定**：按 `trend_buckets.bucket_width_years` 把窗口分桶，每主题给出逐桶计数表；判定阈值全部外置 `rules/statistics.json.trend_verdict`——`rising`（末桶 ≥2 且较首个非空桶有增量且后半窗占比更高）、`established`（前半桶占比 ≥0.6，不下衰退结论）、`thin`（≤1 条，只报计数）、`no_year_data`（年份全缺，只报计数并声明）、`flat`（兜底）；**前沿候选 = rising 且近桶新起且近桶计数高于既有主题近桶中位**，基数对比数值写进主张，比较不成立如实判非前沿并写明原因。每条主张携带 `representative_records`（按 `rules/representative.json#trend_evidence` 去重后取证据最强的 2-3 条，逐字引文 + char_span）与 `claim_scope=pool_snapshot`。
8. **逐字证据表与冲突双列**：每条（记录×主题）取证据最强的一句逐字引文（`record_id + source_file + char_span + quote_verified`）；方向词典（`rules/direction_lexicon.json`）对同主题共享关键词的证据成对判定，`direction_conflict` / `zero_vs_direction` 以 side_a/side_b 双列呈现，不择一、不写"尚存争议"。
9. **词频热度审计**：对词表关键词统计 `raw_hits` 与去重后的 `distinct_records` / 桶分布；`raw_hits ≥3` 但集中在单条记录（或单桶单条）→ 标 `heat_collapse`；趋势主张一律按记录级证据桶计算，结构上禁止引用 `raw_hits`。
10. **研究空白**：零证据/弱证据子问题（检索缺口，`R_NO_EVIDENCE`/`R_WEAK_EVIDENCE`）+ 覆盖缺口 + 未收敛问题 + 维度缺口，每条带 `retrieval_scope / exclusions / upgrade_requires`，并用 `forbid_phrases` 自检越界表述。
11. **证据矩阵 + 契约绑定**：趋势/主题/代表作/空白/冲突主张各自回链 `record_ids` 与复算口径；按 `rules/contract_routing.json` 绑定 `output_contract` 字段，未命中的键落 `not_produced` 并记 `unmet`。

## 输出（`result.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id`、`skill`、`generated_at` | `case_id` 与输入逐字一致 |
| 主结果 | `output_contract` 全部字段（按路由表绑定）+ `研究产物`（`temporal_trends` / `frontier_trends` / `keyword_heat` / `topic_evolution` / `representative_works` / `research_gaps` / `snapshot_scope` / `statistics_policy`）+ `evidence_table` | 契约字段名不固定时由路由表映射 |
| 证据记录 | `evidence.sources_manifest` / `protocol` / `screening` / `temporal` / `topics` / `frontier` / `evidence_table_map` / `conflicts` / `keyword_heat_summary` / `representatives` / `gaps` / `subquestions` / `exclusions` / `rules` / `scope` / `contract_routing` | 每条被排除记录挂 reason code |
| 状态说明 | `status.overall`、`completed`、`unmet`、`notes`、`uncertainty[]` | 缺口逐条列明，不补造 |

`statistics_policy` 固定输出 13 项口径：统计窗口、计数单位、年份口径、去重口径、前瞻协议、证据桶、主题归并、占比分母、拐点判据、前沿判据、词频口径、代表作口径、空白判据。

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 题录为空 | `status.overall=empty`，`unmet=["题录集合"]`，并声明"空结果 ≠ 领域无研究" |
| 前瞻协议缺失 | `R_PROTOCOL_MISSING` + 按声明回退口径筛选（`fallback=true`），不冒充已按协议筛选 |
| 记录正文不可读 | `unscored_no_body` 诚实降级，不参与纳排与趋势统计，不编造内容 |
| 年份缺失率 > 0.25 | 时间趋势降级为观察性描述，记 `unmet` + `uncertainty` |
| 主题只有 1 条记录 | verdict=thin，只报池内计数，不写趋势句（`R_EVIDENCE_THIN`） |
| 主题年份全缺 | verdict=no_year_data，只报池内计数并声明（`R_YEAR_MISSING`），记 `unmet` |
| 词频高但去重后仅单条记录 | `heat_collapse`，登记 `uncertainty`，趋势主张不受影响（只按记录级分桶） |
| rising 但非新起或基数对比不成立 | 判非前沿候选，`reason_if_not` 写明原因 |
| 未命中词表比例 ≥ 0.3 | 记 `unmet`，提示词表可能截断新兴主题 |
| 无引用数字段 | 代表作高被引口径记 `unmet`，不猜数值 |
| 输出出现越界表述 | `research_gaps.forbidden_phrase_check` 自检命中即登记不确定性 |

## 自查

```bash
cd answer/051_研究前沿与趋势分析
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/o/result.json
python3 -B scripts/test_pipeline.py
python3 -B scripts/pdf_min.py --self-test
```

- `validate.py` 必须 PASS；`python3 -B` 保证不落 `__pycache__`；同输入跑两次，除 `generated_at` 外逐字节一致。
- 抽查任意趋势主张：`bucket_table[b].count` 应等于该桶 `record_ids` 长度；代表记录 `quote` 应是 `source_file` 在 `char_span` 处的逐字子串。
- 抽查判定：`rising/established/thin` 的每个阈值都能在 `rules/statistics.json.trend_verdict` 找到出处。

## 最小调用示例（**示例，非正式测试案例**）

```bash
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
```

示例输入为 27 条**合成**题录（固态电解质界面工程，2019–2025），无协议字段（演示回退口径）；示例输出要点：

- `status.overall=partial`（缺协议 → `R_PROTOCOL_MISSING`，按声明回退口径筛选并逐条标 `fallback`）；
- 年度序列 2→3→4→7→2→2→5，CAGR=0.165；`2022` 判为峰值型拐点（两侧水平均值 3.5→2.0，相对变化 -0.43）；
- 趋势证据桶 4 桶（2019-2020 / 2021-2022 / 2023-2024 / 2025-2025），8 个主题：4 established、3 flat、1 thin（sustainability 只报计数）；无前沿候选（池内无近桶新起主题）；
- 演化事件 13 条（5 衰退、2 出现、2 增长、2 合并、2 分裂）；代表作 9 条，3 阶段全覆盖；
- 空白：覆盖缺口 4 / 未收敛问题 2 / 维度缺口 2（无零证据子问题缺口）；
- 词频审计 24 个关键词，11 个热度坍缩（单条记录集中），全部登记 `uncertainty`；
- 逐字证据表 50 行全部通过 `quote_verified`；排除记录 `R_DUP_TITLE ×1`、`R_YEAR_MISSING ×1`。

示例数据全部为合成样例，**不代表任何正式案例的真实结论**。4 个真实画像仿真案例（基准/趋势判定/词频陷阱/鲁棒降级）见 `scripts/test_pipeline.py` 与仓库 `review/051_realtest/`。

## 方法来源

| 借鉴对象 | 借了什么 | 改动点 |
|---|---|---|
| `massimoaria/bibliometrix`（年度产出/三领域图标准口径） | 先固定计数单位与年份口径，再算占比 | 不引 R 依赖；年份口径升级为显式三序列并逐条记账 |
| `MaartenGr/BERTopic`（`topics_over_time`） | "阶段 × 主题"长表形态 | 嵌入聚类换成可审计的词表命中；本版再加"证据桶"层：趋势主张必须带逐桶计数 + 代表记录，事件对薄证据主题封口 |
| `J535D165/pyalex`（年份/引用字段语义） | `publication_year` 语义与引用数只作次级口径 | 不联网；引用数缺失时该口径记 unmet |
| `openclaw/skills` 的 `citation-chasing-mapping` | 代表作多口径 | `first_mover` 与 `most_cited` 强制并置；趋势主张另按 trend_evidence 口径携带逐字代表记录 |
| 本仓库 `answer/048_深度研究 Agent`（11-21 真实画像升级） | sources/records 双结构摄入、前瞻协议逐条筛选（criterion_hits + input_gap 回退）、逐字证据表（char_span + quote_verified）、方向词典冲突双列、零证据子问题空白推导、`pdf_min.py` 自研 PDF 提取 | 全部按只读+复制复用，不改 048 任何文件；把"结论回链"改造成"趋势主张证据化"——判定阈值（rising/established/thin/frontier）外置 `rules/statistics.json`，新增词频热度坍缩审计与池内快照范围声明，这是 051 区别于 048 的核心增量 |
| 本仓库 `answer/001_学术文献检索` | 工程骨架：`kit.Result` 三段式、reason code 体系、契约路由 | 沿用；契约路由扩到筛选留痕/前沿判定/词频审计/快照范围 |

复算边界：本 skill 不联网、不读政策/产业事件，演化"因何变化"只给题录层面证据，因果归因一律 `needs_review`；前沿判定是**池内相对增量**（近桶新起 + 基数对比），不是领域新兴度；词表按词面命中（CJK 子串 / ASCII 词边界+复数），术语漂移表现为 `untagged` 并在 `uncertainty` 登记。
