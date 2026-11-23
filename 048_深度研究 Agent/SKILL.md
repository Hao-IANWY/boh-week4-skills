---
name: deep-research-agent
description: 把不能一次答完的研究问题跑成可复核的多步研究：显式子问题/要素模板拆解→前瞻性筛选协议先落盘后逐条执行（criterion_hits）→来源/记录双结构正文获取（内联/侧车/自研 PDF/DOCX，带 SHA-256）→精确来源证据表（record_id+source_file+char_span+逐字引文校验）→结论逐句挂 evidence_id→方向词典成对冲突双列→空白只从检索缺口推导→受 constraints 约束的研究建议，retrieve/screen/extract/verify 全程留参数与返回、可复跑。
---

# 深度研究 Agent（deep-research-agent）

深度研究的可复核性来自四件事：**问题被拆成了哪些可独立检索的子问题**、**筛选协议是否先注册后执行**、**每条结论能否逐字回到来源文件的具体位置**、**整条路径能否被他人按记录复跑**。本 skill 把这四件事做成流水线：协议与计划先写入 `plan.json` 再执行，证据行带 `char_span` 并在管线内做精确子串校验，方向相反的证据一律双列呈现。

## 何时使用

- 用户给一个需要多步推进的研究问题（或案例已声明子问题清单）与一批给定来源/记录，要求**带证据、可回链的结论**；
- 案例带**前瞻性筛选/检索协议**（纳入/排除/停止条件），需要证明「先落盘计划、后执行筛选」；
- 需要区分"材料事实 / 合理推断 / 无法确定"，把无法确定的部分写成**具体检索缺口**而不是套话；
- 需要把方向相反的研究结论**双列并列**（各自逐字引文 + 方向 + 强度词），而不是取一方或写"尚存争议"；
- 需要在明确约束（来源范围/预算/时间/边界）内产出**研究建议**，且不声称完成实验、不声称全局完备。

不适用：单篇文献逐句精读、方法学与统计审查、全文翻译与写作润色（属相邻课题）。

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
| `/input/case.json` | `case_id`、`output_contract`、`research_question`、可选 `subquestions[]`（显式子问题，含 `keys`/`depends_on`）、`protocol`（前瞻协议）、`constraints`、`proposal_request`（建议场景） |
| `/input/task.md` | 研究问题缺失时从正文兜底抽取；合成声明等进入结果说明 |
| `sources[]` | 来源文件清单：`source_id`/`title`/`path`/`type`（对应"9 份来源"结构） |
| `records[]` | 记录清单：`record_id`/`title`/`type`/`year` + `source_path` 或 `source_id` 指到来源文件（对应"10 条记录"结构）；也可内联 `text`/`abstract` |
| 规则表 | `rules/facets.json`（要素词典 + 子问题模板 + 依赖）、`rules/plan_rules.json`（打分/阈值/停止条件/协议回退口径/建议规则）、`rules/direction_lexicon.json`（方向词典 + 冲突配对规则）、`rules/contract_projection.json`（契约投影） |

**记录正文获取优先级**（每条记录落 `extraction_method` + SHA-256 + 字符数）：内联 `text` → 内联 `abstract` → 侧车 `.txt`/`.ocr.txt` → 自研 stdlib PDF 提取（`scripts/pdf_min.py`：zlib Flate + BT/ET + Tj/TJ，与 review/048_realtest 的最小 writer 往返一致）→ DOCX zip+xml（`w:t` 段落）→ 文本文件直读 → 诚实 `missing`（不编造）。路径按 case 目录 → work 目录 → cwd → `/input` 依次解析。

**协议判据 DSL**：每条纳排标准支持 `all_keys`（全部子串命中）、`any_keys`（任一命中）、`type_in`（记录类型白/黑名单）、`body_readable`+`min_chars`（正文可读性与最短长度）；命中/未命中逐条记入 `criterion_hits`。

## 流程（6 阶段）

1. **摄入**：解析 sources/records 双结构，逐条取正文并留痕（`source_extract` 轨迹：方法、字符数、SHA-256、失败原因）。
2. **拆解**：case 显式 `subquestions[]` 优先（真实案例的领域问题与依赖）；否则按 `facets.json` 模板（词典部分命中而必备要素缺失时才生成澄清子问题）；两者皆不命中时整题回退为一个通用子问题（检索键取题面信息词），绝不硬凑。
3. **前瞻协议落盘**：协议（或缺失声明 + 回退口径）、子问题、检索计划、约束、筛选顺序先写入 work 的 `plan.json`（不可写时回退 out），**再**开始筛选——这是可复核的"先注册后执行"。
4. **筛选与执行**：逐条记录按协议纳排（`protocol_screen` 轨迹记 criterion_hits 与结果）；仅 included 记录进入检索；按依赖深度逐子问题检索（`record_score = 0.5·bm25 + 0.5·idf_coverage`，键先做 token 展开使中文多字键真实参与 BM25），`local_retrieve` 轨迹记参数、命中、新增证据、停止原因（无新增证据即停）。
5. **证据表**：每行 = `evidence_id` + `record_id` + `source_file` + `quote_field` + `char_span` + 逐字 `quote` + 方向 + 强度词；管线内做精确子串校验（`quote_verify` 轨迹），失败标 `quote_mismatch` 不静默；题名不进引文；二进制来源以 `body_sha256` 锚定提取文本。
6. **综合**：结论逐句挂 `evidence_id`（`sentence_map` 一一对应，陈述=原文整句+标记拼接，不改写）→ 方向词典同主题成对比对，`direction_conflict`（+1×-1）与 `zero_vs_direction`（0×±1）双列（side_a/side_b 各带逐字引文、方向、强度词），不合并不择一 → 空白只从零证据（`R_NO_EVIDENCE`）或仅弱证据（`R_WEAK_EVIDENCE`）子问题推导，落到缺哪类证据 + `plan_id` → 若案例声明 `proposal_request`，产出逐条引用 evidence/conflict/gap 与 `constraints` 的建议（`not_claimed` 显式声明不做实验、不越界）。

## 输出（`result.json` + `plan.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id` / `skill` / `generated_at` | 与输入逐字一致 |
| 主结果 | `research_plan`（`subquestions`/`plan`/`protocol`/`constraints`）、`evidence_table[]`、`screening`（协议状态 + counts + 逐条 criterion_hits）、`conclusions[]`（`sentence_map`/`evidence_ids`/`support_level`/`conflict_ids`/`dual_column`/`boundary`）、`conflicts`（count + items，双列）、`research_gaps`（count + items）、`proposals`（requested + items） | 冲突/空白/建议为「计数+明细」对象，count=0 也如实呈现 |
| 证据记录 | `evidence.sources_manifest` / `decomposition` / `protocol` / `screening` / `execution`（`tool_calls`：screen/retrieve/extract/verify 全参数与返回）/ `evidence_table_map` / `backlink`（回链覆盖率与未解析 id）/ `conflicts` / `proposals` / `artifacts` / `plan_file` / `scope` / `run_log` / `contract` | 回链覆盖率 = 引用 evidence_id 的结论数 / 非 no_evidence 结论数 |
| 状态说明 | `status.overall`、`status.completed`、`status.unmet`、`status.notes`、`uncertainty[]` | 协议缺失、正文不可读、引文校验失败、冲突、弱证据逐条列明 |
| 旁产物 | `work/plan.json`（不可写时 `out/plan.json`） | 计划与协议先于筛选执行落盘，供第三方复核时序与口径 |

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 无研究问题且无显式子问题 | `research_plan.subquestions=[]`、`status.overall=empty`、`unmet=["研究问题"]` |
| 筛选协议字段缺失 | `input_gap`（`R_PROTOCOL_MISSING`）+ 按声明回退口径筛选（每条记录标 `fallback=true`），不冒充"已按协议筛选" |
| 记录正文不可读（内联/侧车/PDF/DOCX 均失败） | `unscored_no_body` 诚实降级：不参与纳排与检索、不编造内容，`unmet` 记录 record_id |
| PDF 提取失败 | 记 `pdf_extract_failed` 原因后按 missing 处理，不猜测正文 |
| 子问题在语料内无达阈值证据 | `support_level=no_evidence` + `research_gaps`（`R_NO_EVIDENCE`，带 missing_evidence 与 plan_id）；仅弱证据 → `R_WEAK_EVIDENCE` |
| 证据方向相反 / 与零效应并存 | `direction_conflict` / `zero_vs_direction` 双列（各自 quote + 方向 + 强度词），结论 `support_level=conflicting`，不择一、不写"尚存争议" |
| 同一句内正负方向并存 | 方向标 `mixed`，不参与成对判定，另记 uncertainty |
| quote 非原文精确子串 | 该行标 `quote_verified=false` + `quote_mismatch` 说明，`unmet` 计数，不静默 |
| 问题必备要素缺失（词典部分命中时） | 生成澄清子问题 + uncertainty，不擅自补要素 |
| 纳入记录非空但零证据达阈值 | 全部子问题 empty + 空白分析；计划仍留痕 |
| 契约字段名不在产物中 | 按 `rules/contract_projection.json` 投影并标注 `projection_of`；仍无对应则 `unmet` |

## 自查

```bash
# 从仓库根目录运行
python3 -B "answer/048_深度研究 Agent/scripts/test_pipeline.py"
python3 answer/_shared/verify.py --only 048
```

最小示例必须满足：`validate.py` PASS、与 `examples/min_example/expected/result.json` 一致（除 `generated_at`）、缺协议如实记 `R_PROTOCOL_MISSING`、引文精确子串校验全过、回链覆盖率 1.0。realtest 4 案例必须满足：协议先于结果落盘（`plan.json` 在 work 且内容与结果一致、mtime 不晚于 result）、10 条记录逐条 criterion_hits、证据 span 对来源文件独立复核、冲突双列含 +1/-1/0 三种方向、零证据子问题与空白一一对应、PDF/DOCX 自提哈希可复算、两次运行字节一致（不同 `PYTHONHASHSEED` 验证）。

人工抽查建议：任取一条 `evidence_table` 行，用 `source_file` + `char_span` 在原文（或 `pdf_min` 提取文本）上切出片段，应与 `quote` 逐字相等；任取一条结论，其 `sentence_map` 的 quote 应与所引证据行逐字一致。

## 方法来源

| 借鉴对象 | 借了什么 | 本 skill 的改动 |
|---|---|---|
| `langchain-ai/open_deep_research`（MIT，仅借结构） | plan-then-execute 两段式、state 显式化 | state 落成 `research_plan` + `plan.json`，并把"计划先行"细化为**前瞻协议先落盘再逐条筛选**（criterion_hits 留痕） |
| `meituan-longcat/LongCat-DeepResearch`（仅借思路） | 分节独立研究 + 按缺口定向修订 | 每个子问题独立证据集 + evidence_id 回链；修订建议改为受 `constraints` 约束的 proposals |
| `khoj-ai/khoj`（AGPL-3.0，仅借拒答形态） | 证据不足时的结构化回执 | 改为 `research_gaps`：只认检索缺口，落到缺哪类证据 + plan_id |
| 本仓库 `001_学术文献检索` / `046_科研知识库问答` | `kit.py` 三段式契约、BM25+IDF 覆盖打分、冲突并列口径 | 打分键先做 token 展开（修复中文多字键无法命中 BM25 的缺陷）；冲突升级为方向词典成对双列 |

授权与许可：MIT / AGPL-3.0；本目录未复制任何第三方代码，实现为纯标准库原创。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd "answer/048_深度研究 Agent"
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo048
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo048/result.json
```

示例输入：6 篇合成题录（内联 text/abstract），研究问题含对象/方法/指标/比较/证据要求五类要素；案例未提供筛选协议，管线按声明回退口径筛选并在状态中声明。示例输出要点：

| 项 | 值 |
|---|---|
| 子问题 | 4 条：`S_OBJECT_METRIC`、`S_METHOD`（依赖前者）、`S_COMPARISON`、`S_EVIDENCE`（依赖前者） |
| 筛选 | 6/6 纳入（回退口径：正文可读），每条带 criterion_hits 与 `fallback=true` |
| 证据表 | 7 条，引文为原文整句，`quote_verified` 全过 |
| 冲突 | 1 条 `numeric_conflict`：USPTO-50k + message passing 口径下 52.3% vs 49.0%，双列不择一 |
| 研究空白 | 1 条：`S_EVIDENCE`（`R_NO_EVIDENCE`），missing_evidence 要求多数据集复现研究 |
| `status.overall` | `partial`（协议缺失 input_gap + 1 条子问题缺口） |

示例数据全部为合成样例，不代表任何正式案例的真实结论。
