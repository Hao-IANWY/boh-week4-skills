# 049｜文献综述生成

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 049`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：深度研究与文献综述 ｜ 赛题：challenge-e97137c9 ｜ ARMI：ARMI:2611.000091v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-e97137c9)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：在一批真实文献上依次完成"按标准筛选 → 归纳成有依据的主题 → 组织可逐条回链的证据 → 写出每句都能指回具体文献的草稿"，并把冲突证据与研究空白落到具体缺口上。题面明确不负责单篇精读、翻译、润色、选刊与事实层面的学术审查。
- **输入**：`/input/case.json` + `task.md` + 平台固定的 3 个案例（给定一批文献/题录，材料范围以实际提供文件与 `environment/resources.yaml` 为准）；可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`office-renderer`、`scientific-python`、`browser-automation`、`latex-compiler`、`font-discovery`；工作区 `/app/work`，交付 `/app/output`。无全文/无元数据就如实标注，不得凭标题补造数据。
- **输出契约**：`/app/output/result.json` 三段——① 主结果（纳排标准与筛选记录：逐文献纳入/排除+理由；文献清单与元数据（DOI/年份/类型/是否仅有摘要）；主题树（主题/子主题/支撑文献/边界）；证据表（`evidence_id`/文献标识/原文摘录/页码或段落/支撑论断/证据强度）；综述草稿（分节，逐句挂 `evidence_id`，表述强度分级：已证明/有提示性证据/仍有争议）；冲突证据表；研究空白（对应具体证据缺口））；② 证据记录（题录核验、页码定位、筛选日志、去重记录、草稿↔证据映射表、`case_id`、脚本版本）；③ 状态说明（完成/部分完成、缺全文条目、元数据不全、文献重复、结论冲突、超出上下文或预算、不确定性）。
- **提交形态**：只交 `SKILL.md` 与必要文本资源（≤50 文件、单文件 ≤500KB、合计 ≤2MB、无符号链接）；官方 Harness 3 案例 × 3 重复，预算 64 请求 / 900 秒 / 16384 tokens，禁止自造分数。
- **评分重心**：40 分主项是"文献真实存在、元数据准确、每条实质论断被所引文献真正支持、引述强度与原文一致"（15）与"覆盖输入文献集的主要主题、方法路线、正反证据与研究空白有据"（15）。最容易丢分：通用框架硬套（背景/方法/挑战/展望）、引用与论断脱节、反面证据被抹平、筛选标准写完再倒推。
- **裸 Agent 典型失败**：
  1. 直接套"背景—方法—挑战—展望"四段式，主题分布与这批文献的实际构成无关；段落里的引用是按标题相似度贴上去的。
  2. 把"某研究报告了 X"写成"X 已被证明"，引述强度超过原文；相反结论被合并成一句"仍有争议"。
  3. 筛选标准在写作之后才出现，排除项无记录；研究空白写成"未来有待深入"的套话。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| stanford-oval/storm | 31907 | Python | MIT | SKILL 骨架 | "多视角提问→基于材料的主题→大纲→分节写作"流水线，把"主题从文献里长出来"与"引用只来自检索材料"固定成流程 |
| Future-House/paper-qa | 9620 | Python | Apache-2.0 | 工具库 | 高精度带引用问答：可对草稿每句做"原文是否支持"的反查，也可处理仅有摘要的降级情形 |
| WenyuChiou/ai-research-skills | 676 | Python | MIT | SKILL 骨架 | 面向研究流程的 SKILL.md 目录（文献综述/研究设计/项目记忆/写作），可直接拿综述部分的骨架 |
| lingzhi227/agent-research-skills | 470 | Python | — | SKILL 骨架 | 系统性综述技能集，含检索→筛选→写作的阶段交接物约定 |
| tririver/arc | 117 | Python | MIT | 参考实现 | Agent Research Copilot：技能 + CLI 的形态，参考其"检索/归纳/计算"分工与可复跑命令 |
| littlelelephant/literature-review-agent | 120 | Python | AGPL-3.0 | 参考实现 | LangGraph 实现：检索→证据筛选→大纲→证据落地写作，四阶段与本题四步一一对应 |
| JinchengGao-Infty/FWMA | 58 | Python | Apache-2.0 | 参考实现 | 端到端自动化：抓取→筛选→下载→多智能体评审→报告，参考其筛选记录字段 |
| Zsun79/LitReviewSkill | 30 | Python | — | SKILL 骨架 | 可安装的综述生成 skill，强调"检索过程广而迭代、少漏文献"的框架选择 |
| lira-workflow/auto-review-writing | 19 | Python | MIT | 参考实现 | LiRA 多智能体框架，目标"可靠且可读"的综述，参考其段落-证据对齐机制 |

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| literature-review | K-Dense-AI | 跨多个学术库的系统综述流程：检索策略、引用核验、成文与图表，含质量控制清单 | https://github.com/K-Dense-AI/scientific-agent-skills#skills~literature-review |
| dedupe-rank | WILLOSCAR | 对原始文献集去重与排序，产出 `papers_dedup.jsonl` 与排序结果——正好是本题第一步 | https://github.com/WILLOSCAR/research-units-pipeline-skills#.codex~skills~dedupe-rank |
| section-mapper | WILLOSCAR | 把核心文献映射到大纲每个子节并写 `outline/mapping.tsv`，带覆盖度追踪，防主题漏项 | https://github.com/WILLOSCAR/research-units-pipeline-skills#.codex~skills~section-mapper |
| prose-writer | WILLOSCAR | 依据已批准大纲与证据包写草稿，只用已核实证据，正好约束"每句可回链" | https://github.com/WILLOSCAR/research-units-pipeline-skills#.codex~skills~prose-writer |
| research-survey | CamusGIT | 多阶段流水线生成结构化综述报告（大纲→分节→整合），参考其阶段产物格式 | https://github.com/CamusGIT/EvoQuant#EvoQuant~skills~research-survey |
| paper-audit | bahayonghang | 审稿式审计与投稿闸门，用来对生成的综述做引用/论断一致性的自检 | https://github.com/bahayonghang/academic-writing-skills/tree/main/academic-writing-skills/paper-audit |
| literature-review | Microck | 结构化综述工作流（检索策略/引用验证/成文），适合作为流程对照 | https://github.com/Microck/ordinary-claude-skills#skills_all~claude-scientific-skills~scientific-skills~literature-review |

## 4. 推荐起点与复用方案

- **首选**：`stanford-oval/storm` —— 它是唯一把"主题从这批文献里归纳出来"和"引用只能来自已检索材料"同时写进流程的开源实现（MIT），正对本题两个 15 分主项；且它的大纲是中间产物，天然可审。复用动作：① 用它的 perspective-guided question 生成替代"背景/方法/挑战/展望"套框架，主题树直接落 `topics.json`；② 把 retriever 接口换成题面给定的文献集与全文解析，禁止外网补材料；③ 借它的分节写作 + 引用绑定，强制"无 `evidence_id` 的实质句不得入稿"。
- **次选**：`Future-House/paper-qa` —— 补"逐句核验"：草稿写完把每个论断回查原文，返回支持/不支持/仅摘要支撑三态，正好支撑"引述强度"与"冲突证据"两项；同时能处理仅有摘要的条目，避免因缺全文而漏筛。
- **组合建议**：内核与适配层分开：

```
SKILL.md                    # 触发条件、四步顺序（筛选→归纳→证据→草稿）、失败分支
core/screen.py              # 纳排标准执行 + 逐文献理由 + 去重（DOI/标题-作者-年份/哈希）
core/topics.py              # 主题归纳：从方法/对象/结论分歧聚类，产出主题树 + 边界
core/evidence.py            # 证据表：evidence_id ↔ 文献段落 ↔ 支撑论断 ↔ 强度
core/draft.py               # 草稿生成：段落↔证据映射，表述强度分级
core/audit.py               # 自检：无证据句、强度越界、冲突未并列、主题漏覆盖
adapters/case_input.py      # case.json / task.md → 文献集、研究问题、纳排约束
adapters/metadata.py        # 题录核验与元数据补全（只补可核验字段，缺失即标注）
adapters/conflict.py        # 冲突识别规则（结论相反/方法互斥/样本不同）
adapters/report.py          # result.json 三段拼装
```

- **分工理由**：`core/evidence.py` + `core/audit.py` 是"可回链"的实现核心，与学科无关，049 与 048 共用；`adapters/metadata.py` 与 `conflict.py` 承载本题的领域判断与字段口径，评审改标准只动这里。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 先落 `criteria.md`（纳排标准）与 `screening.tsv`（逐文献决定+理由）再写草稿；主题树带支撑文献与边界；草稿逐句挂 `evidence_id`，无证据句被 `audit.py` 拦下；冲突证据单独成表；研究空白写成"缺哪类研究/人群/时长/对照" |
| 使用增益 25 | 固定对照：纳入/排除正确率、主题覆盖度（对金标准主题集）、论断-引用对齐率、强度越界次数（应 0）、重排与改写工作量；同文献集跑 Skill/裸 Agent 各 3 次，同一金标准比较 |
| 创新增益 15 | ① 主题归纳由"文献方法-对象-结论分歧"驱动而非通用框架，产出可核验的聚类依据；② 表述强度分级器（已证明/提示性/争议）由原文措辞与证据类型共同决定；③ 覆盖度检查——把 `outline/mapping` 反过来验证每篇纳入文献至少落在一个子节，防漏项 |
| 可靠体验 10 | 失败分级 `no_fulltext`（仅摘要参与，标注强度上限）/`metadata_missing`/`duplicate`/`conflict`/`over_budget`（截断到已有证据的分节草稿 + 未覆盖清单）；每次运行落 `run_manifest.json`，重跑可复算 |
| 生态贡献 10 | 交付：纳排标准模板、主题树与证据表 schema、强度分级定义、引用格式、最小示例（标明示例）、不适用边界（单篇精读/翻译/选刊不在范围）；core 脚本可跨题复用 |
| GitHub +5 | 仓库结构：`SKILL.md` / `core/` / `adapters/` / `templates/`（criteria、screening、evidence、outline）/ `docs/` / `examples/` / `tests/`（audit 回归）/ `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `detail.md` 与 `/input/case.json`，写出纳排标准（含边界情形）与文献字段要求，落 `templates/criteria.md`。
- [ ] 写 `core/screen.py`：去重（DOI/标题-作者-年份/哈希）+ 逐文献纳排决定与理由，输出 `screening.tsv`。
- [ ] 写 `adapters/metadata.py`：题录核验，只补可核验字段；缺全文/缺元数据如实标注并影响强度上限。
- [ ] 写 `core/topics.py`：从方法/对象/结论分歧归纳主题树，附支撑文献与边界，禁止套通用框架。
- [ ] 写 `core/evidence.py`：证据表落盘（文献→段落→论断→强度），支持草稿反向查询。
- [ ] 写 `core/draft.py`：按主题树分节写作，段落绑定 `evidence_id`，表述强度分级。
- [ ] 写 `core/audit.py` + `adapters/conflict.py`：无证据句、强度越界、冲突未并列、主题覆盖漏项四类检查。
- [ ] 写 `SKILL.md` 与 `examples/`，用最小文献集跑通四步并核对 result.json 三段与 screening/topics/evidence 三份中间产物一致。