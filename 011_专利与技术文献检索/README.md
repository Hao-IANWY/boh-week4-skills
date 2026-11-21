# 011｜专利与技术文献检索

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 011`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：文献检索 ｜ 赛题：challenge-ad3e9357 ｜ ARMI：ARMI:2611.000153v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-ad3e9357)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：交付一个可被 Agent 调用的 Skill，同时回答五件事——这件专利保护什么、与同族什么关系、现在是否有效、同技术主题还有谁在做、这些信息是否有权访问与展示；并逐字段标注来源库名、版本与查询日期。
- **输入**：`/input/case.json` + `task.md`；本题为有限的真实语料：6 条专利记录，含 3 份原始 USPTO 影印件、真实科学对比论文、号码型与主题型两类查询，以及可定位到来源的权利要求版本；**当前官方法律状态未知**，观察到的同族链接不构成穷尽专利族与等同权项范围，WO 合并件的 OCR 边界被保留。预算 64 次模型请求 / 900 秒 / 16384 tokens per response。
- **输出契约**：`/app/output/result.json` 三段——① 检索结果（候选专利/技术文献、号码规范化、元数据、来源定位）；② 证据记录（查询式、字段抽取与规范化依据、族归并依据、法律状态核验路径、权利要求版本定位）；③ 状态说明（法律状态未知、族不穷尽、OCR 不确定、无授权访问的库）。
- **评分重心**：40 分里最易丢分的是**字段口径**——公开号 vs 授权号、优先权日 vs 申请日、族成员 vs 不同专利；以及**法律状态**：题面已给定"当前官方状态未知"，任何"已授权有效"的断言都是硬伤，正确做法是写"未核验/需回官方登记核对"。
- **裸 Agent 典型失败**：
  1. 把技术主题关键词丢进通用搜索，从二手聚合页拼出"官方著录项"口吻的结论，公开号与授权号混用、把不同专利当成一族；
  2. 直接断言"该专利有效/已失效"，来源是几年前的转载页面，未标查询日期；
  3. 遇到需登录的商业库仍尝试抓取，或把 OCR 出来的残缺权项当作完整权项范围引用。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| google/patents-public-data | 694 | Jupyter Notebook | Apache-2.0 | 数据集 | 官方公开专利集的 BigQuery schema 与查询样例，可直接借其字段定义（公开号/申请号/优先权/CPC/族）统一口径 |
| Obviously-Not/patent-skills | 19 | Go | MIT | SKILL 骨架 | 面向专利与创新研究的开源 Agent Skill 集合，含技术特征发现与检索流程，是本题最贴形态的骨架 |
| dungnotnull/freedom-to-operate-analysis-agent-skill | 9 | Python | — | SKILL 骨架 | FTO 分析型 Skill：生产级 IP 合规 harness，示范"结论必须标授权来源与限定范围"的写法 |
| NathanMaine/memoriant-patent-skills | 3 | — | MIT | SKILL 骨架 | Claude 插件式专利技能（检索/撰写/审阅/分析，6 skills + 4 agents），可借其任务拆分与触发描述 |
| meshcode-ai/skills-research | 3 | — | NOASSERTION | 参考实现 | 8 个研究类 Skill 的深度研究与文献处理编排，可参考多步检索与证据留存的结构 |
| biopython/biopython | 5276 | Python | NOASSERTION | 工具库 | 生物序列类专利中 SEQ ID NO 的解析与校验（SeqIO），避免序列权项被当普通文本切碎 |

（星数与许可照抄 JSON，许可为 null 记 "—"；`biopython` 仅在生物序列专利场景相关。）

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| patent-search | illusionaireal | 显式触发式专利检索技能（/patent-search 或"专利检索"），可作触发语句与检索入口的模板 | https://github.com/illusionaireal/oh-my-patent#plugins~codex~plugins~oh-my-patent~skills~patent-search |
| bigquery-patent-search | RobThePCGuy | 基于 Google BigQuery 检索 7600 万+ 全球专利，支持关键词、CPC 分类与专利详情获取，字段口径可直接对齐 | https://github.com/RobThePCGuy/Claude-Patent-Creator#skills~bigquery-patent-search |
| uspto-database | Microck | 直连 USPTO 专利/商标 API：检索、审查历史、转让记录、引用与诉讼数据，含 Python 脚本与调用限速说明 | https://github.com/Microck/ordinary-claude-skills#skills_all~claude-scientific-skills~scientific-skills~uspto-database |
| free-patent-search-zhcn | yuanzhian-patsnap | 智慧芽免费 MCP 支撑的中文专利检索技能，覆盖新颖性检索、FTO、无效检索、法律状态核验与组合研究 | https://clawhub.ai/skills/free-patent-search-zhcn |
| patent-landscape | openclaw | 生物技术专利态势与空白点分析，含竞争者跟踪与 FTO 视角，适合"同主题还有谁在做" | https://github.com/openclaw/skills#skills~aipoch-ai~patent-landscape |
| linkfox-zhihuiya-cited-references | linkfox-ai | 查询专利前向引用详情（被引专利与非专利文献），补技术文献发现与引证链 | https://github.com/linkfox-ai/linkfox-skills#skills~linkfox-zhihuiya-cited-references |
| patent-invalid-search | openclaw | 无效检索视角的技能骨架：围绕权项要素找对比文件，可直接迁移为"权利要求要素 → 检索式"分解 | https://github.com/openclaw/skills#skills~chaochaosz~patent-invalid-search |

## 4. 推荐起点与复用方案

- **首选**：`Obviously-Not/patent-skills` —— 它是池内唯一"开源 Agent Skill 形态 + Go 实现 + MIT"的专利研究骨架，触发描述、任务拆分与产物结构都可直接照搬，改造成本最低；本题真正的难点（字段口径、状态诚实、族不穷尽）恰好可以在其流程节点上插入校验闸门。复用动作：拷 `SKILL.md` 骨架与子技能划分，把每个检索步骤改成"查询式 → 命中的号码 → 规范化后的字段表 → 来源与查询日期"四段留痕。
- **次选**：`google/patents-public-data` —— 补字段口径与族关系：它的 schema 明确区分 publication / application / grant / priority，并提供族与 CPC 的取法，可把"公开号=授权号"这类低级错误在结构上消灭。
- **组合建议（内核 + 适配层分离）**：
  - 内核（跨题复用）：`core/normalize.py`（号码规范化：国别码 + 种类码 + 去空格校验位）、`core/family.py`（族归并：优先权一致 → 同族候选，输出依据与"不穷尽"标记）、`core/claims.py`（权利要求版本定位：权项号 → 原文段落 + 版本号）、`core/status.py`（法律状态：只有官方登记可写确定值，否则 `unverified`）、`core/provenance.py`（库名 + 版本 + 查询日期 + 授权状态四元组）。
  - 本题适配层：`adapters/case_input.py`（6 条记录与 3 份影印件的读取，OCR 边界显式标记）、`adapters/schema_map.py`（字段表 → result.json 三段）、`adapters/policy.py`（哪些库属受限访问、哪些原文不得外显）。
  - 资源：`resources/field_dictionary.yaml`（每字段的定义、来源库、允许值）、`resources/kind_codes.csv`（A1/B1/B2 等种类码释义）、`resources/status_rules.md`（状态判定与"未知"的书写规范）。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 每条记录给出 `publication_number / application_number / priority_date / family_id + family_basis / cpc / assignee / source_db / queried_at`；权利要求按 `claim_no + version + quote + page` 定位；法律状态一律 `unverified` 并附"需回官方登记核对"的路径；OCR 段落标 `ocr=true + confidence`。 |
| 使用增益 25 | 与裸 Agent 同 6 条记录对照：字段正确率（号码/日期/族）、越权断言数（应为 0）、证据可定位率、二手源误引率。效率侧报人工核对字段与回官方库的次数下降。 |
| 创新增益 15 | 号码规范化器 + 族归并依据留痕 + "状态未知"的显式建模；权利要求版本对齐（同族不同版本权项范围不同，避免用错版本）；来源四元组让每条结论可回官方库核对。 |
| 可靠体验 10 | 号码缺种类码 → 输出候选解释并标 `ambiguous`；族链接不完整 → 写"观察到的同族，不穷尽"；OCR 残句 → 保留原文片段并标缺口，不补字；受限库 → 只登记元数据与访问条件，不抓正文；预算不足时优先保号码/日期/来源三件套。 |
| 生态贡献 10 | 交出字段字典、号码规范化规则、族归并判据、状态书写规范与"受限源处理"约定；附一份 6 条记录的字段表样例，可直接被其他查新任务套用。 |
| GitHub +5 | 仓库结构：`SKILL.md` + `core/` + `adapters/` + `resources/` + `examples/`（示例字段表，明确标注示例）+ `tests/`（号码规范化与族判定单测），根目录 `GITHUB_URL.txt`。 |

## 6. 落地清单

- [ ] 读 `case.json`，把 6 条记录的原始号码、来源库、查询日期抄进 `resources/records_raw.csv`（不改动原值）。
- [ ] 写 `core/normalize.py`：国别码/号码/种类码拆分与校验，输出 `ambiguous` 时给候选列表。
- [ ] 写 `core/family.py`：以优先权为第一判据，标题+申请人+时间为辅助，输出族组与"不穷尽"标记。
- [ ] 写 `core/claims.py`：按权项号定位原文段落，标注版本与 OCR 边界。
- [ ] 写 `core/status.py`：默认返回 `unverified`，仅当有官方登记证据时才允许给确定值，并在证据里写登记号与日期。
- [ ] 生成 `result.json` 三段；状态说明固定包含"法律状态未核验、族不穷尽、OCR 边界"三条。
- [ ] 对 3 份 USPTO 影印件做人工对照，核对抽出的号码/日期/权项是否与原件一致，差异写进 `/app/output/fidelity.md`。
- [ ] 写 `docs/limits.md`：不做侵权判定、FTO 结论与专利价值评估，不绕过付费墙、不外显受限原文。
