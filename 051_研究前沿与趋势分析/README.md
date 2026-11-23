# 051｜研究前沿与趋势分析

## 0. 快速开始（Harbor 正式运行）

```bash
python3 -B /skills/weekly-skill/scripts/pipeline.py \
  --case /input/case.json --work /app/work --out /app/output
python3 -B /skills/weekly-skill/scripts/validate.py \
  --case /input/case.json --result /app/output/result.json
```

产物：`/app/output/result.json`（契约与证据台账）、`/app/output/report.md`（人类可读研究产物）、`/app/work/plan.json`（前瞻计划与协议）。本地最小示例见 `examples/min_example/`；示例为合成材料，不代表正式案例结论。

正式运行分四步：确定性管线 → 模型读 `work/synthesis_input.json` 写 `work/synthesis.json` → 回跑管线校验引用/数字并生成 `review.md` → `validate.py`。详见 `SKILL.md` 的“执行”节；无模型或校验失败时，`report.md` 是可交付的确定性回退。

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 051`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：深度研究与文献综述 ｜ 赛题：challenge-073af7d3 ｜ ARMI:2611.000092v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-073af7d3)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：交付一个可被官方 DeepSeek Harness 加载的 Skill，把一个真实领域主题做成「时间趋势 + 主题演化 + 代表性工作 + 研究空白」四类可检查产物；组织方 3 个固定案例，不加载/加载 Skill 各跑 3 次对照。
- **输入**：`/input/case.json`（case_id、主题、时间范围、数据口径约定）+ `task.md` + 案例实际材料（题录导出/CSV/全文 PDF；PDF 用 pdf-text-tool / pdf-render-tool / ocr-tool 真解析）。可用 scientific-python 做统计，browser-automation 取公开文献库页面。
- **输出契约**：`/app/output/result.json` 按题面三段回填，并保留 `case_id` 与全部 `output_contract` 字段——
  (1) **研究产物**：年度/时间窗产出量分布表与增长速率、拐点判定（区分离散波动与真实拐点）、阶段划分及依据、主题的出现/合并/分裂/衰退序列、各阶段代表作清单与入选口径、研究空白清单（含排除证据）；
  (2) **证据矩阵**：每条趋势/演化/空白主张 → `record_id`、年份、主题标签、支撑统计量、原文定位（页-段-句）、代表作入选理由；
  (3) **不确定性**：年份缺失或口径不一（在线发表/见刊/预印本）、术语漂移与同名异义、文献量爆炸导致抽样、样本过小、结论边界。
- **评分重心**：40 分里「结果正确性」最易丢分——年份口径混用会把趋势算反，主题热度冒充「前沿」，总被引排序冒充代表作，「没检索到」冒充研究空白；「结果可核验性」要求时间切片口径、主题归并规则、统计表能被独立复算。
- **裸 Agent 典型失败**：
  1. 把检索结果前 N 条直接当「前沿」，不给年度分布与增长率，趋势无统计支撑；
  2. 只按总被引取代表作，遗漏长尾中的方法首创工作，且不写入选口径；
  3. 空白判据缺失，把检索遗漏（词表不全）当成领域空白；年份字段缺失时悄悄丢样本、不记缺失量。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| MaartenGr/BERTopic | 7965 | Python | MIT | 工具库 | `topics_over_time` 直接产出「时间 × 主题」矩阵，是主题演化与阶段划分的计算底座 |
| lda-project/lda | 1316 | Python | MPL-2.0 | 工具库 | 轻量 LDA 基线；用作对照，证明 BERTopic 的主题归并不是单模型偶然结果 |
| massimoaria/bibliometrix | 676 | R | NOASSERTION | 参考实现 | 年度产出、三领域图、主题图的标准文献计量口径，可照它的字段定义与清洗规则实现统计脚本 |
| pybliometrics-dev/pybliometrics | 510 | Python | NOASSERTION | 工具库 | Scopus API 封装：按年份/学科切片拉题录，天然带年份与出版状态字段 |
| J535D165/pyalex | 434 | Python | MIT | 工具库 | OpenAlex 全量题录（含 `publication_year`、`type`、引用计数），是年份归一与口径声明的数据源 |
| ata381/BibMedEd | 3 | Python | MIT | 参考实现 | PubMed + OpenAlex 双源的领域趋势分析平台，可直接读它的检索→统计→可视化串联方式 |
| danyIkram/generative-ai-research-analysis | 2 | Jupyter Notebook | —（未声明） | 数据集 | 基于 OpenAlex 导出的主题趋势 Jupyter 分析，可当小样本复现实验数据与对照脚本 |
| jlmayorgaco/ml-biblio-synth-tool | 6 | Jupyter Notebook | LGPL-2.1 | 参考实现 | Zotero 接入 + ML/NLP 的文献计量自动化，看它如何处理人工整理库与自动抓取库的合并 |

（池内共 8 条，已全列；`pybliometrics-dev/pybliometrics` 与 `massimoaria/bibliometrix` 的 license 字段为 NOASSERTION，商用前须自行核对上游条款。）

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| openalex-database | K-Dense-AI | Python 工具集查询 OpenAlex 2.4 亿+ 文献，支持检索、引文追踪、研究趋势分析与文献计量 | https://github.com/K-Dense-AI/claude-scientific-skills#scientific-skills~openalex-database |
| openalex-database | Microck | 同族的轻量版：API 客户端 + 常用查询辅助函数，按作者/机构查找，适合塞进内核做数据层 | https://github.com/Microck/ordinary-claude-skills#skills_categorized~astronomy-physics~openalex-database |
| citations | ZimoLiao | 查看高被引论文排名并从在线源重新抓取引用计数，天然对应「代表作遴选」与引用数时效问题 | https://github.com/ZimoLiao/scholaraio#.claude~skills~citations |
| citation-chasing-mapping | openclaw | 识别领域奠基性论文并绘制引文关系图，补「代表作覆盖各阶段」与「主题谱系」 | https://github.com/openclaw/skills#skills~aipoch-ai~citation-chasing-mapping |
| preprint-surveillance-finder | aipoch | 追踪最新预印本与新兴主题，是「前沿」信号的正确来源之一，可与见刊数据分口径对照 | https://github.com/aipoch/medical-research-skills#awesome-med-research-skills~Evidence Insight~preprint-surveillance-finder |
| deep-research-notebooklm | davila7 | 基于 NotebookLM MCP 的结构化深度研究流程，可作多源材料整理与引用回链的外壳 | https://github.com/davila7/claude-code-templates#cli-tool~components~skills~ai-research~deep-research-notebooklm |

## 4. 推荐起点与复用方案

- **首选**：`MaartenGr/BERTopic` —— 它同时解决本题最硬的两块计算：主题归属（c-TF-IDF 让主题词可解释）与主题随时间的演化（`topics_over_time`），且 MIT 许可、维护活跃（pushed_at 2026-11-14）。复用动作：固定 `random_state` 与 `min_topic_size`，用它的 topics_over_time 输出直接生成「时间 × 主题」长表；把主题标签与人工归并规则写成独立词表文件，保证同一输入复跑得到同一主题集合。
- **次选**：`J535D165/pyalex` —— 补数据层的年份归一与口径声明（`publication_year` vs 见刊年、预印本类型），这是本题「年份口径」失分点的解药；同时提供引用计数用于代表作的次级排序。
- **组合建议**：
  - 内核（跨题可复用）：`skill/kernel/ingest.py`（多源题录归一、去重）、`skill/kernel/timeslice.py`（时间窗切分与增长率/拐点判定）、`skill/kernel/topic_evo.py`（主题出现-合并-分裂-衰退追踪）、`skill/kernel/representative.py`（代表作多口径遴选：首创/高被引/各阶段代表）、`skill/kernel/gap.py`（空白判据 + 排除证据）；
  - 适配层（本题专用）：`skill/adapter/parse_case.py`（读 case.json/task.md，映射主题与时间范围）、`skill/adapter/normalize_year.py`（口径标注与缺失计数）、`skill/adapter/report.py`（三段式 result.json 组装）；
  - 资源：`skill/templates/` 放主题归并词表、时间窗约定、代表作口径说明、空白判据 checklist；`skill/SKILL.md` 写触发条件与不适用边界（不覆盖全领域、不宣称已完成实验）。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 四类产物齐全且互相印证：年度分布表 + 拐点判定依据、阶段划分与演化事件序列（何时/因何变化）、各阶段代表作与入选口径、空白清单附"已排除"证据；每条主张带 `record_id + span` |
| 使用增益 25 | 同案例、同库、同预算对照白板；量化指标：趋势结论与金标准年度分布的一致率、代表作覆盖率（含长尾首创工作）、空白条目中"有排除证据"的比例、重复运行的人工返工条数 |
| 创新增益 15 | 时间窗敏感度分析（换窗宽看结论是否翻转）、术语漂移检测（同义/同名异义登记表）、代表作多口径并置、空白判据与检索遗漏的显式区分，四者都不在常规工具默认能力内 |
| 可靠体验 10 | 年份缺失 → 单列 `year_missing` 并给缺失率，不外推；文献量爆炸 → 分层抽样 + 抽样误差说明；主题严重歧义 → 输出候选归并方案并挂 `needs_review`；检索为空 → 零命中报告 + 词表放宽建议 |
| 生态贡献 10 | 时间切片统计脚本、主题词表、代表作口径表、空白判据 checklist、题集与评分表全部独立成文件可复用；文档写清适用边界与维护方式 |
| GitHub +5 | `skill/ kernel/ adapter/ templates/ scripts/ docs/` 分目录；根 README 带最小调用示例（标注示例）；提交目录根放 `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `case.json` 与 `task.md`，确定主题、时间范围与数据口径，写进 `search_log.json`
- [ ] 多源题录抓取与归一（OpenAlex/Scopus/PubMed 任一可得源），输出去重后题录表与去重计数
- [ ] 做年份口径标注与缺失统计，产出 `year_audit.csv`（在线发表/见刊/预印本分列）
- [ ] 跑 BERTopic + LDA 双模型，产出「时间 × 主题」长表与主题词表，人工归并规则落文件
- [ ] 实现阶段划分与演化事件序列（出现/合并/分裂/衰退 + 依据文献）
- [ ] 实现代表作多口径遴选，产出 `representative.csv`（含入选理由与覆盖阶段）
- [ ] 实现空白判据与排除证据，产出 `gaps.csv`，逐条标检索范围与排除条件
- [ ] 写 `SKILL.md` + 最小调用示例，跑 3 次重复验证主题集合与统计表可复算
- [ ] 建 GitHub 仓库并放 `GITHUB_URL.txt`，未登录窗口自检
