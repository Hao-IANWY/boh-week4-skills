---
name: patent-literature-retrieval
description: 专利与技术文献检索：读取 USPTO 影印件 PDF/OCR 侧车（SHA-256+提取方法留痕）、号码规范化含 WO 合并 OCR 拆解、优先权族归并声明不穷尽、权利要求定位到来源文本（source_ref+精确 char_span）、法律状态默认 unverified，主题查询对材料正文另做命中与原文短引。
---

# 专利与技术文献检索 Skill（patent-literature-retrieval）

专利查新最容易出错的地方不是"检索不到"，而是**字段口径**和**状态口吻**：公开号当授权号引用、
优先权日当申请日、把关键词命中的不同专利说成同族、把几年前的转载页当"官方著录项"、在只有二手聚合数据时
仍以确定语气写"该专利有效"、把影印件里 OCR 出的残句当完整权项。本 skill 把这些环节全部落成可复核字段与显式缺口，
并且**每条权项都能指回它来自哪份文件的哪一页哪几个字符**。

## 何时使用

- 从公开号 / 申请号 / 授权号 / 优先权号出发定位专利，需要判断**引用的到底是哪一个号**；输入号码可能带千分位、空格、小写、全角或缺种类码；
- 需要给出专利族归并结果，并说明"这是观察到的同族，不穷尽"；
- 输入含 USPTO/WIPO **影印件 PDF 或 OCR 侧车文本**，权利要求只存在于影印件里，需要定位到来源文件、页码与字符区间；
- 号码被 OCR 合并（如 `WO2018123456A1` 丢斜杠丢空格），需要拆解候选而不是硬猜；
- 需要引用权利要求，且要标清**权项版本**（A1 公开版 vs B2 授权版）与 OCR 边界；
- 需要回答"现在是否有效"，而手上只有二手来源；
- 需要用关键词 + IPC/CPC + 申请人组合做技术主题检索，并对**技术对比文献的正文**（TXT/影印件提取文本）做命中与原文短引。

不适用：侵权判定、FTO 结论、专利价值评估、专利撰写；不绕过付费墙、不外显受限库原文。

## 输入

| 位置 | 内容 |
|---|---|
| `/input/case.json` | `case_id`、`task_instruction`、`output_contract`、`queries[]`（`type=number/topic/assignee_topic`）、`records[]`（或 `patents` / `candidates`）、材料引用 |
| `task.md` | 任务说明；不参与号码解析 |
| 记录字段（容错） | `publication_number` / `application_number` / `priority_number` / `priority_date` / `filing_date` / `publication_date` / `assignee` / `ipc` / `cpc` / `claims`（列表或整段文本）/ `source_db` / `source_tier` / `queried_at` / `ocr` / `legal_status_observed` / `restricted` |
| 影印件引用 | 记录级 `facsimiles` / `facsimile_pdfs` / `pdf`（字符串路径或 `{path, ocr}`）；案例级 `facsimiles` / `materials`（可带 `record_id` / `kind` / `ocr`）；文献全文另有记录级 `fulltext_path` 与案例级 `literature` / `comparison_papers`。相对路径按 case 所在目录解析，case 迁到 `/app/work` 后回退各级祖先 `input/`、cwd 与 `/input` |
| 侧车文本 | 影印件旁的 `<pdf>.ocr.txt` / `<pdf>.txt` / `<stem>.ocr.txt` / `<stem>.txt`，优先级与 OCR 判定见 `rules/facsimile.json.sidecar_priority` |
| 主题词表 | `rules/ipc_topics.json`：主题 → IPC 前缀 + 关键词 + 检索式模板；`rules/field_dictionary.json`：号码类型、种类码、来源分级、状态取值；`rules/scoring.json`：权重、阈值与材料命中参数；`rules/facsimile.json`：侧车优先级、提取方法词表、OCR 边界、WO 合并拆解口径 |

## 流程（9 步）

1. **读查询**：号码型、主题型、申请人型三类；每条生成 `expression`（`PN=(...)` / `(IPC=(...) ) AND (TI=(...))`）。
2. **读材料正文**：每份影印件优先吃侧车文本，其次用 `scripts/pdf_min.py`（zlib + BT/ET/Tj 流扫描，纯标准库）提取；逐文件记 SHA-256、`extraction_method`、字符数与页数；扫描件抽不出文本时如实标 `failed`，不编造正文。未显式挂接的影印件按"公开号数字出现在文件名"提示性回填（`linkage=filename_match`）。
3. **号码规范化**：NFKC 归一后拆 `country` / `digits` / `kind_code`，判 `grant` / `application_publication` / `application`；位数不足或缺种类码 → `ambiguous=true` + `alternatives[]`；**WO 合并形态**（丢斜杠丢空格，如 `WO2018123456A1`）仍给 canonical 以便跨写法命中同一记录，但标 `wo_merged=true + ambiguous=true` 并给「年份/序号」拆解候选（`wo_split`），不把单一拆解写成确定结论。
4. **相关性打分**：`w_num·number_match + w_kw·keyword_match + w_ipc·ipc_match + w_meta·metadata`；文献记录挂接的**对比文献正文**参与该记录自身的关键词命中（影印件正文不参与打分，只用于定位与佐证）；技术文献用另一套元数据字段。
5. **材料正文命中**：主题型/申请人型查询另对全部材料正文做关键词+申请人命中，产出 `quote`（来源文本精确切片）+ `char_span`，落在 `evidence_map.literature_hits`，**与 records 打分分开呈现**，不并入 relevance。
6. **族归并**：优先权号归一为第一判据，申请人+标题 Jaccard+优先权日窗为辅助；每族固定标 `scope=observed_not_exhaustive`。
7. **权利要求来源定位**：内联 claims 优先（`source_ref=inline_field`，char_span 在内联拼接文本内）；缺内联时解析挂接的影印件/侧车文本（`facsimile:<file>` + `page:N`，或 `ocr_sidecar:<file>`），只认"行首 N. "从 1 连续递增的字面锚点；**quote 必须是其来源文本内 char_span 指向的精确子串**（无省略号、无空白归一）；内联权项若在影印件文本中出现同文，记 `facsimile_corroborated`；OCR 来源一律 `ocr_boundary=true`。
8. **法律状态**：默认 `unverified`；仅当 `source_tier=official_registry` 且带 `queried_at` 才写确定值，否则保留 `observed_value` 并给 `recheck_path`。
9. **落盘**：`candidates` / `field_table` / `legal_status` / `claims_table` / `patent_families` / `excluded` / `evidence_map` + 状态说明。

## 输出（`result.json`）

| 段 | 字段 | 说明 |
|---|---|---|
| 标识 | `case_id`、`skill`、`generated_at` | `case_id` 与输入逐字一致 |
| 主结果 | `candidates`、`field_table`、`legal_status`、`claims_table`、`patent_families`、`excluded`（+ contract 其余字段） | 每条候选含规范化号码对象（含 `wo_merged`/`wo_split`/`alternatives`）、`provenance{db,queried_at,source_tier}`、挂接影印件视图、`score_breakdown`、`query_scores` |
| 权利要求 | `claims_table[].claims[]`：`source_ref`（`inline_field` / `facsimile:<file>` / `ocr_sidecar:<file>`）、`page`、`char_span`、`quote`、`ocr_boundary`、`facsimile_corroborated`、`status` | quote 是来源文本精确子串；`claims_table[].gap` 显式登记 `R_CLAIM_SOURCE_UNAVAILABLE` 等缺口 |
| 证据记录 | `evidence_map.search_expressions / number_normalization / facsimiles / literature_hits / family_basis / claims_lookup / legal_status_lookup / provenance_rules` | 传真件索引带 SHA-256/提取方法/字符数/挂接方式；材料正文命中带逐关键词 quote+char_span |
| 状态说明 | `status.overall`、`status.unmet`、`status.notes`、`uncertainty[]` | 法律状态未核验、族不穷尽、OCR 边界、号码 ambiguous/合并、材料提取失败逐条列明 |

## 异常处理

| 情况 | 行为 |
|---|---|
| `case.json` 缺失/不可解析 | `status.overall=input_error`，保留错误原文，退出码 2 |
| 无记录 | `candidates=[]`、`status.overall=empty`、`unmet=["检索结果"]`，注明"空结果≠该号码/主题不存在" |
| 影印件文件缺失 | `extraction_method=missing`，进入 `status.unmet`（材料文本提取），不编造文本 |
| 影印件抽不出文本（扫描件/非文本流） | `extraction_method=failed` 并写原因，进入 `status.unmet`；权项缺口记 `R_CLAIM_SOURCE_UNAVAILABLE`，不摘引、不改写 |
| 侧车为 `.ocr.txt` 或记录 `ocr=true` | 该来源上的一切权项/摘引 `ocr_boundary=true`，残句不作为完整权项范围 |
| WO 号 OCR 合并（丢斜杠丢空格） | 保留 canonical、标 `wo_merged + ambiguous`、给 `wo_split` 拆解候选，状态说明列出记录；拆解需回 WIPO/受理局确认 |
| 号码缺种类码或位数不足 | `ambiguous=true` + `alternatives[]`，不猜类型 |
| 只有二手来源的状态 | 输出 `unverified`，保留 `observed_value`，给 `recheck_path`，并记 `R_OVERSCOPED_CLAIM` 风险 |
| 权项定位失败（内联存在但找不到原文） | `R_CLAIM_NOT_LOCATED`，不摘引、不改写 |
| 技术文献记录无权利要求 | 不算缺口（`non_patent` 不挂 `R_CLAIM_SOURCE_UNAVAILABLE`），claims_table 空表呈现 |
| 材料正文关键词未命中 | `literature_hits` 不含该材料；`terms_without_hit` 保留诚实记录 |
| 受限商业库 | 只登记元数据与访问条件，`provenance.restricted=true`，不抓正文 |
| 族成员仅限本轮记录 | `scope=observed_not_exhaustive`，不声称简单族/扩展族 |

## 自查

```bash
# 从仓库根目录运行
python3 -B answer/011_专利与技术文献检索/scripts/test_pipeline.py
python3 answer/_shared/verify.py --only 011
```

多案例回归（`scripts/test_pipeline.py`）必须全绿：最小示例 + realtest 5 案例（基准/号码混乱/仅传真件权项/鲁棒/空记录），
校验号码变体同 canonical、合并 WO 的 ambiguity 与拆解候选、claims quote 是来源文本精确子串、
unverified 口径、`pdf_min` 写入→读回 round-trip，以及同一输入两次运行除 `generated_at` 外逐字节一致。

单案例手工核查：

```bash
cd answer/011_专利与技术文献检索
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/o/result.json
```

- `validate.py` PASS；同一输入两次运行除 `generated_at` 外逐字节一致。
- 抽查 `field_table[i].publication_number_canonical` 与原始号码是否只差空格/千分位/大小写/全角。
- 抽查 `legal_status[].verified` 是否全部为 `false`（本轮无官方登记访问）且 `recheck_path` 非空。
- 抽查每条 located 权项：在其 `source_ref` 指向的来源文本里 `text[char_span[0]:char_span[1]] == quote`。

## 方法来源（借鉴与改动点）

- 骨架借鉴 `Obviously-Not/patent-skills`（MIT）的检索/分析子技能划分，**改动**为把每个检索步骤落成"查询式 → 命中号码 → 规范化字段表 → 来源与查询日期"四段留痕。
- 字段口径借鉴 `google/patents-public-data` 对 publication / application / priority / CPC / family 的区分，**改动**为本地号码规范化器（含 OCR 噪声与 WO 合并拆解）+ `ambiguous` 候选解释，从结构上消灭"公开号=授权号"。
- 族归并借鉴 SkillHub `free-patent-search-zhcn`（智慧芽）对法律状态核验的叙述要求，**改动**为显式 `unverified` 建模与 `recheck_path`，且族关系强制声明不穷尽。
- 权项定位借鉴 SkillHub `patent-invalid-search` 的"权项要素 → 检索式"分解，**改动**为在源文本（内联字段或影印件/侧车文本）上做字符区间定位并标 `version`、`source_ref` 与 `ocr_boundary`；影印件提取用自研 `scripts/pdf_min.py`（纯标准库 zlib + BT/ET/Tj 流扫描），**不做 OCR、不做通用 PDF 解析**，扫描件如实记 gap。

## 最小调用示例（**示例，非正式测试案例**）

```bash
cd answer/011_专利与技术文献检索
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json
```

示例为 6 条合成记录（3 条电池主题专利、1 篇期刊技术文献、1 条 mRNA 专利、1 条号码 ambiguous 的记录）
与 4 条查询（2 条号码型、2 条主题型），随案例附 3 份合成材料（`examples/min_example/input/materials/` 下
影印件 PDF、WO OCR 侧车、对比文献全文）。示例输出要点：

- 6 条全部命中，`Q1` 命中 P1 授权号，`Q2` 命中 P2 申请号，`Q3` 命中电池主题（含技术文献 P4），`Q4` 命中 mRNA 专利 P5；
- `P1/P2` 因同一优先权 `US62345678` 归入同一族 `F-…`，`scope=observed_not_exhaustive`；
- P1 权项 `source_ref=inline_field`、quote 为内联文本精确切片，且在影印件文本中获得佐证（`facsimile_corroborated=true`）；
- `evidence_map.facsimiles` 登记三份材料：PDF 走 `pdf_stdlib_text`、WO 侧车走 `ocr_sidecar`（`ocr_boundary=true`）、文献全文走 `plain_text`，各带 SHA-256 与字符数；
- `evidence_map.literature_hits` 中 `Q3` 对材料正文给出逐关键词 quote + char_span（与 records 打分分开呈现）；
- `P6` 的 `US 10,234,567` 缺种类码 → `number_kind=unknown`、`ambiguous=true`、给两条候选解释；
- 6 条状态全为 `unverified`（其中 P1/P3/P5 保留二手来源的 `observed_value`），逐条带 `recheck_path`；
- `status.overall=partial`（法律状态核验未完成）。

示例数据全部为合成样例，**不代表任何正式案例的真实结论**。
