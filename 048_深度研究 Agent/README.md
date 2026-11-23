# 048｜深度研究 Agent

## 0. 快速开始（Harbor 正式运行）

```bash
python3 -B /skills/weekly-skill/scripts/pipeline.py \
  --case /input/case.json --work /app/work --out /app/output
python3 -B /skills/weekly-skill/scripts/validate.py \
  --case /input/case.json --result /app/output/result.json
```

产物：`/app/output/result.json`（契约与证据台账）、`/app/output/report.md`（人类可读研究产物）、`/app/work/plan.json`（前瞻计划与协议）。本地最小示例见 `examples/min_example/`；示例为合成材料，不代表正式案例结论。

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 048`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：深度研究与文献综述 ｜ 赛题：agent-f8fb8bda ｜ ARMI：ARMI:2611.000090v1 ｜ [详情页](https://play.bohrium.com/challenge/agent-f8fb8bda)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：围绕一个不能一次答完的研究问题，走完"问题拆解 → 检索规划 → 多步工具调用 → 证据综合"：拆出可独立检索的子问题并标依赖、给检索式与停止条件、记录每步参数与返回、把材料整理成带定位的证据表并写有边界的结论。题面明确不负责单篇逐句精读、方法学审查、翻译与润色。
- **输入**：`/input/case.json` + `task.md` + 平台固定的 3 个研究问题案例（材料范围以 `environment/resources.yaml` 与实际提供文件为准）；可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`office-renderer`、`scientific-python`、`browser-automation`、`latex-compiler`、`font-discovery`；工作区 `/app/work`，交付 `/app/output`。环境未提供的能力（如外网检索）不得虚构调用结果，缺材料就写缺。
- **输出契约**：`/app/output/result.json` 三段——① 主结果（拆解树：子问题+依赖+证据要求；检索计划：顺序/并行、来源范围、停止条件；证据表：`evidence_id`/文献标识/原文摘录/页码或段落/支撑的子问题/证据强度；综合结论：逐句挂 `evidence_id`，冲突结论并列，研究空白落到具体证据缺口）；② 证据记录（每步工具调用的参数、返回摘要、重试与失败、耗时/预算消耗、检索式与命中数、文献真实性与元数据核验、`case_id`）；③ 状态说明（完成/部分完成、检索为空、工具报错或限流、PDF 解析失败、证据冲突、权限不足、无法确定的部分）。
- **提交形态**：只交 `SKILL.md` 与必要文本资源（≤50 文件、单文件 ≤500KB、合计 ≤2MB、无符号链接）；官方 Harness 3 案例 × 3 重复，预算 64 请求 / 900 秒 / 16384 tokens，禁止自造基准分与原生 trace。
- **评分重心**：40 分主项是"结论句与所引文献段落一一对应、文献真实存在、冲突结论如实呈现"（15）与"六类要点全覆盖：拆解、检索覆盖、证据回链、冲突证据、综合结论、研究空白"（15）。最容易丢分：拆解停在关键词改写、子问题重复查同一批文献、工具调用无记录、证据表与正文脱节、研究空白写成套话。
- **裸 Agent 典型失败**：
  1. 把原问题改写成几个关键词就开搜，没有依赖关系与停止条件，反复查同一批文献直到预算耗尽。
  2. 结论句写得很顺，但引用是"相关研究显示"，证据表与正文两套东西，谁也回不到具体段落。
  3. 遇到相反结论时取一方，或统一写成"尚有争议"；研究空白照抄模板句式，看不出是检索缺口还是凭空判断。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| stanford-oval/storm | 31907 | Python | MIT | 参考实现 | "多视角提问→检索→大纲→分节写作"的流水线，其视角生成可当子问题拆解的起点 |
| assafelovic/gpt-researcher | 30769 | Python | Apache-2.0 | 工具库 | 工程化模板：多源检索器抽象、抓取/摘要链路、token 与成本追踪，可直接当工具编排层 |
| Alibaba-NLP/DeepResearch | 20152 | Python | Apache-2.0 | 参考实现 | 通义系 agentic 检索范式（迭代式搜索+精读+反思），参考其"何时停止检索"的判据 |
| dzhng/deep-research | 19910 | TypeScript | MIT | 参考实现 | 极简可读的"迭代加深"实现（生成 query → 读 → 追问 → 再查），适合当最小骨架改题 |
| langchain-ai/open_deep_research | 12675 | Python | MIT | SKILL 骨架 | 把"澄清→研究计划→并行子研究员→反思→成稿"显式编成图，state 结构即拆解树+证据表的现成 schema |
| LearningCircuit/local-deep-research | 9601 | Python | MIT | 工具库 | 全本地（llama.cpp/Ollama）+ 多检索引擎（arXiv/PubMed/私有文档），适合无外网时的合规基线 |
| openJiuwen-ai/deepsearch | 130 | Python | Apache-2.0 | 参考实现 | 块级引用（chunk-level citation）+ 可追溯推理，正对"证据回链到块"这一评分点 |
| meituan-longcat/LongCat-DeepResearch | 31 | Python | MIT | 参考实现 | 证据落地型报告 harness：多智能体规划 + 分节独立研究 + 定向修订，参考其"按证据缺口做定向补检索" |
| brycewang-stanford/lit-review-agent-tools | 54 | Python | CC0-1.0 | 评测数据 | 70+ 文献综述类工具/技能的分类清单，用来快速选型与对照已有方案的边界 |

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| deep-research | Imbad0202 | 13 智能体通用研究流水线、7 种模式（全研究/快报/审稿/综述/事实核查），可裁成单模式最小实现 | https://github.com/Imbad0202/academic-research-skills#deep-research |
| deep-research | brycewang-stanford | 学术向深度研究团队：拆解、检索、写作分工明确，参考其角色划分与交接物 | https://github.com/brycewang-stanford/Auto-Empirical-Research-Skills#skills~24-Imbad0202-academic-research-skills~deep-research |
| comprehensive-research-agent | muratcankoylan | 强调多工具调用下的充分验证、错误恢复与推理透明，正对本题"失败可定位、过程可复核" | https://github.com/muratcankoylan/Agent-Skills-for-Context-Engineering#examples-interleaved-thinking-generated_skills-comprehensive-research-agent |
| deep-research-agent | shobcoder | 通用深度调研 agent：拆问题、找证据、出报告，结构简单易改 | https://github.com/shobcoder/shob/tree/main/skills/deep-research-agent |
| deep-research-pro | AlekseiUL | 多源检索 + 综合 + 带引用报告，参考其引用格式与来源分级 | https://github.com/AlekseiUL/sprut-agent-kit/tree/main/skills/deep-research-pro |
| web-search-exa | openclaw | 神经检索 + 内容抽取 + 深度研究接口；环境允许联网时作为检索工具层 | https://github.com/openclaw/skills#skills~theishangoswami~web-search-exa |
| jina | openclaw | URL→干净 Markdown 的读取与搜索接口，稳定化抓取结果，降低解析噪声 | https://github.com/openclaw/skills#skills~adhishthite~jina-ai |

## 4. 推荐起点与复用方案

- **首选**：`langchain-ai/open_deep_research` —— 它是这批里唯一把"澄清→研究计划→并行子研究员→反思→成稿"每一步的中间状态显式化并可落盘的实现（MIT），直接对上"拆解树/检索计划/工具调用记录/证据表能被独立复核"这四个评分锚点；单文件图结构也便于在 900 秒预算内裁剪成单案例可跑。复用动作：① 借其 state schema 定义 `subquestions`/`search_plan`/`tool_calls`/`evidence`/`sections` 五个落盘结构；② 拷它的 plan-then-execute 两段式，把"计划"先写进 `/app/work/plan.json` 再执行（可复核的关键）；③ 用它的反思节点改造成"按证据缺口定向补检索"，避免无边际加查。
- **次选**：`meituan-longcat/LongCat-DeepResearch` —— 补两点：分节独立研究（每节自己的证据与引用，防串用）与定向修订（按 reviewer 指出的缺口补证据而非重写全文）；它的"证据落地型报告"目标与本题"每条结论回链"完全同向。
- **组合建议**：内核与适配层分开：

```
SKILL.md                    # 触发条件、四阶段顺序、停止条件、失败分支
core/decompose.py           # 问题→子问题树（对象/机制/方法/时间/比较/证据要求）+ 依赖
core/plan.py                # 检索计划：顺序、并行度、来源、停止条件、预算分配
core/tools.py               # 工具注册与统一调用契约（参数/返回/重试/超时/限流）
core/trace.py               # 每步调用记录（入参、返回摘要、耗时、失败原因）
core/evidence.py            # 证据表：evidence_id ↔ 子问题 ↔ 原文段落
core/synth.py               # 综合：逐句挂证据、冲突并列、缺口→研究空白
adapters/case_input.py      # case.json / task.md → 研究问题与证据要求
adapters/sources.py         # 本题可用来源范围（本地库/API/无网降级）
adapters/budget.py          # 900s/64 请求预算切分与熔断
adapters/report.py          # result.json 三段拼装
```

- **分工理由**：`core/trace.py` 与 `core/evidence.py` 是"可复核"的全部来源，必须与具体来源渠道解耦；`adapters/sources.py` 与 `budget.py` 承载本题环境约束，换环境只改这两个文件。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 先写 `plan.json` 再执行（计划可审）；每个子问题记录检索式、命中数、纳入/排除理由；证据表与正文用 `evidence_id` 双向绑定，正文中无 `evidence_id` 的实质句一律删除；冲突结论并列成表；研究空白必须写成"缺哪类证据（人群/时长/方法/年份）" |
| 使用增益 25 | 固定对照：子问题可独立检索率、证据回链率（结论句挂 evidence 的比例）、无效检索次数、工具调用留痕率；同问题跑 Skill/裸 Agent 各 3 次，同一证据金标准比较，只报可复算差值 |
| 创新增益 15 | ① 依赖感知的检索计划（前置/并行分组 + 去重，避免重复查同一批）；② 缺口驱动补检索（只按未覆盖的证据类型追加，带收益预估与停止判据）；③ 引用真实性校验（文献标识/元数据交叉核对，查不到即标 `unverified` 并降级表述） |
| 可靠体验 10 | 失败分级 `empty_result`/`rate_limited`/`tool_error`/`pdf_parse_failed`/`conflicting_evidence`/`no_permission`；每级给重试策略或降级路径；预算将尽时先固化"已完成子问题 + 结论草稿 + 未覆盖清单"，不伪装完整 |
| 生态贡献 10 | 交付：拆解模板、检索计划模板、证据表模板（字段+强度分级）、工具调用记录格式、缺口判据、最小示例（标明示例）、不适用边界；core 可跨题（049 直接复用证据表） |
| GitHub +5 | 仓库结构：`SKILL.md` / `core/` / `adapters/` / `templates/`（拆解树、计划、证据表）/ `docs/` / `examples/` / `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `detail.md` 与 `/input/case.json`，确定研究问题、证据要求、可用来源与预算，写进 `adapters/sources.py` 与 `adapters/budget.py`。
- [ ] 写 `core/decompose.py`：输出子问题树（含对象/机制/方法/时间/比较/证据要求六要素）与依赖边。
- [ ] 写 `core/plan.py`：按依赖分组、定并行度与停止条件，产出可审的 `plan.json`。
- [ ] 写 `core/tools.py` + `core/trace.py`：统一调用契约与全量留痕（参数、返回摘要、重试、耗时）。
- [ ] 写 `core/evidence.py`：证据表落盘（`evidence_id`/来源/段落/强度/支撑子问题），供正文反向引用。
- [ ] 写 `core/synth.py`：逐句挂证据、冲突并列、缺口→研究空白，禁止无证据实质句。
- [ ] 加引用真实性校验：文献标识与元数据交叉核对，失败标 `unverified`。
- [ ] 写 `SKILL.md` 与 `examples/`，用最小问题跑通"拆解→计划→调用→证据→结论"并核对 result.json 三段与 plan 一致。
