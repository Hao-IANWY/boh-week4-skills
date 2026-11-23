---
name: systematic-review-prisma
description: 把研究问题跑成第三方可复跑的 PRISMA 式系统综述：sources/records 双结构摄入（TXT/PDF/DOCX 自研提取）、前瞻协议先落盘再筛选、题录级与全文级两轮纳排（每条评估全部条款、主因唯一）、四阶段计数链闭合自检、字段级质量评价（缺字段判 not_reported 不判低质量）、逐字证据矩阵带 char_span 校验、冲突双列与确定性降级综合。
---

# 系统性文献综述（systematic-review-prisma）

系统综述的价值在于**别人能沿你的路径走一遍**。本 skill 把五件事固化成机器：可执行的 PICO 检索式、条款化纳排（先注册后执行）、逐条可查的筛选记录与闭合的计数链、字段级质量评价（含人工复核边界）、可回链的证据矩阵与综合结论。

## 何时使用

- 给定研究问题与一批题录/全文材料，要求按 PRISMA 式流程做**系统综述**；
- 要求「纳入/排除的每一篇都有可查理由」「各排除原因计数之和=排除总数」「纳入+排除=进入筛选数」；
- 要求质量评价不止一句"质量较高"：逐字段判断，**缺字段判 not_reported 而非低质量**；
- 要求结论确定性随证据受限如实降级、冲突证据双列并列、每条论断回链到具体证据 span。

不适用：单篇精读与翻译、写作润色、事实层面的学术审查（属相邻课题）。

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
| `/input/case.json` | `case_id`、`task_instruction`、`output_contract`、`research_question`（PICO 式）、`protocol`（前瞻纳排协议）、`sources[]` 与 `records[]` 双结构、`constraints` |
| `task.md` | `research_question` 缺失时从正文兜底 |
| 协议条款 DSL | `id/stage/type/value/label`；type 支持 `year_min/year_max/type_in/language_in/require_abstract/require_fulltext/keyword_any/keyword_all/outcome_reported/field_present` |
| 记录字段（容错） | `record_id/title/abstract/text/source_path/source_id/year/type/language/doi/venue/source/keywords/sample_size/methodology` |
| 默认协议 | `rules/prisma_protocol.json`（案例未提供协议时按声明回退口径执行，仅结构条款）；契约投影见 `rules/contract_projection.json` |

正文获取优先级：内联 `text` → 侧车 `.txt/.ocr.txt` → 自研 PDF 提取（`scripts/pdf_min.py`）→ DOCX zip+xml → 文本直读 → 内联 `abstract` 兜底（不算全文）→ 诚实 `missing`。逐条记录 `extraction_method + SHA-256 + 字符数`。

## 流程（7 环节）

1. **前瞻协议落盘**：案例协议（条款/主因顺序/检索要素块）先写 `protocol.json`，再逐条筛选；协议缺失 → `R_PROTOCOL_MISSING` + 声明回退口径，筛选行 `fallback=true` 并整体进入人工复核队列。
2. **检索式**：PICO 要素块 → 每块 `(A OR B …)` → 合成 `AND` 表达式，记录数据库/字段/时间窗/语言限制与每库题录数。
3. **去重与识别**：DOI 归一 + 标题+年归一去重，重复逐条记 `R_DUP_DOI`/`R_DUP_TITLE` 与 `duplicate_of`。
4. **两轮纳排**：题录级 → 全文级；每条记录评估该阶段**全部条款**并记 `criterion_hits`；多因命中按协议 `primary_reason_order` 取**唯一主因**（其余记 `secondary_hits`，不重复计数）；元数据缺失的条款判 `undecidable` → 人工复核队列，**不自动排除**；不在筛选途中放宽。
5. **计数链自检**：四阶段计数落档（`prisma_flow.flow_counts`），自校验闭合——恒等式 I1–I3（识别=去重+筛选；筛选=排除+全文级；全文级=排除+纳入）、I4–I5（各桶之和=阶段排除数）、I6（每条排除恰落一桶）、I7（每库计数加总=识别数）；断链如实 `partial` 并写 `status.unmet`。
6. **质量评价**：`rules/prisma_protocol.json` 字段级量表（field/cue 两类）；缺字段或缺依据词 → `not_reported`（weight=0、不计入总分与分母、不判低质量，单列 `not_reported_items`）；全部条目未报告 → `not_assessable`（无法分级）。
7. **证据矩阵与综合**：主题 × 纳入研究 → 逐字整句 + `char_span` 精确子串校验 + `source_file/extraction_method` + `sha256_quote`；同主题且记录级上下文口径可比的数值不一致 → 冲突**双列**（`side_a/side_b` 各带逐字引文与数值），不择一；结论逐句挂 `evidence_id`（`sentence_map` 一一对应），确定性随 `not_reported/not_assessable` 降级（strong 封顶 moderate，not_assessable 封顶 weak）；空白只从无证据主题推导；协议缺失时不虚构主题（`R_PROTOCOL_NO_THEMES`）。

## 输出（`result.json` + `prisma_flow.xlsx` + `protocol.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id` / `skill` / `generated_at` | 与输入逐字一致 |
| 主结果 | `search_strategy`、`screening_log`、`prisma_flow`、`quality_appraisal`、`evidence_matrix`、`synthesis`、`conflicts`、`research_gaps` | `prisma_flow` 含四阶段计数、逐条排除原因桶、`flow_counts` 与闭合校验 `consistency`；`screening_log` 含逐条 `stage_decisions`、主因/次要命中、`manual_review` 队列 |
| 证据记录 | `evidence.protocol` / `sources_manifest` / `counts` / `matrix_map` / `backlink` / `quality_boundary` / `manual_review` / `conflicts` / `artifacts` / `plan_file` / `scope` / `run_log` | 协议块与可重放口径、来源清单 SHA-256、闭合恒等式、引文校验统计、回链覆盖率、确定性封顶规则 |
| 状态说明 | `status.overall` / `completed` / `unmet` / `notes`、`uncertainty[]` | 纳入集为空、协议缺失、计数断链、主题无证据、冲突未仲裁、质量受限、检索时间未记录 |
| 旁产物 | `prisma_flow.xlsx` | 四工作表：PRISMA_flow（含 closure 行）/ exclusions / included / quality；`protocol.json` 为前瞻协议落盘件 |

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 无题录 | 检索式与协议仍落盘，`screening_log.rows=[]`，`status.overall=empty` |
| 案例未提供协议 | `R_PROTOCOL_MISSING` → 回退口径（仅结构条款），全行 `fallback=true` + 人工复核队列；综述主结果为空、不虚构主题 |
| 两轮筛选后纳入集为空 | `empty` + `unmet=["纳入研究"]`，排除理由全部在 `prisma_flow` |
| 多条款同时命中 | 主因唯一（`primary_reason_order` 最先命中者），其余进 `secondary_hits`；每条排除恰落一个原因桶 |
| 年份/类型/语言等元数据缺失 | 条款 `undecidable`：不自动排除，记录继续走完流程并进 `manual_review` 队列（人工复核边界） |
| 记录缺正文/来源文件为 0 字节 | `missing`：正文 0 字符如实落档，按条款排除或转人工复核，不编造内容 |
| PDF/DOCX 提取失败 | 记 `extraction_method=missing` 与 error 原因；支持范围见 `scripts/pdf_min.py`（CMap/加密/扫描件不猜测） |
| 计数链断裂 | 恒等式检查 `consistency.checks` 标 FAIL，`unmet=["计数闭合"]`，`partial` |
| 某主题无证据句 | `R_THEME_NO_EVIDENCE` 进 `research_gaps`，该结论 `support_level=no_evidence`，不写正文 |
| 全部纳入研究 low/无法分级 | `R_ALL_LOW_QUALITY` 进空白，结论强度降级 |
| xlsx 生成失败 | 记 `uncertainty`，`result.json` 仍完整 |
| 契约字段名不在产物中 | 按 `rules/contract_projection.json` 投影并标注 `projection_of`；仍无对应则 `unmet` |

## 自查

```bash
cd answer/050_系统性文献综述
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/o/result.json
python3 -B scripts/test_pipeline.py
```

- `validate.py` 必须 PASS；两次运行除 `generated_at` 外逐字节一致（不同 `PYTHONHASHSEED` 已验证）。
- 核对计数闭合：`prisma_flow.consistency.all_closed=true`，七条恒等式 I1–I7 全 ok。
- 抽查任一 `screening_log.rows[]`：`stage_decisions` 与 `primary_criterion_id` 能由 `rules/prisma_protocol.json`（或案例协议）条款顺序重放；多因命中时 `secondary_hits` 留痕且不重复计数。
- 抽查 `quality_appraisal.rows[].items[]`：`judgment=not_reported` 的项 `weight=0` 且不在分母；确认没有任何记录因缺字段被判 low。
- 抽查 `evidence_matrix[]`：`quote` 对 `source_file` 按 `char_span` 切片逐字相等（PDF 行用 `scripts/pdf_min.py` 重新提取复核）。
- 抽查 `prisma_flow.xlsx`：四个工作表都在，`included` 表行数 = `included.records_included`。

## 方法来源

| 借鉴对象 | 借了什么 | 本 skill 的改动 |
|---|---|---|
| `ChaokunHong/MetaScreener`（Apache-2.0，仅借口径） | 题录级与全文级两级筛选的判定结构；排除必须带理由 | 判定改为**条款表 + 理由码 + 主因顺序**的确定性重放；undecidable 不自动排除 |
| `asreview/asreview`（MIT，仅借思路） | 「同一输入应得到同一纳入集」的可重算目标 | 不引入主动学习；固定条款顺序 + 稳定排序，多次运行纳入集一致 |
| `PRISMA 2020 四阶段流程`（方法学公开框架，仅借结构） | identification / screening / eligibility / included 与各阶段排除计数 | 落成 `prisma_flow.flow_counts` + 七条闭合恒等式自检 + xlsx 表 |
| 本仓库 `answer/048_深度研究 Agent`（同画像，只读复用） | sources/records 双结构摄入、前瞻协议先落盘、逐字证据 char_span 校验、冲突双列 | 主因规则、字段级质量量表（not_reported 边界）、人工复核队列为 050 特有 |
| 本仓库 `001_学术文献检索` | `kit.py` 的 `Result/Clock/norm_title/stable_id` 与三段式契约 | — |

授权与许可：Apache-2.0 / MIT / PRISMA 为公开方法学框架；本目录未复制任何第三方代码；Office 产物由 `_shared` 同源的 `scripts/office_min.py`（纯标准库）生成。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd answer/050_系统性文献综述
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
# 产物：/tmp/demo/result.json + /tmp/demo/prisma_flow.xlsx + /tmp/demo/protocol.json
```

示例输入：8 条合成题录（含 1 条同 DOI 预印本、1 条无全文会议论文、1 条无全文综述、1 条无关文献）+ 示例前瞻协议（7 条款、主因顺序）。示例输出：

| 阶段 | 计数 |
|---|---|
| 识别 | 8 条，去重 1 条（R2 同 DOI），剩 7 |
| 题录级筛选 | 7 条，排除 1 条（R7 对象要素未命中 `C4_OBJECT`） |
| 全文级合格性 | 6 条，排除 2 条（R5、R8 全文不可得 `C5_FULLTEXT`） |
| 纳入 | 4 条：R1、R3、R4、R6 |

- 闭合校验 I1–I7 全 ok；
- 质量评价：high 4（缺 `sample_size`/`methodology` 字段记 `not_reported`，共 9 项不计入低质量）；
- 证据矩阵 9 行，`quote` 全部通过 `char_span` 精确子串校验；
- 冲突 1 组：`C01`（R1 × R6，同 uspto-50k + message-passing 口径，52.3% vs 49.0%）双列呈现，`TH_PERFORMANCE` 结论 `conflicting`；
- 综合确定性：`records_with_not_reported` 列明各记录未报告条目，strong 封顶为 moderate；
- `prisma_flow.xlsx` 四个工作表均非空；`status.overall = complete`（冲突以 notes/uncertainty 呈现，不阻断完成）。

示例数据全部为合成样例，不代表任何正式案例的真实结论。真实画像仿真（9 来源 / 10 记录 / 4 案例）见 `review/050_realtest/`。
