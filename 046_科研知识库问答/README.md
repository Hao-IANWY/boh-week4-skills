# 046｜科研知识库问答

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 046`；工程标准见 `answer/_shared/README.md`。
>
> **v0.2.0 真实文件库升级**（对标 047 优化力度）：支持 `materials_root` 目录扫描模式——逐文件 SHA-256/大小/权限建档，同哈希别名合并、管理性版本说明归组（canonical）、纯标准库 PDF 文本层提取（`scripts/pdf_min.py`）+ DOCX 段落读取 + OCR 侧车；权限不足/加密/损坏/无文本层/无提取器五码诚实跳过；不确定性三态 supported / partially_supported / insufficient_evidence；恢复/索引操作只写 `ops_log` 建议（`input_readonly=true`）并用前后哈希对照证明输入库未动。回归 `python3 answer/046_科研知识库问答/scripts/test_pipeline.py`（8 用例，含 realtest 4 案例端到端仿真）；真实画像与运行证据见 `review/046_realtest/REPORT.md`。
>
> 本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：资料管理 ｜ 赛题：challenge-80dd5ddc ｜ ARMI：ARMI:2611.000089v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-80dd5ddc)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：在一个含重复副本与历史版本的多格式资料库上做带来源引用的问答：每个关键论断落到具体文件、具体版本、具体段落；库内确实没有依据时敢说"没有"并说明缺什么；别人拿记录能把同一问答重走一遍。
- **输入**：`/input/case.json` + `task.md`；工作单为"授权修复的真实 9 文件级资料库"（真实文件、别名、行政性版本说明、元数据、检索与问答请求、恢复与索引操作）。可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`office-renderer`、`scientific-python`、`browser-automation`、`latex-compiler`、`font-discovery`；工作区 `/app/work`，交付 `/app/output`。
- **输出契约**：`/app/output/result.json` 三段——① 主结果（每个问题的答案、断言级引用列表（文件路径+版本/哈希+页码/段落+原文片段）、证据强度标注（直接支持/间接提示/仅背景）、答案三态（有据/证据冲突/库内无据）、缺失要素说明、检索范围与命中统计）；② 证据记录（索引参数、chunk 边界、检索路径 trace、去重与版本选择理由、`case_id`、脚本版本）；③ 状态说明（完成/部分完成、不可读文件、无权限、扫描件无文本层、空目录、矛盾证据的并列呈现与不确定性）。
- **提交形态**：只交 `SKILL.md` 与必要文本资源（≤50 文件、单文件 ≤500KB、合计 ≤2MB、无符号链接）；官方 Harness 3 案例 × 3 重复，预算 64 请求 / 900 秒 / 16384 tokens，禁止自造分数。
- **评分重心**：40 分主项是"引用真实、定位准确、答案与证据逐句对应"（15）与"覆盖文件类型/元数据/目录层级/重复文件/版本差异/权限六维"（15）。最容易丢分：引用指向的文件段落对不上、把旧版本当现行版本、重复副本被当两条独立证据、无据问题硬凑答案。
- **裸 Agent 典型失败**：
  1. 答案通顺但引用是"某文件大致提到"，页码/段落缺；把摘要当全文引用，读者回不到原文。
  2. 重复下载与历史版本各被当一条证据，制造"两条文献都支持"的假象；版本冲突不声明。
  3. 命中不足时用常识补答案，把"库内无依据"写成看似有据的结论。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| open-webui/open-webui | 159005 | Svelte | NOASSERTION | 工具库 | 文档库（#doc 引用）+ 多模型问答前端，参考其"引用卡片"字段与知识库作用域声明 |
| infiniflow/ragflow | 94378 | Go | Apache-2.0 | 参考实现 | layout-aware 解析 + chunk 位置元数据 + 带引用检索，是回链可靠性的底座 |
| run-llama/llama_index | 52941 | Python | MIT | 工具库 | `CitationQueryEngine` 的 citation 结构（source node + 文本片段）可直接当输出 schema；metadata filter 做版本/权限过滤 |
| khoj-ai/khoj | 38170 | Python | AGPL-3.0 | 参考实现 | 自托管"问自己的文档"端到端形态，含"没有相关信息"的拒答行为与来源列举 |
| MODSetter/SurfSense | 16892 | Python | Apache-2.0 | 参考实现 | 多源（文档/网页/视频）入库 + 引用问答的工程化实现，参考其来源去重与权限分区 |
| better-vault/better-vault | 105 | TypeScript | NOASSERTION | 参考实现 | "与来源对话"的引用交互与来源固定（pin）机制，可借其证据面板字段设计 |
| smallnest/notex | 245 | Go | Apache-2.0 | 参考实现 | 轻量 NotebookLM 替代实现，结构简单可读，适合当最小骨架改题 |
| LCccode/Karpathy-wiki-graph | 6 | Python | MIT | SKILL 骨架 | 目录→三层 wiki（文章/概念/图）+ 低成本 RAG 的 SKILL 形态，给"知识结构如何组织"的落地模板 |
| Albert-Libra/nanobot-zotero-bridge | 3 | Python | — | 参考实现 | L0→L3 论文处理流水线（元数据→摘要→全文→总结）+ FTS5/向量混合检索，正好对上分层证据粒度 |

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| mmq | s-gryt | 知识库查询技能：先读会话上下文，再读根索引，再落到相关条目，分层检索顺序可直接借用 | https://github.com/s-gryt/memory-mason#.cursor-skills-mmq |
| clarity-gate | frmoretto | RAG 入库前的"认知质量"闸门：文档是否够格入库、字段是否完整，可当缺证判断的前置过滤器 | https://github.com/frmoretto/clarity-gate#.codex~skills~clarity-gate |
| rlama | tdimino | 用 RLAMA + Ollama 建本地知识库并查询，离线可跑的最小可用链路 | https://github.com/tdimino/claude-code-minoan/tree/main/skills/integration-automation/rlama |
| llamaindex | davila7 | LlamaIndex 技能：文档摄取、索引、检索与引用问答的 API 用法速查 | https://github.com/davila7/claude-code-templates#cli-tool~components~skills~ai-research~agents-llamaindex |
| afrexai-knowledge-management | openclaw | 知识管理流程技能，覆盖知识归档、检索与更新，可借其"库内变更"处理规范 | https://github.com/openclaw/skills#skills~1kalin~afrexai-knowledge-management |
| ml-knowledge-index | NatiLevyy | 问题路由到专门技能的索引式入口，参考其"先判问题类型再选检索策略"的设计 | https://github.com/NatiLevyy/claude-useful-skills/tree/master/skills/ml-dl-skills/ml-knowledge-index |

## 4. 推荐起点与复用方案

- **首选**：`run-llama/llama_index` —— Apache/MIT 系里最完整地把"摄取→索引→带引用回答"抽象成可替换组件的库，`CitationQueryEngine` 的引用结构正好是本题 `assertions[].evidence` 的现成 schema，metadata filter 直接承担版本与权限过滤。复用动作：① 借 citation 输出结构（source node id + 文本片段 + 元数据）定义回链字段；② 用 node postprocessor 做重复副本/旧版本剔除，保证"一条证据一个来源"；③ 用 ingestion pipeline 的 docstore 记 sha256 与版本，接上证据记录段。
- **次选**：`khoj-ai/khoj` —— 补"敢说没有"的部分：它的检索置信不足时的拒答与"未找到相关内容"的结构化回执，正好对上本题的三态答案与不确定性提示；同时提供文件+网页混合库的组织方式，便于声明检索范围。
- **组合建议**：内核与适配层分开：

```
SKILL.md                     # 触发条件、问答顺序（判型→检索→证据→作答→缺证声明）、失败分支
core/ingest.py               # 多格式→块，保留页码/段落/章节路径 + sha256
core/registry.py             # 文件台账：路径/哈希/版本族/权限/别名
core/retrieve.py             # 召回（BM25+向量）→ 去重（按哈希/内容指纹）→ 重排
core/answer.py               # 断言级作答：每句挂证据 ID，三态判定
core/verdict.py              # 有据/冲突/无据 判定 + 证据强度分级
adapters/case_input.py       # case.json / task.md → 问题集与库范围
adapters/scope.py            # 版本/权限/类型过滤与"检索范围声明"
adapters/absence.py          # 缺证判据：缺什么文件/什么字段/什么版本
adapters/report.py           # result.json 三段拼装
```

- **分工理由**：`core/` 只处理"证据可信度"这一通用问题，可复用到 044/047/049；`adapters/` 承载本题的库范围、权限模型和缺证话术，评审改判定标准只动这里。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 断言级引用：答案拆成独立论断，每条挂 `evidence_id`（文件+哈希+页码/段落+原文片段≥1 句逐字）；三态显式标注；冲突证据并列呈现并说明分歧点；无据问题输出"缺什么"清单（缺文件/缺全文/缺某年某项目） |
| 使用增益 25 | 固定对照：引用可核实率、论断-证据对齐率、重复副本误用次数（应 0）、无据问题的正确拒答率；同库同问跑 Skill/裸 Agent 各 3 次，比同一金标准段落集，只报可复算差值 |
| 创新增益 15 | ① 版本感知检索：同族多版本只取现行版作答，同时保留旧版差异说明；② 去重指纹（正文 shingle 哈希）压掉改名副本，避免假"多源支持"；③ 缺证判据器——把"没找到"细分为库内无该主题/有文件但不可读/有全文但无该断言 |
| 可靠体验 10 | 失败分级 `unreadable`/`encrypted`/`no_text_layer`/`no_permission`/`empty_dir`/`version_conflict`；每级给处置 + 日志；超预算先出"已覆盖范围 + 未覆盖范围"的部分答案，不伪装完整 |
| 生态贡献 10 | 交付：库约定与元数据字段表、引用格式规范、三态与证据强度定义、缺证判据说明、最小示例（标明示例）、不适用边界；core 可跨题复用 |
| GitHub +5 | 仓库结构：`SKILL.md` / `core/` / `adapters/` / `docs/`（字段表、引用规范、缺证判据）/ `examples/` / `tests/`（引用可核实性回归）/ `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `detail.md` 与 `/input/case.json`，确定库范围、权限模型、问题集字段与三态枚举，写入 `adapters/`。
- [ ] 写 `core/ingest.py`：PDF/Word/Excel/PPT/扫描件转块，保留页码、段落序号、章节路径、sha256。
- [ ] 写 `core/registry.py`：文件台账 + 版本族（现行/历史/别名副本），供检索时按版本取舍。
- [ ] 写 `core/retrieve.py`：混合召回 + 内容指纹去重 + 重排，并记录每路贡献以解释排序。
- [ ] 写 `core/answer.py`：论断拆分 + 逐条挂证据；无证据的句子禁止出现在最终答案里。
- [ ] 写 `core/verdict.py` + `adapters/absence.py`：三态判定与缺证细分，输出"缺什么"清单。
- [ ] 写 `adapters/scope.py`：版本/权限/类型硬过滤，未满足即声明而非放宽。
- [ ] 写 `SKILL.md` 与 `examples/`，跑通一轮"指定库→提问→带引用答案 + 缺证说明"，并核对 result.json 三段。