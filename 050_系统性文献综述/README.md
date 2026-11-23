# 050｜系统性文献综述

## 0. 快速开始（Harbor 正式运行）

```bash
python3 -B /skills/weekly-skill/scripts/pipeline.py \
  --case /input/case.json --work /app/work --out /app/output
python3 -B /skills/weekly-skill/scripts/validate.py \
  --case /input/case.json --result /app/output/result.json
```

产物：`/app/output/result.json`（契约与证据台账）、`/app/output/report.md`（人类可读研究产物）、`/app/work/plan.json`（前瞻计划与协议）。本地最小示例见 `examples/min_example/`；示例为合成材料，不代表正式案例结论。

正式运行分四步：确定性管线 → 模型读 `work/synthesis_input.json` 写 `work/synthesis.json` → 回跑管线校验引用/数字并生成 `review.md` → `validate.py`。详见 `SKILL.md` 的“执行”节；无模型或校验失败时，`report.md` 是可交付的确定性回退。

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 050`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：深度研究与文献综述 ｜ 赛题：challenge-51977f9b ｜ ARMI:2611.000095v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-51977f9b)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 0.1 证据约束综合层（本轮新增）

确定性管线负责“可复核”，模型负责“会综合”，两者用同一张证据表衔接：

1. 管线写出 `result.json` 后，自动把证据压缩成 `/app/work/synthesis_input.json`（记录、逐字引文、冲突、空白、筛选计数与本题统计）。
2. Harness 模型按 `rules/synthesis_prompt.md` 写 `/app/work/synthesis.json`：每个实质句以 `[E###]`/`[M###]` 回链，数字必须能在所引引文回查，冲突并列，薄弱主题标注。
3. 回跑管线，`merge_synthesis.py` 校验引用 ID、段落引用覆盖、带单位/小数数字的回查与冲突留痕；通过才写 `review.md` 与 `synthesis_audit.json`，不通过保留 `report.md` 并打印违规。
4. 无模型或综合稿未通过时，`report.md` 是确定性回退；两条产物同源，不会出现“正文引文与证据表对不上”。

这一层的作用是把“摘要拼接”换成“有证据约束的综合”：模型可以组织主题、比较口径、写边界，但不能引入证据表外的文献、数字或结论。

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：交付一个能被官方 DeepSeek Harness 加载的 Skill（`/app/submission/SKILL.md` + 实际调用的脚本/模板/量表），把「研究问题 → 可执行检索式 → 题录级与全文级两轮纳排 → 质量评价 → 带证据回链的综述结论」跑成一条第三方可复跑的流程；组织方用 3 个固定案例分别在不加载/加载 Skill 两种条件下各跑 3 次做对照。
- **输入**：`/input/case.json`（case_id、研究问题、领域约束、证据范围）+ `task.md` + 题面按案例给的实际材料（题录导出、全文 PDF；PDF 由 pdf-text-tool / pdf-render-tool / ocr-tool 真解析，无预抽取答案层）。可用工具含 scientific-python、browser-automation、latex-compiler，需按 `environment/resources.yaml` 实际存在的能力编排。
- **输出契约**：`/app/output/result.json` 必须落在题面三段上，并回填 `case_id` 与 `output_contract` 全部字段——
  (1) **研究产物**：研究问题拆解（对象/人群/暴露/结局/比较）、检索式原文（库、字段、布尔、同义词与受控词、时间与语言限制）、年度纳入计数、综述结论与冲突证据、研究空白；
  (2) **证据矩阵**：每条文献的 `record_id / 来源库 / 检索式编号 / 两轮筛选结论 / 排除理由码 / 比较字段 / 全文定位（页-段-句）`，以及每条综述论断到 `record_id + span` 的回链；
  (3) **不确定性**：排除理由冲突、质量等级偏低、全文不可得、术语歧义、纳入研究结论互相矛盾、无法判定项。
- **评分重心**：40 分实际效果里「结果正确性」（检索式真可执行并命中领域核心文献、纳入集真实存在、论断不越证据）与「结果可核验性」（检索日期/数据库/筛选记录能否被他人复跑）最容易丢分；失分高发点是排除理由缺失或前后矛盾、质量评价退化成一句「质量较高」、综述论断没有 `span` 级回链。
- **裸 Agent 典型失败**：
  1. 检索式退化为一个关键词串，不写库/字段/时间限制，事后无法重放；
  2. 纳排标准边筛边放宽，只给通过项不给排除理由，两次运行纳入集不一致；
  3. 综述段落写作流畅但论断无法回链到具体文献段落，冲突证据与研究空白整段缺失。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| ChaokunHong/MetaScreener | 1340 | Python | Apache-2.0 | 参考实现 | 摘要级与 PDF 级两级 LLM 筛选的判定口径与 prompt 结构，可直接改造成「筛选结论 + 理由码」记录器 |
| asreview/asreview | 1077 | Python | Apache-2.0 | 工具库 | 主动学习排序减少人工题录筛选；导出带标签的题录，作为筛选记录与"重算纳入集"的基线 |
| vitorfs/parsifal | 531 | Python | MIT | 参考实现 | 成熟的 SLR 阶段模型（protocol → screening → quality assessment），借它的阶段划分与字段命名做输出 schema |
| nealhaddaway/citationchaser | 164 | R | —（未声明） | 工具库 | 前向/后向引文追踪补漏，补齐数据库检索覆盖不到的纳入文献，并留下"从哪篇追到哪篇"的链路 |
| elizagrames/litsearchr | 128 | R | —（未声明） | 工具库 | 关键词共现 → 候选检索词 → 布尔检索式生成，正好对应题面的「检索式构造」环节 |
| mjwestgate/revtools | 61 | R | —（未声明） | 工具库 | 题录去重、标题/摘要文本聚类与半自动筛选，稳定题录级筛选的重复率 |
| htlin222/prisma-automation | 10 | Python | MIT | SKILL 骨架 | 已按 PRISMA 合规组织「多库检索 → 去重 → 筛选 → 流程图」的流程骨架，可直接改写 SKILL.md 主流程 |
| sanatladkat/auto-slr-screener | 5 | Python | —（未声明） | 参考实现 | 容错流水线设计（单条记录失败不中断整批），是「异常处理」3 分的现成参考 |

（本题池共 11 条，另有 `CarinaSchoppe/PRISMA-Literature-Review-Pipeline-Automation-Tool`、`SisonkeBiotik-Africa/Systematic-Review-Automation`（GPL-2.0）、`htlin222/llm-in-med` 可读流程但许可受限或体量过小，只作阅读参考，不拷贝代码。）

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| systematic-literature-review | zaoshangduziteng | 多篇论文的综述/综合/注释书目，检索 arXiv 并输出 APA/IEEE/BibTeX，明确区分「单篇评审」用别的 skill | https://github.com/zaoshangduziteng/deer-flow#skills~public~systematic-literature-review |
| literature-review | K-Dense-AI | 多学术库检索 + 引用验证 + 格式化文档生成的完整 SLR 工作流，含数据库专属检索策略 | https://github.com/K-Dense-AI/scientific-agent-skills#skills~literature-review |
| literature-review | Microck | 结构化 SLR 流程 + 详细检索策略 + 引用验证，面向生物医学与通用科学场景 | https://github.com/Microck/ordinary-claude-skills#skills_all~claude-scientific-skills~scientific-skills~literature-review |
| literature-review | fabioc-aloha | 跨库（multidb）变体，把「多库联合检索」拆成独立步骤，适合做检索式并集与去重后的计数口径 | https://github.com/fabioc-aloha/Alex_Skill_Mall#plugins~academic-research~literature-review-multidb~skills~literature-review-multidb |
| literature-review-tools | brycewang-stanford | 推荐并实际运行开源 SLR 工具/Agent，可作为工具选型与调用层 | https://github.com/brycewang-stanford/lit-review-agent-tools |
| peer-review | K-Dense-AI | 方法论评估 + 统计有效性 + CONSORT/STROBE/PRISMA 报告标准清单，可直接充当「质量评价量表」的条目来源 | https://github.com/K-Dense-AI/scientific-agent-skills#skills~peer-review |
| peer-review | BbgnsurfTech | 同一量表族的中文说明版，便于写中文文档与量表定义 | https://github.com/BbgnsurfTech/claude-skills-collection#community~individual-skills~claude-scientific-skills~scientific-skills~peer-review |

## 4. 推荐起点与复用方案

- **首选**：`ChaokunHong/MetaScreener` —— 它是池内唯一把「题录级筛选」和「PDF 级筛选」都做成可运行组件且许可干净（Apache-2.0）的项目，与本题 40 分里最重的纳排一致性、排除理由可追溯直接对齐。复用动作：借它的两级筛选判定口径改写成 `screening.py` 的两轮函数，输出强制带 `reason_code`（数据源不足/对象不符/时间越界/全文不可得/语言不符），并把 prompt 里"给出理由"改成"给出理由码 + 原文依据 span"。
- **次选**：`asreview/asreview` —— 补「主动学习优先排序 + 可重算纳入集」的能力。动作：用它的排序结果做筛选顺序的确定性种子（固定 random_state），使三次重复运行得到同一纳入集，直接服务「增益可验证性」与「运行稳定性」。
- **组合建议**：内核与适配层分开落地——
  - `skill/kernel/search_protocol.py`：研究问题 → 检索要素表 → 布尔检索式（可跨题复用于 051/052 的检索环节）；
  - `skill/kernel/screening.py`：两轮筛选 + 理由码枚举 + 版本号与人工修改记录（跨题复用）；
  - `skill/kernel/quality_scale.py`：评价量表条目 + 等级 → 对结论影响的映射表；
  - `skill/kernel/evidence_matrix.py`：`record_id + span` 回链与冲突证据矩阵；
  - `skill/adapter/parse_case.py`：读 `/input/case.json` 与 `task.md`，映射到内核字段（本题专用，题面换版本只改这里）；
  - `skill/adapter/guards.py`：检索为空、库超时、全文不可得、术语歧义、条款冲突的降级与状态标记；
  - `skill/SKILL.md` + `skill/templates/`（检索式模板、纳排条款表、筛选记录表、质量量表、证据矩阵 CSV 头）。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 五个环节全部产出可检查文件：检索式原文含库/字段/日期、两轮筛选逐条结论与理由码、量表逐维判断与依据位置、综述每条论断带 `record_id+span`、冲突证据矩阵与研究空白（空白必须附"已排除"证据，不是"没检索到"） |
| 使用增益 25 | 与白板（不加载 Skill 的裸 Agent）同案例同库同预算对照；三项可量化指标：纳入集与金标准的一致率、论断回链覆盖率、排除理由齐全率；效率侧记「重跑次数」和「人工修改条目数」 |
| 创新增益 15 | 纳排条款版本化 + 判定留痕（每次放宽都新增版本号）、双人筛选分歧标记、证据表自动回链、冲突证据矩阵四件套；用同一案例的两次独立运行证明纳入集可重算 |
| 可靠体验 10 | 检索为空 → 输出「零命中报告 + 放宽建议」而非空综述；库超时 → 切备用库并标记 partial；全文不可得 → 降级到题录级并标 `fulltext_missing`；条款冲突 → 挂起该条并进 `uncertainty` |
| 生态贡献 10 | 检索式模板、纳排条款表、筛选记录表、质量量表、证据矩阵表头全部独立成文件可复用；文档写清适用边界（不含单篇精读/翻译/写作润色）与失败处理 |
| GitHub +5 | 仓库按 `skill/ kernel/ adapter/ templates/ cases/ docs/` 分目录，根 README 给最小调用示例并标注"示例非正式案例结果"，提交目录根放 `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `case.json` 与 `task.md`，把研究问题拆成 PICO/检索要素表，落 `search_protocol.py`
- [ ] 写检索式生成与落盘：库、字段、布尔、同义词、时间/语言限制、检索日期，输出 `search_log.json`
- [ ] 实现题录级 + 全文级两轮筛选，强制理由码枚举与版本号，产出 `screening.csv`
- [ ] 落质量评价量表（条目 + 等级 + 依据 span + 对结论的影响），产出 `quality.csv`
- [ ] 实现证据矩阵与 `record_id+span` 回链，产出 `evidence_matrix.csv` 与冲突证据矩阵
- [ ] 写 `SKILL.md`：触发条件、输入输出、调用步骤、适用/不适用边界、异常处理与自查清单
- [ ] 补最小调用示例（标注为示例）与 3 次重复运行的对照记录，验证纳入集可重算
- [ ] 建 GitHub 仓库、按目录结构提交、根放 `GITHUB_URL.txt` 并用未登录窗口自检可访问
