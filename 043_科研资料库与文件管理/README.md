# 043｜科研资料库与文件管理

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 043`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：资料管理 ｜ 赛题：challenge-2bfd9194 ｜ ARMI：ARMI:2611.000084v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-2bfd9194)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：做一个可被 Agent 调用的 Skill，把组织方给定的一批真实科研文件（含别名副本、行政性版本说明、元数据）真正落库：生成目录树、写元数据清单、按内容哈希重建版本关系、留可复跑的操作日志——不是给整理建议。
- **输入**：`/input/case.json` + `task.md`；工作单为"授权修复的真实 9 文件级资料库"（真实文件、别名、版本说明、元数据、检索/问答请求、恢复与索引操作）。可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`office-renderer`、`latex-compiler`、`scientific-python`、`browser-automation`、`font-discovery`；产物落 `/app/work`，最终结论落 `/app/output`。
- **输出契约**：`/app/output/result.json` 必须三段齐全——① 主结果（目录树、文件清单、逐文件元数据字段、重复组与版本链、可检索索引入口）；② 证据记录（每文件 sha256 与文件大小、来源路径、权限位、变更/移动记录、操作日志与脚本版本、引用页码或段落）；③ 状态说明（`case_id`、完成/部分完成、跳过清单及原因、冲突与无权限处置、恢复方式、不确定性）。
- **提交形态**：只交 `SKILL.md` 与必要文本资源（≤50 个文件、单文件 ≤500KB、合计 ≤2MB、无符号链接），评分侧把 Skill 装到 `/skills/weekly-skill` 后用官方 DeepSeek Harness 跑 3 个固定案例；预算 64 次模型请求 / 900 秒 / 单响应 16384 tokens，三家三重复，禁止自造分数。
- **评分重心**：40 分里最关键的是"落库后的文件与源文件一一对应"（15）＋"重复与版本按内容判断而非文件名"（15）；`完整`（5）与`可核验`（5）本质是禁止静默跳过、禁止无清单。最容易丢分处：把未完成写成完成、同名文件直接覆盖、元数据靠文件名猜、缺 sha256。
- **裸 Agent 典型失败**：
  1. 只提交一份"分类建议 + 目标目录草图"，文件没动、清单没落，输出无法与源文件一一对应。
  2. 用文件名或 mtime 判重复/版本，遇到 `xxx(1).pdf`、`_final_v2`、改过名的备份就断链；同名不同来源直接覆盖，事后无法回滚。
  3. 对无文本层扫描件、深层嵌套目录、非 UTF-8 文件名、无权限目录静默跳过，报告写"全部完成"，跳过清单为空。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| paperless-ngx/paperless-ngx | 48250 | Python | GPL-3.0 | 参考实现 | `storage_paths` 模板就是把"目录约定"写成可执行配置的现成 DSL；consume 管道的 checksum 去重可直接照搬为版本判据 |
| logseq/logseq | 45648 | Clojure | AGPL-3.0 | 参考实现 | block 级 Markdown + property/frontmatter 约定，可当元数据清单与交接文档的落地格式 |
| kovidgoyal/calibre | 26464 | Python | GPL-3.0 | 工具库 | `metadata.db` 的 books/authors/identifiers/formats 表结构是现成字段字典；`calibredb` CLI 可外部批处理导入 |
| zotero/zotero | 15980 | JavaScript | NOASSERTION | 参考实现 | itemType/creator/DOI/attachment 条目模型是科研元数据的事实标准，直接映射清单字段与出处标注 |
| AgriciDaniel/claude-obsidian | 16587 | Python | MIT | SKILL 骨架 | 自带"投入即整理"的 SKILL.md 流程与目录约定，可裁剪成落库动作序列（盘点→归位→写清单→校验） |
| TagStudioDev/TagStudio | 7303 | Python | GPL-3.0 | 参考实现 | 文件条目表 + tag 树 + hash 去重的库结构，正是"按内容判断重复"的落库骨架 |
| coddingtonbear/obsidian-local-rest-api | 3166 | TypeScript | MIT | 工具库 | 文件读写/移动/改名的 REST+MCP 接口，便于把每次整理动作转成可回放操作日志 |
| cyanheads/obsidian-mcp-server | 712 | TypeScript | Apache-2.0 | 工具库 | frontmatter 原子读写 + 搜索，用于把元数据回写 Markdown 清单并做字段一致性校验 |
| devwhodevs/engraph | 175 | Rust | MIT | 工具库 | 本地知识图谱 + 混合检索，可充当"整理完是否真的可检索"的验收端 |
| bitbonsai/mcpvault | 1725 | TypeScript | MIT | 工具库 | 轻量 MCP 文件访问层，做"只读盘点 + 受控写入"两档权限，避免整理脚本误删 |

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| obsidian-knowledge-organizer | openclaw | 导入文章、组织笔记、维护索引的库整理流程，可直接改成落库动作编排 | https://github.com/openclaw/skills#skills~cjke84~obsidian-knowledge-organizer |
| material-organizer | Orkas-AI | 把用户材料或本地目录整理成可追溯笔记、知识卡、分类与索引，最贴近本题"落库+清单" | https://github.com/Orkas-AI/Orkas/tree/main/resources/builtin/marketplace/skills/9be6fda271a5 |
| knowledge-synthesizer | zenobi-us | 从多源信息提取要点并建关系图，用于版本差异之外的内容级关联 | https://github.com/zenobi-us/dotfiles#ai~files~skills~experts~meta-orchestration~knowledge-synthesizer |
| context-manager | zenobi-us | 共享状态管理与快速检索，可借其状态模型做操作日志与断点续跑 | https://github.com/zenobi-us/dotfiles#ai~files~skills~experts~meta-orchestration~context-manager |
| Doc Setup | brauliodiasribeiro | 把任意来源文档整理成带索引的知识文档集，提供索引 schema 参考 | https://clawhub.ai/skills/doc-setup |
| agent-organizer | zenobi-us | 把复杂整理任务拆解、按能力分工并管理依赖与执行流程，用于大批次归位的步骤编排 | https://github.com/zenobi-us/dotfiles#ai~files~skills~experts~meta-orchestration~agent-organizer |
| Fragment Thoughts Organizer En | sheyuy | 把散落片段归位到既有结构的实践做法，借鉴其"归位前先判定归属依据"的规则 | https://clawhub.ai/skills/fragment-thoughts-organizer-en |

## 4. 推荐起点与复用方案

- **首选**：`paperless-ngx/paperless-ngx` —— 它是唯一把"目录约定"做成可执行配置（storage path 模板）而非文档说明的开源实现，正好对上本题"目录树与约定一致"这一 15 分主项。复用动作：① 拷 `storage_paths` 模板语法做目录树 DSL（`{created_year}/{correspondent}/{document_type}` → 本题字段）；② 借 Document/Correspondent/Tag/StoragePath 模型做字段字典与出处标注；③ 照搬其 consume 阶段的 checksum 去重作为"重复 vs 版本"判据。
- **次选**：`TagStudioDev/TagStudio` —— 补它缺的两块：SQLite 条目表 + tag 树 + 文件哈希的表设计，用来存版本链与重复组；以及它的"文件不动、库内挂元数据"思路，便于做非破坏性落库与回滚。
- **组合建议**：内核与适配层严格分开，题面约定只进适配层：

```
SKILL.md                 # 触发条件、调用顺序、失败分支、输入输出契约
core/manifest.py         # 遍历 + sha256 + 清单 schema
core/versions.py         # 重复组 / 版本链重建
core/paths.py            # 目录模板渲染 + 命名规范化
core/journal.py          # 操作日志 + rollback.sh 生成
core/verify.py           # 清单↔磁盘双向核对
adapters/case_input.py   # /input/case.json·task.md → 任务参数
adapters/field_map.yaml  # 元数据字段 → 表头 → 出处
adapters/skip_policy.py  # 权限/加密/无文本层/重名处置
adapters/report.py       # result.json 三段拼装
```

- **分工理由**：`core/` 只管"文件的可信台账"——哈希、版本、模板渲染、日志回滚，与科研场景无关，可直接搬到 044/045；`adapters/` 放题面约定（目录模板、字段字典、跳过策略、状态枚举），评审换一批案例只改这一层；`SKILL.md` 只写触发条件、调用顺序与失败分支，不写具体路径常量，避免换环境即失效。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 真跑：盘点→按模板归位→改名→写 `manifest.csv`+`metadata.jsonl`→版本链→`journal.jsonl`；每文件 sha256、原名、新名、来源、权限位齐全；末尾跑 `verify.py` 做双向核对，差异即写进失败记录 |
| 使用增益 25 | 对照点固定为：哈希去重正确率、元数据字段填充率、版本链正确条数、静默跳过数（应为 0）；同输入同权限跑 Skill/裸 Agent 各 3 次，用同一批文件快照与同一字段白板比较，只报差异项不报自评 |
| 创新增益 15 | 三件裸 Agent 做不稳的事：① 目录模板 DSL（约定变更只改模板不改代码）；② 内容哈希 + 时间双阈值的去重/版本二分（先判重复再判版本）；③ 每次动作生成可回滚的逆操作脚本（move/rename 反向指令） |
| 可靠体验 10 | 失败分级：`skipped`（无权限/加密）→ `failed`（哈希不符/损坏）→ `conflict`（同名不同内容）；每级给处置动作与恢复命令；超预算或中断时先落 `partial` 清单 + 已完成项，续跑靠 journal 的 offset |
| 生态贡献 10 | 交付目录规范说明、字段字典（含出处列）、命名规则、去重/版本策略流程图、最小调用示例（标明示例）、限制与不适用场景；core 脚本无本题硬编码，可直接搬题 |
| GitHub +5 | 仓库结构：`SKILL.md` / `core/` / `adapters/` / `templates/`（目录与命名模板）/ `docs/`（字段字典、策略、限制）/ `examples/`（示意输入输出，标明示例）/ `GITHUB_URL.txt` 指向本题目录 |

## 6. 落地清单

- [ ] 读 `detail.md` 与 `/input/case.json`，把目录约定、字段清单、命名规则抄成 `adapters/field_map.yaml` + `templates/paths.yaml`。
- [ ] 写 `core/manifest.py`：递归遍历（含中文/空格/特殊字符名）、sha256、大小、mtime、权限位、相对路径，输出 `manifest.csv`。
- [ ] 写 `core/versions.py`：同哈希→重复组；同族不同哈希→按 mtime+内容差异建版本链，输出 `versions.json`（`supersedes`/`superseded_by`/`diff_summary`）。
- [ ] 写 `core/paths.py`：模板渲染 + 命名规范化（保留原名映射），落 `journal.jsonl` 并同时生成 `rollback.sh`。
- [ ] 写 `adapters/skip_policy.py`：无权限、加密 PDF、无文本层扫描件、深层目录、重名的分类处置与记录字段。
- [ ] 写 `core/verify.py`（清单↔磁盘双向核对，输出缺失/多余/哈希不符三张表）与 `SKILL.md`（触发条件、调用顺序、失败分支、输入输出契约）。
- [ ] 加 `--dry-run`：只产出计划与实际差异预估，便于先给人看成目录树再落盘。
- [ ] 用最小示例跑一遍并留 `examples/`，确认 `result.json` 三段字段齐全（含 `case_id`、跳过清单、不确定性）。