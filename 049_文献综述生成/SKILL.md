---
name: literature-review-writer
description: 在给定文献集上生成可复核的综述：sources/records 双结构摄入（内联/侧车/自研 PDF/DOCX，带 SHA-256）→前瞻筛选协议先落盘后逐条执行（criterion_hits）+DOI/标题去重→主题由证据聚类生成（词表×记录集×Jaccard 合并，不套通用框架）→逐字证据表（char_span 精确子串校验）→方向词典冲突双列→薄证据主题如实声明→覆盖声明（池内快照非全局），每句论断挂 evidence_id 回链。
---

# 文献综述生成（literature-review-writer）

综述不是把摘要连起来，更不是把「背景/方法/挑战/展望」四段式套上去。本 skill 把四件事拆开并各自留痕：**筛选标准先于写作落盘并被逐条执行**、**主题从这批文献的证据里聚类长出来**、**每条实质论断能指回原文的精确位置**、**反面证据以双列呈现不被抹平**；综述末尾给出**覆盖声明**（覆盖了什么、没覆盖什么、语料边界=池内快照非全局）。

## 何时使用

- 用户给定一批文献（题录/来源文件）与研究问题，要求生成**带引用、可复核的综述草稿**；
- 案例带**前瞻性筛选协议**（纳入/排除/停止条件），需要证明「先落盘计划、后执行筛选」；
- 需要区分"已被证明 / 有提示性证据 / 仍有争议"，并把研究空白落到**具体证据缺口**；
- 需要把方向相反的研究结论**双列并列**（各自逐字引文 + 方向 + 强度词），而不是取一方或写"尚存争议"；
- 需要明确**证据薄弱的主题**与**覆盖边界**，不用通用框架硬凑章节。

不适用：单篇精读与翻译、语言润色、投稿选刊、事实层面的学术审查（属相邻课题）。

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
| `/input/case.json` | `case_id`、`task_instruction`、`output_contract`、`research_question`、可选 `protocol`（前瞻协议）、可选 `themes[]`（主题锚点：`theme_id`/`label`/`terms`）、可选 `constraints` |
| `/input/task.md` | `research_question` 缺失时从正文兜底；合成声明等进入结果说明 |
| `sources[]` | 来源文件清单：`source_id`/`title`/`path`/`type`（对应"9 份来源"结构） |
| `records[]` | 记录清单：`record_id`/`title`/`type`/`year`/`doi` + `source_path` 或 `source_id` 指到来源文件；也可内联 `text`/`abstract` |
| 规则表 | `rules/review_criteria.json`（协议回退口径/去重/阈值/冲突口径）、`rules/topic_clustering.json`（聚类词表/合并/命名/薄证据规则）、`rules/direction_lexicon.json`(方向词典 + 冲突配对规则)、`rules/contract_projection.json`（契约投影） |

**记录正文获取优先级**（每条落 `extraction_method` + SHA-256 + 字符数）：内联 `text` → 内联 `abstract` → 侧车 `.txt`/`.ocr.txt` → 自研 stdlib PDF 提取（`scripts/pdf_min.py`：zlib Flate + BT/ET + Tj/TJ）→ DOCX zip+xml → 文本文件直读 → 诚实 `missing`（不编造）。路径按 case 目录 → work 目录 → cwd → `/input` 依次解析。

**协议判据 DSL**：`all_keys`（全部子串命中）、`any_keys`（任一命中）、`type_in`（类型白/黑名单）、`body_readable`+`min_chars`（正文可读性与最短长度）；命中/未命中逐条记 `criterion_hits`。

## 流程（7 阶段）

1. **摄入**：解析 sources/records 双结构，逐条取正文并留痕（`source_extract` 轨迹：方法、字符数、SHA-256、失败原因）。
2. **问题与锚点**：研究问题（case 优先、task.md 兜底）；案例显式 `themes[]` 作为主题锚点（支撑关系仍由记录文本命中产生），否则主题完全由证据聚类生成。
3. **前瞻协议落盘**：协议（或缺失声明 + 回退口径）、主题生成规则、约束、筛选顺序先写入 work 的 `plan.json`（不可写时回退 out），**再**开始筛选——可复核的"先注册后执行"；协议缺失记 `R_PROTOCOL_MISSING`，按声明回退口径筛选并逐条标 `fallback=true`。
4. **筛选与去重**：逐条记录按协议纳排（`protocol_screen` 轨迹记 criterion_hits）；随后 DOI 归一 + 标题+年份强归一去重（保留首见，重复改判 excluded 并记 `dedupe_of`，理由码 `R_DUP_DOI`/`R_DUP_TITLE`）。
5. **主题聚类**：从纳入记录内容提取聚类词表（CJK 极大 n 元组 2–8 字、修剪边缘虚词与停用词 + ASCII 词，df≥2、剔除语料普遍词，上限 24）→ 词项-记录集按 Jaccard≥0.6 合并成簇 → 判别性命名（簇内词项取前 3，去子串/高相似重复）；记录数 <2 的词簇不设主题，其记录进未聚类清单。案例 `themes[]` 声明时按锚点生成，无命中的锚点如实记 `R_SUBQUESTION_NOT_COVERED`。
6. **证据表与冲突**：每主题逐条支撑记录取一句最佳证据（主题术语命中数优先、idf 加权和次之），行 = `evidence_id` + `record_id` + `source_file` + `quote_field` + `char_span` + 逐字 `quote` + 方向 + 强度词；管线内做精确子串校验（`quote_verify` 轨迹），失败标 `quote_mismatch` 不静默；题名不进引文。冲突按 `rules/direction_lexicon.json` 同主题成对判定：`direction_conflict`（+1×-1）与 `zero_vs_direction`（0×±1）双列（side_a/side_b 各带逐字引文、方向、强度词），同证据对多主题复现记 `also_in_topics`；数值冲突要求同单位不同数值且可比口径词表（`context_vocabulary`，可选）同时命中。
7. **综述草稿**：大纲分节（方法口径 / 主题 / 薄证据主题 / 分歧与冲突 / 覆盖声明）；主题节每句 = 原文整句 + `[E###]`，强度分级 `established`（≥2 条且无冲突）/ `suggestive`（1 条）/ `contested`（涉冲突）；证据 <2 条的主题标注「证据薄弱」并写明缺口，不硬凑章节、不补写正文；数据/状态陈述句（筛选计数、薄证据声明、覆盖声明）不挂证据但逐条列于 `draft.audit.data_statements`。产物同时落 `review_draft.docx`（office_min 生成，可编辑）。

## 输出（`result.json` + `plan.json` + `review_draft.docx`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id` / `skill` / `generated_at` | 与输入逐字一致 |
| 主结果 | `screening`（协议状态 + counts + 逐条 criterion_hits + 去重明细）、`topics[]`（`topic_source`/`terms`/`support`/`support_level`/`thin`/`conflict_ids`）、`outline`、`evidence_table[]`、`draft`（sections + body + audit）、`conflicts`（count + items，双列）、`research_gaps`（count + items）、`coverage`（覆盖/未覆盖主题、未聚类记录、语料边界） | 冲突/空白为「计数+明细」对象，count=0 也如实呈现 |
| 证据记录 | `evidence.sources_manifest` / `protocol` / `screening` / `clustering`（词表 + 词项-记录映射）/ `evidence_table_map` / `backlink`（回链引用与未解析 id、引文校验计数）/ `conflicts` / `coverage` / `execution`（`tool_calls`：source_extract/protocol_screen/evidence_extract/quote_verify 全参数与返回）/ `artifacts` / `plan_file` / `scope` / `run_log` / `contract` | 词表、筛选、打分口径全部参数化于 rules/，可复算 |
| 状态说明 | `status.overall`、`status.completed`、`status.unmet`、`status.notes`、`uncertainty[]` | 协议缺失、正文不可读、引文校验失败、薄证据主题、冲突未仲裁逐条列明 |
| 旁产物 | `work/plan.json`（不可写时 `out/plan.json`）、`review_draft.docx` | 协议先于筛选落盘；docx 可编辑草稿（章节 + 段落 + 证据表摘录） |

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 无研究问题且无文献集 | `status.overall=empty`、`unmet=["研究问题与文献集"]` |
| 筛选协议字段缺失 | `input_gap`（`R_PROTOCOL_MISSING`）+ 按声明回退口径筛选（每条标 `fallback=true`），不冒充"已按协议筛选" |
| 记录正文不可读 | `unscored_no_body` 诚实降级：不参与纳排与综述、不编造内容，`unmet` 记录 record_id |
| PDF 提取失败 | 记 `pdf_extract_failed` 原因后按 missing 处理，不猜测正文 |
| 重复文献 | 协议筛选后按 DOI / 标题+年份去重，保留首见，重复行 `reason_code=R_DUP_DOI/R_DUP_TITLE` + `dedupe_of` |
| 声明主题在语料内无支撑 | `R_SUBQUESTION_NOT_COVERED` 进未覆盖清单与覆盖声明，不凭空写节 |
| 主题证据 <2 条 | `R_TOPIC_THIN_EVIDENCE`：节标题标注（证据薄弱），写「证据薄弱声明」+ 缺口，不硬凑章节 |
| 主题抽不出证据句 | `R_TOPIC_NO_EVIDENCE`：按缺口处理，不补写正文 |
| 记录未进入任何聚类 | `R_TOPIC_UNASSIGNED` + 覆盖声明单列，不静默丢弃 |
| 证据方向相反 / 与零效应并存 | `direction_conflict` / `zero_vs_direction` 双列（各自 quote + 方向 + 强度词），结论 `support_level=contested`，不择一、不写"尚存争议" |
| 同一句内正负方向并存 | 方向标 `mixed`，不参与成对判定，另记 uncertainty |
| quote 非原文精确子串 | 该行标 `quote_verified=false` + `quote_mismatch`，`unmet` 计数，不静默 |
| 契约字段名不在产物中 | 按 `rules/contract_projection.json` 投影并标注 `projection_of`；仍无对应则 `unmet` |

## 自查

```bash
# 从仓库根目录运行
python3 -B "answer/049_文献综述生成/scripts/test_pipeline.py"
python3 answer/_shared/verify.py --only 049
```

回归必须满足：最小示例与 `examples/min_example/expected/result.json` 一致（除 `generated_at`）、缺协议如实记 `R_PROTOCOL_MISSING`、DOI 去重 `R_DUP_DOI`；realtest 4 案例：协议先于结果落盘（mtime 时序 + plan.json 与结果协议一致）、主题 `topic_source=evidence_clustered` 且无通用框架节标题、证据 span 对来源文件独立复核、case2 冲突双列含 +1/-1/0 三种方向、case3 薄证据主题有「证据薄弱声明」、case4 PDF/DOCX 自提哈希可复算 + 缺正文诚实降级、两次运行字节一致。

人工抽查建议：任取一条 `evidence_table` 行，用 `source_file` + `char_span` 在原文（或 `pdf_min` 提取文本）上切出片段，应与 `quote` 逐字相等；任取草稿一句 `[E###]`，应能在 `evidence_table` 解析；`draft.audit.sentences_without_evidence` 必须为空。

## 方法来源

| 借鉴对象 | 借了什么 | 本 skill 的改动 |
|---|---|---|
| `stanford-oval/storm`（MIT，仅借流程） | 主题由材料归纳而非套四段式；引用只能来自已检索材料 | 主题归纳改为**证据聚类**（词表提取→词项-记录集 Jaccard 合并→判别性命名）的确定性算法；引用绑定改为句级 `[E###]` + 无证据句审计 |
| `Future-House/paper-qa`（Apache-2.0，仅借思路） | 草稿写完把论断回查原文 | 改为强度分级 `established/suggestive/contested` + `char_span` 精确子串校验（管线内 quote_verify） |
| `infiniflow/ragflow`（Apache-2.0，仅借字段） | 引用带位置元数据 | 位置收敛为 `source_file` + `char_span` + `sha256_quote`，可逐字复核 |
| 本仓库 `048_深度研究 Agent` | 双结构摄入/前瞻协议/方向词典/引文校验机器 | 复用其摄入与协议机制；产物从「研究结论」改为「综述」：主题聚类驱动、薄证据主题声明、覆盖声明（池内快照非全局） |
| 本仓库 `001_学术文献检索` | `kit.py` 的 `Result/Clock/norm_title` 与三段式契约 | — |

授权与许可：MIT / Apache-2.0；本目录未复制任何第三方代码，Office 产物由本仓库 `_shared/office_min.py`（纯标准库）生成。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd answer/049_文献综述生成
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
# 产物：/tmp/demo/result.json + /tmp/demo/plan.json + /tmp/demo/review_draft.docx
```

示例输入：9 条合成题录（含 1 条同 DOI 预印本、1 条无关催化文献），无协议（回退口径）。示例输出：

| 项 | 值 |
|---|---|
| 筛选 | 纳入 8、排除 1（P2 同 DOI → `R_DUP_DOI`，`dedupe_of=P1`） |
| 协议 | `missing_fallback_declared`（回退口径，逐条 `fallback=true`，`unmet` 记 `R_PROTOCOL_MISSING`） |
| 主题 | 8 个由证据聚类生成：`accuracy×top-1`(6) / `retrosynthesis`(6) / `single-step×uspto-50k×reaction`(6) / `benchmark×splits`(3) …，词表 24 项 |
| 证据表 | 27 行，逐字整句 + `char_span` + `quote_verified` 全过 |
| 冲突 | 1 组 `C01`（`numeric_conflict`，E001×E005：同 USPTO-50k + message passing，52.3% vs 49.0%），`also_in_topics=["T03"]`，双列呈现 |
| 草稿自检 | 29 句实质句全部挂 `[E###]`，`sentences_without_evidence=[]` |
| 产物 | `plan.json`（先落盘）+ `review_draft.docx` 111 段（回读自检通过） |
| `status.overall` | `partial`（协议缺失） |

示例数据全部为合成样例，不代表任何正式案例的真实结论。
