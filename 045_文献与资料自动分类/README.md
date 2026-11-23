# 045｜文献与资料自动分类

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 045`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：资料管理 ｜ 赛题：challenge-666fefb0 ｜ ARMI：ARMI:2611.000087v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-666fefb0)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：把一批混杂的科研文献 PDF、预印本、会议论文、学位论文、补充材料、数据集、扫描件，真正完成"归类入目录 + 打标签 + 按模板改名 + 识别重复下载与预印本/正式版差异 + 批量整理"，操作可回滚、结果可幂等重跑、每份归属都能用原文与元数据核对。
- **输入**：`/input/case.json` + `task.md`；工作单为"授权修复的真实 9 文件级资料库"（真实文件、别名、行政性版本说明、元数据、检索/问答请求、恢复与索引操作）。可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`office-renderer`、`scientific-python`、`browser-automation`、`font-discovery`；工作区 `/app/work`，交付 `/app/output`。
- **输出契约**：`/app/output/result.json` 三段——① 主结果（每文件的目录归属与依据、标签集与抽取出处、新文件名（原名→新名映射）、重复组、版本族（预印本/正式版/补充材料/修订版）、批量操作结果、未处理项清单）；② 证据记录（sha256、DOI/标题/作者/年份字段及其出处（原文位置或元数据文件）、规则表版本、标签词表版本、操作日志与逆操作、幂等键）；③ 状态说明（`case_id`、完成/部分完成、`skipped`/`failed`/`conflict` 分类计数、损坏与加密文件、缺失字段的如实标注、不确定性）。
- **提交形态**：只交 `SKILL.md` 与必要文本资源（≤50 文件、单文件 ≤500KB、合计 ≤2MB、无符号链接）；官方 Harness 3 案例 × 3 重复，预算 64 请求 / 900 秒 / 16384 tokens，禁止自造分数。
- **评分重心**：40 分主项是"目录归属、标签、文件名、去重结论与原文及元数据一致"（15）和"覆盖批次全部文件、六类要素逐一交代"（15）。最容易丢分：把分类做成聊天建议不动盘、元数据缺失时凭空补 DOI/作者/年份、同名不同版本直接覆盖或误删、批量改名不可回滚、重复运行结果漂移。
- **裸 Agent 典型失败**：
  1. 输出一份"建议分类表"就结束，目录没动、文件名没改，交付物进不了资料库。
  2. 只按文件名/标题关键词判类，缺 DOI 时自己编一个能对上格式的号；标签一次给出几十个近义词，造成标签爆炸且无法归并。
  3. 同名文件用后者覆盖前者、`*_v2` 与 `*_final` 当成两份独立文献；失败了没有回滚记录，重跑一次结果又不一样。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| paperless-ngx/paperless-ngx | 48250 | Python | GPL-3.0 | 参考实现 | 落库骨架：consume 管道 + 归档目录模板 + 标签树 + checksum 去重，正好是"分类要真的动盘"的载体 |
| OpenRefine/OpenRefine | 12110 | Java | BSD-3-Clause | 工具库 | 标签归一与模糊聚类（key collision / n-gram / 编辑距离）现成可用，且所有批量编辑可撤销，天然满足"可回滚" |
| TagStudioDev/TagStudio | 7303 | Python | GPL-3.0 | 参考实现 | 文件条目 + tag 树 + 文件哈希的表结构，用来存重复组与版本族，文件本体不动、库内挂元数据 |
| clusterzx/paperless-ai | 6122 | TypeScript | MIT | 参考实现 | AI companion：自动起标题、打标签、归档目录，其字段抽取 prompt 与 document_type 枚举可直接改成题面字段映射 |
| sebischair/Lbl2Vec | 185 | Python | BSD-3-Clause | 工具库 | 无标注冷启动的标签分配（标签/文档/词联合嵌入），元数据缺失或语料少时的降级打标方案，且返回相似度可解释 |
| davidsbatista/text-classification | 109 | Jupyter Notebook | — | 参考实现 | 多标签文本分类的训练/评估样例，可借其评估口径（P/R/F1 与混淆矩阵）做分类质量对照 |
| dayyass/text-classification-baseline | 61 | Python | MIT | 工具库 | TF-IDF + LogReg 快速基线，用于"轻量规则/模型"档位，避免一上来就上大模型 |
| nampara-ai/librarian | 49 | Python | MIT | 工具库 | 语料清洗与整理管道（解析/校对/索引），适合处理乱码、缺页、非规范命名的脏批 |
| knows-cloud/paperless-iq | 16 | Python | NOASSERTION | 参考实现 | 自动元数据与打标 + RAG 检索的插件形态，参考其"先抽取再人工确认"的两段提交 |

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| knowledge-base-manager | oakoss | 知识库架构选择、治理与策展，提供"何时用目录/标签/RAG"的决策框架 | https://github.com/oakoss/agent-skills/tree/main/skills/knowledge-base-manager |
| obsidian-file-manager | arlenagreer | 在 vault 内管理文档文件：创建、命名、归位的操作规范，可改成批量改名流程 | https://github.com/arlenagreer/claude_configuration_docs#skills~obsidian-file-manager |
| golden-dataset-curation | yonatangross | 金标准数据集条目的质量准则与多智能体复核模式，用来做"分类/标签正确性"自检集 | https://github.com/yonatangross/create-yg-app#.claude~skills~golden-dataset-curation |
| transformers | synthetic-sciences | 预训练 transformer 的文本分类/序列标注用法（含少量样本微调），做分类器实现参考 | https://github.com/synthetic-sciences/openscience#backend~cli~skills~llm-tools~transformers |
| image-breaker | openclaw | 把 PDF/图片/网页拆成结构化 Markdown 笔记入库，解决扫描件与版式文档的预清洗 | https://github.com/openclaw/skills#skills~biohackerrrrrr~image-breaker |
| scansnap-bridge | idanbeck | 扫描仪→PDF 的入库衔接流程，参考其"扫描件命名与批次登记"约定 | https://github.com/idanbeck/claude-skills/tree/main/scansnap-bridge |

## 4. 推荐起点与复用方案

- **首选**：`clusterzx/paperless-ai` —— 这批里最贴合本题三件事（归类、打标签、自动命名）的完整实现，且是 MIT，抽取字段恰好就是题面要的标题/作者/年份/期刊/DOI/类型。复用动作：① 拷它的字段抽取 prompt 与 document_type 枚举，改成题面的文献类型枚举与目录层级；② 复用它的"抽取→建议→落库"两段式提交，第二段强制带逆操作；③ 把它对重名/重复的处理替换成本题的哈希判定（它按标题相似度，不够硬）。
- **次选**：`OpenRefine/OpenRefine` —— 补标签归一与同义合并：借用 key collision / n-gram fingerprint 两类聚类算法做标签收敛，压制标签爆炸；它的批量编辑全可撤销，正好对应"改名去重可回滚"这一评分点。
- **组合建议**：内核与适配层分开：

```
SKILL.md                    # 触发条件、整理六步顺序（盘点→抽取→归类→命名→去重→落库）、失败分支
core/hash.py                # sha256 + 分块哈希（大文件流式）
core/dedupe.py              # 重复组判定（哈希强判 + DOI/标题-作者-年份弱判）
core/version.py             # 版本族：预印本/正式版/补充材料/修订版
core/naming.py              # 命名模板渲染 + 特殊字符/超长/多语言降级
core/ops.py                 # 幂等执行器：plan → apply → journal → rollback
adapters/case_input.py      # case.json / task.md → 目录约定与字段清单
adapters/tagging.py         # 标签抽取、归一、粒度控制与词表版本
adapters/field_provenance.py# 每个字段值 → 出处（原文位置/伴随表格/人工补录）
adapters/report.py          # result.json 三段拼装
```

- **分工理由**：`core/` 与文献领域无关，换批次可直接复用；`adapters/` 承载题面的目录层级、字段字典、标签词表与准入标准，评分差异集中在这里可读可审。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 真动盘：`plan.json`（每文件的归属/标签/新名/去重结论/依据）→ 评审可读的计划 → apply 落盘；落盘后逐项校验：原文件数=已归位+未处理、每文件 sha256 不变、原名可回溯、标签有出处、版本族有判据 |
| 使用增益 25 | 固定对照：目录归属正确率、字段填充率（含"如实标注缺失"的比例）、去重/版本判定正确条数、人工改名条目数；同批次跑 Skill/裸 Agent 各 3 次，比同一金标准清单，报差值不报自评 |
| 创新增益 15 | ① 三级去重（哈希强判 → DOI 弱判 → 标题+作者+年份弱判）并输出判据等级，避免误删；② 标签收敛：抽取后先归一再落库，粒度上限与同义表版本化；③ 幂等键 = `sha256+目标路径+规则版本`，重跑只补差不动已完成项 |
| 可靠体验 10 | 失败分级 `damaged`/`encrypted`/`no_text_layer`/`garble`/`conflict`/`no_permission`；每级给处置与日志；`--dry-run` 默认开；中断后续跑靠 journal offset；超预算先落已完成 + 未处理清单 |
| 生态贡献 10 | 交付规则表、命名模板、标签词表、字段映射表、最小示例（标明示例）、适用/不适用边界；core 脚本零题面硬编码 |
| GitHub +5 | 仓库结构：`SKILL.md` / `core/` / `adapters/` / `rules/`（命名模板、标签词表、类型枚举）/ `docs/` / `examples/` / `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `detail.md` 与 `/input/case.json`，固化目录层级、字段清单、文献类型枚举、命名模板到 `adapters/` + `rules/`。
- [ ] 写 `core/hash.py`：流式 sha256、分块哈希（大文件/损坏文件的差异定位）。
- [ ] 写 `core/dedupe.py` + `core/version.py`：三级去重与版本族，输出判据等级与"保留/归档"建议。
- [ ] 写 `adapters/field_provenance.py`：每字段带出处，抽不到就写缺失，禁止推断填值。
- [ ] 写 `core/naming.py` + `rules/naming.yaml`：模板渲染、特殊字符与超长标题降级、多语言标题策略。
- [ ] 写 `core/ops.py`：`plan/apply/journal/rollback` 四段 + 幂等键，确保重跑不漂移。
- [ ] 写 `adapters/tagging.py`：抽取→归一→粒度上限→落库，词表版本写入证据。
- [ ] 写 `SKILL.md` 与 `examples/`，用最小批次跑通一次完整整理并核对 result.json 三段与 `--dry-run` 计划一致。