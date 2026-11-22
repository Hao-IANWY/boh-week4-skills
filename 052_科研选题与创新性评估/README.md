# 052｜科研选题与创新性评估

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 052`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：深度研究与文献综述 ｜ 赛题：challenge-041f89c5 ｜ ARMI:2611.000094v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-041f89c5)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：交付一个可被官方 DeepSeek Harness 加载的 Skill，把「模糊的研究设想」推进成可决策的选题评估：研究问题定义、已有工作比较、创新点识别、可行性判断四件事同时交付且互相一致；3 个固定案例，不加载/加载各跑 3 次对照。
- **输入**：`/input/case.json`（case_id、研究设想、领域、可用条件约束、证据范围）+ `task.md` + 案例材料（题录导出、全文 PDF、可能的预印本；PDF 走 pdf-text/pdf-render/ocr）。scientific-python 用于比较矩阵统计，browser-automation 用于查最近工作。
- **输出契约**：`/app/output/result.json` 按题面三段回填，带 `case_id` 与全部 `output_contract` 字段——
  (1) **研究产物**：可检验问题陈述（研究对象、关键变量、比较基线、预期结论、可证伪条件）、比较矩阵（任务/方法/数据/评价方式/结论）、创新点清单及类型（方法/数据/场景迁移/结论修正）与被覆盖判定、带条件的可行性结论与失败风险；
  (2) **证据矩阵**：每条判断 → 文献 `record_id` + 原文定位、比较维度、是否可直接比较及不可比原因、作者主张与引用他人观点的区分；
  (3) **不确定性**：冲突证据（含结论相反的文献与取舍理由）、无法判定的创新诉求、把"检索不到"与"确实无人研究"的区分、可行性前提中标为假设的条目。
- **评分重心**：40 分的「结果正确性」集中在三点——文献真实且比较对象选对、创新点是否已被最近工作覆盖、可行性每个前提有来源或被明确标为假设；「结果可核验性」要求检索式、筛选标准、证据表与判断理由可独立复核。最常见丢分：把应用场景迁移说成理论创新、把旧方法当新方法、可行性只写「技术路线可行」。
- **裸 Agent 典型失败**：
  1. 输出四平八稳的综述式答复，没有可检验的问题陈述，也没有比较矩阵；
  2. 把「检索不到」直接写成「尚无人研究」，创新性判定无对照文献；
  3. 可行性结论无前提清单，不标假设、不给失败风险，无法据此决策。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| KalarisLabs/research-agent-skills | 157 | Python | NOASSERTION | SKILL 骨架 | 280+ 研究型 skill 的目录组织与 SKILL.md 写法（含文献综述/空白识别），可直接对齐官方加载格式 |
| LengFeng00/research-gap-finder | 23 | — | MIT | SKILL 骨架 | 专做「系统性文献分析 → 研究空白识别 → 假设生成」的 Agent skill，是本题前三步的现成骨架 |
| cxcscmu/IdeaTaste | 33 | Python | MIT | 评测数据 | 研究品味 benchmark：让 Agent 提 idea 再检验与真实录用工作的匹配度，可作创新性判定的金标准与评分锚点 |
| x66ccff/liveideabench | 40 | Jupyter Notebook | MIT | 评测数据 | LLM 科学创造力/idea 生成的评测集与打分脚本，用来对照"创新点是否只是复述已有工作" |
| ulab-uiuc/GraphEval | 19 | Python | MIT | 工具库 | 基于图的 idea 评估框架（ICLR 2025），提供可解释的多维评估结构与分数聚合方式 |
| artificial-scientist-lab/SciMuse | 43 | Python | MIT | 参考实现 | 知识图谱 + LLM 的 idea 生成，附 100 位研究者的评估设计，可借它的评估问卷与维度 |
| SakanaAI/AI-Scientist | 15014 | Jupyter Notebook | NOASSERTION | 参考实现 | idea 生成 → 实验 → 自动评审的完整流水线，借其"新颖性检索 + 评审打分"的提示结构与失败案例 |
| SamuelSchmidgall/AgentLaboratory | 5932 | Python | MIT | 参考实现 | 端到端自主研究工作流（含文献综述与实验计划阶段），可看它如何把设想拆成可执行子任务 |

（池内另有 `HKUDS/AI-Researcher`（★5895，许可未声明）、`imlrz/DeepResearch-Bench-II`（★99，Apache-2.0，偏报告质量评测）、`deer-flow/llm-space`（★2139）可作对照；`NoorBayan/Awzan`、`Aryia-Behroziuan/References`、`mangools/seo-skills` 与本题语义无关。）

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| novelty-assessment | lingzhi227 | 通过系统性文献检索评估研究设想的新颖性，是本题创新点识别的直接起点 | https://github.com/lingzhi227/agent-research-skills/tree/main/skills/novelty-assessment |
| novelty-check | wanshuiyin | 对照最近文献验证 idea 是否新颖，输出与近期工作的重合点 | https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep#skills~skills-codex-gemini-review~novelty-check |
| novelty-vs-feasibility-assessor | aipoch | 判断医学选题是否值得启动，同时给新颖性与可行性两维结论，正好对应题面「可行性判断」 | https://github.com/aipoch/medical-research-skills#awesome-med-research-skills~Evidence Insight~novelty-vs-feasibility-assessor |
| idea-evaluator | poco-ai | 用五维标准评估初步研究设想，可作评估量表与分档锚点 | https://github.com/poco-ai/Agentero#templates~vault~.agents~skills~idea-evaluator |
| idea-generation | lingzhi227 | 带迭代精炼的 idea 生成，用于「提出的问题是否只是已有工作的平移」的反向检验 | https://github.com/lingzhi227/agent-research-skills/tree/main/skills/idea-generation |
| light-idea-generation | Light0305 | 中文科研主线里的 idea 步骤：从模糊方向/数据/文献结构化发散，附中文话术与字段 | https://github.com/Light0305/Light-skills#skills~light-idea-generation |
| patent-novelty-check | wanshuiyin | 对照现有技术评估专利新颖性与非显而易见性，补充「技术类选题」的对照口径 | https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep#skills~skills-codex~patent-novelty-check |

## 4. 推荐起点与复用方案

- **首选**：`LengFeng00/research-gap-finder` —— 它是池内唯一把「文献分析 → 空白识别 → 假设生成」三件事封成一个 Agent skill 且 MIT 干净的仓库，与本题前三个评分环节一一对应，改动量最小。复用动作：拷它的 SKILL.md 骨架与步骤划分，把输出从"空白描述"改成题面要求的四条硬字段（问题陈述/比较矩阵/创新点判定/带条件可行性），并把每步强制挂 `record_id + span`。
- **次选**：`cxcscmu/IdeaTaste` —— 补「创新性怎么算成立」的金标准。它的设计是让 Agent 提 idea 再检验与真实录用工作的匹配度，可作为创新点覆盖判定的评分锚点与误报对照集，直接服务「增益可验证性」。
- **组合建议**：
  - 内核（跨题复用）：`skill/kernel/prior_art.py`（检索式 + 最近工作收集）、`skill/kernel/compare_matrix.py`（任务/方法/数据/评价/结论五维矩阵）、`skill/kernel/novelty.py`（创新类型判定 + 覆盖检验）、`skill/kernel/feasibility.py`（前提清单 → 条件式结论 + 失败风险）、`skill/kernel/evidence.py`（回链与冲突证据登记）；
  - 适配层（本题专用）：`skill/adapter/parse_case.py`（设想 → 字段化）、`skill/adapter/constraints.py`（把案例给的资源/时间/数据约束映射为可行性前提）、`skill/adapter/report.py`（三段式 result.json）；
  - 资源：`skill/templates/` 放问题陈述模板（可证伪条件必填）、比较矩阵表头、创新类型判定表、可行性前提 checklist；`skill/SKILL.md` 声明不适用于"宣称已完成实验"或"替代作者做研究结论"。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 四项交付齐全且互相一致：可证伪的问题陈述、五维比较矩阵（含"不可直接比较"原因）、创新点类型与覆盖判定、逐前提带来源或标为假设的可行性结论；每条判断可回到 `record_id + span` |
| 使用增益 25 | 同案例同预算对照白板；量化指标：创新点被判"已被覆盖"的正确条数、比较矩阵中真正相近工作的召回、可行性前提有来源的比例、人工返工改写条数 |
| 创新增益 15 | 创新类型四分类（方法/数据/场景迁移/结论修正）判定表、"检索不到 ≠ 无人研究"的显式判据、冲突证据强制登记、可行性前提→失败风险的映射，均超出裸 Agent 稳定能力 |
| 可靠体验 10 | 文献过少 → 输出"证据不足，无法判定创新性"并给最小补充检索建议；证据冲突 → 双列并置 + 取舍理由；接口异常 → 缓存降级并标 partial；权限不足 → 明确列出未覆盖库 |
| 生态贡献 10 | 问题陈述模板、比较矩阵模板、创新判定表、可行性 checklist、题集与评分锚点全部独立可复用；文档给输入契约与评估口径 |
| GitHub +5 | `skill/ kernel/ adapter/ templates/ cases/ docs/`；根 README 带示例（标注示例非正式结果）；根放 `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `case.json`/`task.md`，把研究设想字段化为对象/变量/基线/预期结论/可证伪条件
- [ ] 写检索式与最近工作收集，落 `prior_art.json`（含检索日期与库）
- [ ] 建五维比较矩阵，标出"不可直接比较"的原因，产出 `compare_matrix.csv`
- [ ] 实现创新点四分类判定与覆盖检验，产出 `novelty_claims.csv`（每条带对照文献与判定依据）
- [ ] 建可行性前提清单，逐条给来源或标 `assumption`，产出条件式结论与失败风险
- [ ] 登记冲突证据与无法判定项，落 `uncertainty.json`
- [ ] 写 `SKILL.md` + 最小调用示例（标注示例），跑 3 次重复验证判定稳定
- [ ] 用 IdeaTaste 类评测集做一次盲对照，记录创新判定的召回与误报
- [ ] 建 GitHub 仓库并放 `GITHUB_URL.txt`，未登录窗口自检
