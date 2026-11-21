# 056｜论文逻辑与论证审查

> **已实现可运行 Skill（2026-11-20 按真实案例画像升级）**：同目录 `SKILL.md` + `scripts/pipeline.py`（全文获取链：内联→侧车→stdlib PDF/DOCX→诚实 gap）+ `scripts/pdf_min.py` + `scripts/test_pipeline.py` + `rules/`（argument_rules/facets/severity/reason_codes/contract_routing）+ `examples/min_example/`；真实全文仿真在 `review/056_realtest/`（英文 3500+ 词全文、中文全文、facets 定向、PDF 无侧车/截断/扫描件 6 案例）。验收：`python3 answer/_shared/verify.py --only 056`，回归：`python3 -B answer/056_论文逻辑与论证审查/scripts/test_pipeline.py`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：学术审查 ｜ 赛题：challenge-16d4414d ｜ ARMI:2611.000100v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-16d4414d)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：交付一个可被官方 DeepSeek Harness 加载的 Skill，对给定论文识别「论点—证据—推理链—章节衔接」中的逻辑问题，产出带位置、类型、严重度和可执行建议的问题清单；3 个固定案例，不加载/加载各跑 3 次对照。
- **输入**：`/input/case.json`（case_id、论文标识、审查范围）+ `task.md` + 案例实际论文（PDF/LaTeX；PDF 走 pdf-text-tool，图表与图注走 pdf-render-tool，扫描件走 ocr-tool）。scientific-python 用于跨章节数值一致性抽查。
- **输出契约**：`/app/output/result.json` 按题面三段回填并保留 `case_id` 与全部 `output_contract` 字段——
  (1) **研究产物**：`claims[]`（主张 + 限定条件 + 来源类型：作者主张/引用他人/假设情形）+ `findings[]`（`location_span / quote / issue_type / severity / evidence / fix`），覆盖论点抽取、证据支撑、推理链完整性、章节衔接与前后一致性、严重度与修改建议五个方向；
  (2) **证据矩阵**：每条主张对应的支撑证据（本文实验位置 / 所引文献位置）、跨章节一致性核对表（摘要-引言-方法-结果-讨论-图表）、推理链图谱（前提 → 中间结论 → 主结论，标出断裂边）；
  (3) **不确定性**：无法判断作者意图、原文表述歧义、跨章节信息不足、图表读不出、材料不足以判定而标 `needs_review` 的条目。
- **评分重心**：40 分落在「结果正确性」与「结果可核验性」——报出的问题必须真实存在且能回到原文某一句、定位精确到句而非章节名、修改建议必须可执行（补什么证据、改哪一句、把哪个结论强度降下来）；同时要压住误报：作者有意设置的对照组、明确标注的假设情形、转述他人观点不能判成自相矛盾。严重度一律标"中"是典型失分。
- **裸 Agent 典型失败**：
  1. 输出「本文结构完整、论证较为充分」式感想，或只给章节名不落到句子；
  2. 把有意设置的对照/假设情形/转述观点误报为矛盾；严重度全部标"中"；
  3. 建议写成"建议补充论证""建议加强逻辑"；长文被截断仍宣称完成全文审查，跨章节矛盾整段漏检，且不留可复核记录。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| vale-cli/vale | 6404 | Go | MIT | 工具库 | 句级定位引擎（报行/列），把逻辑检查的结论落到原句而不只是章节名；规则可自定义 |
| WenyuChiou/ai-research-skills | 676 | Python | MIT | SKILL 骨架 | 通用 SKILL.md 目录（文献综述/研究设计/项目管理等），提供官方加载格式与文件划分范式 |
| allenai/scifact | 282 | Python | NOASSERTION | 评测数据 | claim-evidence 三分类数据与模型，用来校准"证据是否支撑该强度的主张"这一判定 |
| vene/marseille | 66 | Python | BSD-3-Clause | 工具库 | 论证结构挖掘（线性 + LSTM 引擎），可从段落抽前提/结论成分，作为推理链图谱的输入层 |
| UKPLab/acl2017-neural_end2end_am | 59 | Python | NOASSERTION | 参考实现 | 端到端论证成分标注（claim/premise/major claim），提供成分标签体系与训练/推理脚本写法 |
| Hellisotherpeople/DebateSum | 55 | Python | —（未声明） | 评测数据 | 大规模论证摘要数据集（含论点-论据配对），可作长文论证抽取的弱监督语料 |
| recap-utr/argument-graph-mining | 28 | Jupyter Notebook | Apache-2.0 | 参考实现 | 文本 → 论证图的完整 pipeline（节点=主张，边=支持/攻击），几乎就是本次"推理链图谱"的现成实现 |
| pkargupta/tree-of-debate | 20 | Python | Apache-2.0 | 参考实现 | 把论文变成 LLM persona 互相辩论，自动暴露论点冲突与隐含假设，可作"替代解释未排除"的检查手段 |

（池内另有 `UKPLab/naacl18-multitask_argument_mining`（★39，Apache-2.0）、`gaoyixu/CAIL2020-Argument-Mining`（★40）、`trusthlt/mining-legal-arguments`（★83，Apache-2.0）可读；`chrisneagu/FTC-Skystone-...`、`Rynkll696/HH`、`Aryia-Behroziuan/References`、`zaynsdev/ETHOS` 语义无关或体量过小。）

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| claimify | leegonzales | 从话语中抽取并结构化主张、构建论点地图、识别逻辑关系与隐含假设，直接对应「论点与主张抽取」与「推理链完整性」 | https://github.com/leegonzales/AISkills#Claimify~claimify |
| consistency-checker-across-manuscript | aipoch | 跨标题/摘要/方法/结果/讨论的一致性核查，正是题面「章节衔接与前后一致性」的现成检查器 | https://github.com/aipoch/medical-research-skills#awesome-med-research-skills~Academic Writing~consistency-checker-across-manuscript |
| deep-reading-analyst | ginobefun | 论文/文章的深度解析框架（结构、论证、证据强度、局限），可作通读层的分面提示骨架 | https://github.com/ginobefun/deep-reading-analyst-skill#src-deep-reading-analyst |
| paper-self-review | Galaxy-Dawn | 投稿前自审流程（结构、论证、表达），提供中文语境的审查顺序与输出形态 | https://github.com/Galaxy-Dawn/claude-scholar#skills~paper-self-review |
| aeri-theory-model | brycewang-stanford | 面向理论模型构建的检查（假设、机制、可检验推论），适合判定"假设情形"与"作者主张"的边界 | https://github.com/brycewang-stanford/Awesome-Journal-Skills#AER-Insights-Skills~skills~aeri-theory-model |
| aeri-literature-positioning | brycewang-stanford | 文献定位与贡献陈述的检查，用于判断"引言承诺的问题是否在结果章节交付" | https://github.com/brycewang-stanford/Awesome-Journal-Skills#AER-Insights-Skills~skills~aeri-literature-positioning |

## 4. 推荐起点与复用方案

- **首选**：`recap-utr/argument-graph-mining` —— 本题真正的技术难点是把"论证"变成可检查的对象。它是池内唯一提供完整「文本 → 论证图」pipeline（节点=主张、边=支持/攻击）且许可干净（Apache-2.0）的实现，输出可直接充当我们的推理链图谱与断裂边定位，而不是再写一遍论文总结。复用动作：跑它的图抽取，把节点映射成 `claims[]`、边映射成 `evidence_links[]`，对"无支撑边的节点""只有攻击边的主结论""跨章节不可达的主张"三类打缺陷类型，再交给严重度模块。
- **次选**：`allenai/scifact` —— 补"证据强度是否匹配表述强度"的金标准：用它的 SUPPORT/REFUTE/NEI 标注校准"结论超出数据"与"引用不支持观点"两类缺陷的边界，并把误报率量化进对照报告。
- **组合建议**：
  - 内核（跨 053–057 复用）：`skill/kernel/parse_doc.py`（带 span 的段落树 + 图表图注）、`skill/kernel/claim_extract.py`（主张抽取 + 来源类型三分类 + 限定条件挂接）、`skill/kernel/arg_graph.py`（论证图构建与断裂边检测）、`skill/kernel/cross_section.py`（摘要/引言/方法/结果/讨论一致性矩阵）、`skill/kernel/severity.py`（三问定级）、`skill/kernel/fix.py`（可执行建议模板：补哪类证据 / 改哪句 / 降多少强度）；
  - 适配层（本题专用）：`skill/adapter/case_scope.py`（审查范围与章节启用）、`skill/adapter/vale_bridge.py`（把结论落到句级 location）、`skill/adapter/guard.py`（误报抑制：有意对照、显式假设、转述他人观点三类白名单）；
  - 资源：`skill/templates/claim_schema.json`、`issue_types.md`（跳跃/隐含假设/替代解释未排除/相关因果混用/章节未交付/强度越界）、`severity_anchors.md`、`fix_patterns.md`。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 主张清单（含来源类型与限定条件）+ 论证图（节点/边/断裂边）+ 跨章节一致性矩阵 + 句级定位的 findings；每条 fit 落到具体句子，严重度按三问定级并给依据 |
| 使用增益 25 | 同论文同版本对照白板；量化：真实逻辑缺陷召回、句级定位命中率、误报条数（尤其三类白名单命中）、建议可执行率、人工重写条数 |
| 创新增益 15 | 论证图驱动的断裂检测、主张来源三分类（作者/引用/假设）以压制误报、跨章节"承诺-交付"矩阵、建议模板化（补证据/改表述/降强度），均有反例集 |
| 可靠体验 10 | 长文超预算 → 分章审查 + 跨章程一致性合并，并在覆盖声明中写明未审章节（不宣称全文审查）；图表读不出 → 标 `unverifiable`；表述歧义 → 列两种解释 + `needs_review`；截断时输出 partial 与缺口清单 |
| 生态贡献 10 | issue 类型表、严重度锚点、建议模板、claim schema、论证图格式、题集与评分表可复用；文档写清不适用边界（不含润色、查重、参考文献格式、数据重分析、投稿策略） |
| GitHub +5 | `skill/ kernel/ adapter/ templates/ examples/ docs/`；根 README 带最小调用示例（标注示例）；根放 `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `case.json`/`task.md`，确认论文版本、审查范围与章节清单
- [ ] 解析论文 → 带 span 段落树 + 图表图注（扫描页 OCR 并标来源）
- [ ] 写主张抽取与来源三分类（作者主张/引用他人/假设情形），加限定条件
- [ ] 接通论证图 pipeline（或移植其抽取规则），产出节点/边与断裂边清单
- [ ] 建跨章节一致性矩阵：引言承诺 → 方法/结果交付、摘要 → 正文结论、图表 → 正文描述
- [ ] 写缺陷类型表与三问式严重度锚点，配反例（有意对照/显式假设/转述）
- [ ] 写可执行建议模板，逐条检查是否仍是"建议加强逻辑"
- [ ] 接 vale 做句级定位，确保 location_span 能直达原句
- [ ] 写 `SKILL.md` + 最小调用示例（标注示例），用 Scifact 子集测证据强度判定的一致率
- [ ] 建 GitHub 仓库并放 `GITHUB_URL.txt`，未登录窗口自检
