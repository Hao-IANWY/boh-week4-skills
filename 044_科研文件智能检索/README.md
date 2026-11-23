# 044｜科研文件智能检索

> **已实现可运行 Skill（真实文件库工单适配版）**：同目录 `SKILL.md` + `scripts/pipeline.py` + `scripts/pdf_min.py` + `scripts/test_pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 044`；工程标准见 `answer/_shared/README.md`；真实文件库仿真（4 案例：9 文件基准/版本串味/定位漂移/鲁棒）见 `review/044_realtest/`（build_cases.py + REPORT.md）。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

## 0. 当前实现能力（realtest 已验证）

- **文件库模式**：`library_path` 目录递归扫描，逐文件 SHA-256+大小+类型+读权限；内联模式兼容旧行为。提取链：内联 → 侧车 `.txt/.ocr.txt`（带 OCR 置信度）→ 自研 stdlib PDF 文本层（`pdf_min.py`，双栏聚类+阅读顺序）→ DOCX zip+xml → 纯文本 → 诚实 gap（加密/损坏/无文本层/无提取器/无权限分码）。
- **别名与版本组**：同 SHA-256 异名归并为单一证据源；管理性版本说明归版本组、canonical=版本序最大者；命中旧版给 `stale_version`、命中副本给 `duplicate_copy`，不作独立计数。
- **跨文件语义对齐**：`rules/retrieval_rules.json` 的 `query_expansion` 词表（缩写/同义/上下位，如 采样率↔sampling rate、PCR↔polymerase chain reaction、eDNA↔metabarcoding），概念级 term_match，逐条扩展带 `via=expansion:<group>` 出处，可解释、无向量库。
- **硬过滤不放宽**：年份/项目号/类型/批次/版本/权限/扫描件逐文件判定，被拦命中单列 `filtered_but_relevant`；空结果附 `coverage_note`（库内无支撑 vs 无权限不可判定）。
- **回链与只读**：file+sha256+version+page/page_label+section_path+char_range+段落首句+逐字片段自检；双栏页给阅读顺序；恢复/索引操作只写建议到 `ops_log`（落盘 `ops_log.json`），前后哈希对照证明输入库未动。

> 分类：资料管理 ｜ 赛题：challenge-139745c7 ｜ ARMI：ARMI:2611.000082v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-139745c7)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：库已经在了，解决"如何被准确找到"：跨文件语义检索 → 落到章节标题/页码/段落 → 按条件硬过滤 → 给出可点击、可逐字验证的证据回链。题面明确不负责开放网络检索、全文精读与综述写作。
- **输入**：`/input/case.json` + `task.md`；工作单为"授权修复的真实 9 文件级资料库"（真实文件、别名、行政性版本说明、元数据、检索与问答请求、恢复与索引操作）。可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`office-renderer`、`browser-automation`、`scientific-python`、`latex-compiler`、`font-discovery`；工作区 `/app/work`，交付 `/app/output`。
- **输出契约**：`/app/output/result.json` 三段——① 主结果（每个提问的命中文件清单、章节路径、页码（印刷页+物理页双记）、段落首句、原文片段、过滤条件逐项满足情况、候选排序与得分）；② 证据记录（文件路径+版本/哈希、索引构建参数与脚本版本、chunk 边界、检索路径 trace、`case_id` 关联）；③ 状态说明（完成/部分完成、空结果原因、无权限/加密/无文本层文件清单、条件无法满足时的如实报告、不确定性）。
- **提交形态**：只交 `SKILL.md` 与必要文本资源（≤50 文件、单文件 ≤500KB、合计 ≤2MB、无符号链接）；官方 Harness 跑 3 案例 × 3 重复，预算 64 请求 / 900 秒 / 16384 tokens，禁止自造基准分。
- **评分重心**：40 分的关键是"定位到的位置就是原文那一段"（15）与"过滤条件是硬约束、不得放宽"（15）；完整（5）看过滤维度是否逐项交代；可核验（5）看别人能否按记录回到同一段。最容易丢分：印刷页码与 PDF 物理页错位、双栏排版串行、跨页表格与图注被切断、只命中文件不给章节、扫描件无文本层却当作已检索。
- **裸 Agent 典型失败**：
  1. 用 `grep`/文件名搜出整篇文档就交付，章节级问题回答成"该文件第 3 节可能相关"，页码给的是 PDF 物理页而非印刷页。
  2. 过滤条件被悄悄放宽：要求"2021-2023 起、项目号 P-17、非扫描件"，命中不足时自动去掉一条却不声明。
  3. 双栏论文按 y 轴排序串行读，左右栏拼成一句话；跨页表格只取首页，引用片段在原文里根本连不起来。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| infiniflow/ragflow | 94378 | Go | Apache-2.0 | 参考实现 | 把"分块+位置+引用"当一等公民：chunk 带 doc/chunk/position 信息，layout-aware 解析对双栏与表格友好，metadata filter 可直接做硬过滤 |
| paperless-ngx/paperless-ngx | 48250 | Python | GPL-3.0 | 工具库 | 台账层：sha256、原始路径、对应者、标签、ocr 结果，是回链里的"文件与版本"来源 |
| khoj-ai/khoj | 38170 | Python | AGPL-3.0 | 参考实现 | 检索→带引用回答的端到端形态，其引用结构与"未找到"话术可作输出 schema 参考 |
| TagStudioDev/TagStudio | 7303 | Python | GPL-3.0 | 参考实现 | 文件条目 + tag + 哈希去重，解决"副本/旧版本被当成独立证据"的判据问题 |
| O0000-code/paper-search-pro | 236 | HTML | Apache-2.0 | SKILL 骨架 | 纯 SKILL.md 形态的"初筛→精读→定位"三段流程，可直接改成库内检索的调用骨架 |
| Mayuqi-crypto/everything-search-skill | 70 | PowerShell | MIT | 工具库 | 文件名层毫秒级召回，适合做第一层候选集（再用语义层重排），避免全库向量化开销 |
| hermes-labs-ai/zer0dex | 64 | Python | Apache-2.0 | 参考实现 | 双层记忆：人可读的 Markdown 索引 + 向量层，正好对应"索引可复核、语义可召回" |
| sfrangulov/minirag-mcp | 60 | Python | MIT | 工具库 | 本地 hybrid 检索（语义 + BM25 加权）MCP server，离线兜底基线，接口即插即用 |
| DokRaphael/harbor-preindex | 2 | Python | MIT | 工具库 | 把任意目录预索引成语义工作区，参考其增量索引与失效重建策略 |

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| Knowledge | danielmiessler | 维护带类型的人/公司/想法/研究笔记知识档案，可借其 typed graph + 索引入口设计检索面 | https://github.com/danielmiessler/LifeOS/tree/main/LifeOS/install/skills/Knowledge |
| rag-system-builder | openclaw | 本地 RAG 系统搭建（离线文档处理、嵌入、检索），提供完整流水线的步骤骨架 | https://github.com/openclaw/skills#skills~alexfeng75~rag-system-builder |
| the-librarian | rochyroch | 用 TurboVec 建轻量量化文档索引并检索，适合做低预算、可离线的最小索引方案 | https://clawhub.ai/skills/the-librarian |
| chroma | davila7 | 本地向量库技能：带元数据的嵌入存储、相似度与过滤查询，对应本题"过滤条件硬执行" | https://github.com/davila7/claude-code-templates#cli-tool~components~skills~ai-research~rag-chroma |
| gemini-document-processing | mrgoonie | 用视觉模型处理 PDF：抽文本/图/表/结构化数据，可补无文本层扫描件的解析路径 | https://github.com/mrgoonie/xxxnaper#.claude~skills~gemini-document-processing |
| grepai-quickstart | yoanbernabeu | 语义搜索的安装与首次查询指南，参考其索引/查询命令分层，便于写成 SKILL 调用序列 | https://github.com/yoanbernabeu/grepai-skills#skills~getting-started~grepai-quickstart |

## 4. 推荐起点与复用方案

- **首选**：`infiniflow/ragflow` —— 它是这批里唯一把"解析分块→位置元数据→带引用检索"整条链路做齐且 Apache-2.0 可商用的实现，直接对上"章节级定位 + 证据回链"这两个 15 分主项。复用动作：① 借其 chunk schema（`doc_id`/`chunk_id`/`position`/`page`/`bbox`）定义本题 `evidence` 结构；② 用其 PDF layout-aware 解析替代固定窗口切分，解决双栏/表格/图注；③ 用其 metadata filter 语法做年份、项目号、批次、版本、权限的硬约束执行。
- **次选**：`sfrangulov/minirag-mcp` —— 补两点：极轻的本地 hybrid 检索（BM25 兜底语义漏召回，对术语/编号类查询尤其重要），以及 MCP 形态便于作为 Agent 工具直接挂载；当向量化预算不足时降级到它仍能给可用结果。
- **组合建议**：内核与适配层分开，索引与检索解耦：

```
SKILL.md                    # 触发条件、提问解析、检索→定位→回链顺序、失败分支
core/parse.py               # 各格式→块（保留页码/bbox/章节路径）
core/index.py               # 索引构建 + 增量失效（按 sha256）
core/retrieve.py            # 候选召回（文件名层 + BM25 + 向量）+ 重排
core/locate.py              # 印刷页↔物理页换算、章节归属、段落首句
core/evidence.py            # 回链结构 + 逐字可验证性自检
adapters/case_input.py      # case.json / task.md → 提问与过滤条件
adapters/filters.py         # 硬过滤执行器：不可放宽，不满足即如实报告
adapters/degrade.py         # 无文本层/加密/权限不足的降级与声明
adapters/report.py          # result.json 三段拼装
```

- **分工理由**：`core/` 全部与题目无关，换一个库、换一份提问集可直接复用；`adapters/` 承载题面约定（过滤字段、页码口径、状态枚举），评审看差异只改这里。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 每条回答 = 命中文件 + 版本/哈希 + 章节路径 + 印刷页/物理页 + 段落首句 + 原文片段（≥1 句逐字）；过滤条件逐项打勾并写满足/不满足；空结果明确写"库内无依据"及缺什么，而不是拿相似段落凑 |
| 使用增益 25 | 固定对照：章节定位命中率、页码正确率、过滤条件违反次数（应 0）、单问平均调用轮次；同库同问跑 Skill/裸 Agent 各 3 次，同一批提问与同一金标准页段落比较，只报可复算的差 |
| 创新增益 15 | 三处改进：① 印刷页↔物理页偏移自动校准（用页脚文本与页面序列对齐，不写死偏移）；② 双栏/跨页块合并规则（按栏内 y 连续 + 语义断句，而非简单排序）；③ 回链自检器——把给的片段回到原文按字符比对，不一致即降级为"定位不确定" |
| 可靠体验 10 | 失败分级：`no_text_layer`（转 OCR 或声明不可检索）/`encrypted`/`no_permission`/`path_stale`/`empty`；每级给可执行处置与日志；索引构建超预算时先出小库可用索引 + 明确覆盖范围 |
| 生态贡献 10 | 交付：目录与索引约定、提问解析规范、过滤字段字典、页码口径说明、定位规则、金标准示例（标明示例）、限制（不适用加密/纯图表 PDF）；core 可脱离本题复用 |
| GitHub +5 | 仓库结构：`SKILL.md` / `core/` / `adapters/` / `docs/`（字段字典、页码口径、限制）/ `examples/`（示意提问与输出，标明示例）/ `tests/`（定位与过滤的小样本回归）/ `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `detail.md` 与 `/input/case.json`，记录提问字段、过滤维度、页码口径、状态枚举，写进 `adapters/`。
- [ ] 写 `core/parse.py`：PDF（含双栏/表格/图注）、Word、Excel、PPT、扫描件统一转块，保留 page/bbox/章节路径。
- [ ] 写 `core/index.py`：按 sha256 建索引与增量失效；重复副本只在索引里保留一条并标注 alias。
- [ ] 写 `core/retrieve.py`：文件名层筛选 → BM25 → 向量，三路融合并保留各路贡献（可解释排序）。
- [ ] 写 `core/locate.py`：页脚对齐算偏移、章节标题树归属、段落首句截取。
- [ ] 写 `adapters/filters.py`：年份/项目号/类型/批次/版本/权限六类硬过滤，未命中即返回 `unsatisfied` 而不放宽。
- [ ] 写 `core/evidence.py`：回链自检（片段与原文逐字比对）+ 置信分级，低置信显式标注。
- [ ] 写 `SKILL.md` 与 `examples/`，跑通"指定库→提问→定位结果"一次完整调用并核对 result.json 三段字段。