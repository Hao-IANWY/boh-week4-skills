# 047｜阅读批注与知识沉淀

> **已实现可运行 Skill**：同目录 `SKILL.md` + `scripts/pipeline.py` + `rules/` + `examples/min_example/`；验收 `python3 answer/_shared/verify.py --only 047`；工程标准见 `answer/_shared/README.md`。本文以下内容为**开源起点调研**（为什么选这些仓库、怎么改）。

> 分类：资料管理 ｜ 赛题：challenge-c050aaed ｜ ARMI：ARMI:2611.000088v1 ｜ [详情页](https://play.bohrium.com/challenge/challenge-c050aaed)
> 起点调研数据：`sources_github.json`（GitHub 仓库检索+精选种子，含 stars/license/pushed_at）、`sources_skillhub.json`（SkillHub 现成 skill 语义检索）

## 1. 题目要点（读 detail.md 后提炼）

- **任务**：把散落在 PDF 高亮/划线、页边批注、扫描件手写标记、网页剪藏里的阅读判断，做成"批注提取→笔记结构化→知识关联→可检索入库"的闭环：每条批注能回到原页原位置，每条笔记论断能指到支撑它的原文片段，沉淀结果能被真实问题检索到。
- **输入**：`/input/case.json` + `task.md`；工作单为"授权修复的真实 9 文件级资料库"（真实文件、别名、行政性版本说明、元数据、检索/问答请求、恢复与索引操作）；题面强调实际输入文件是事实依据，不按错误的领域标签补造数据。可用工具：`pdf-text-tool`、`pdf-render-tool`、`ocr-tool`、`office-renderer`、`scientific-python`、`browser-automation`、`font-discovery`；工作区 `/app/work`，交付 `/app/output`。
- **输出契约**：`/app/output/result.json` 三段——① 主结果（批注清单：文件+哈希+页（印刷页/物理页）+坐标或锚点+类型（高亮/划线/页边/手写）+颜色+原文片段+标签；结构化笔记条目：问题/方法/结论/可复用要点/待验证疑点 + 每条挂 `annotation_id`；实体与关联（文献-方法-对象-项目）及关联依据；可检索索引入口）；② 证据记录（抽取脚本版本、OCR 置信度、锚点对齐方式与偏差、批注↔笔记映射表、文件哈希、人工改动 diff）；③ 状态说明（`case_id`、完成/部分完成、加密/损坏/无文本层/权限不足清单、低置信批注、旧版本批注归属、重复下载合并情况、不确定性）。
- **提交形态**：只交 `SKILL.md` 与必要文本资源（≤50 文件、单文件 ≤500KB、合计 ≤2MB、无符号链接）；官方 Harness 3 案例 × 3 重复，预算 64 请求 / 900 秒 / 16384 tokens，禁止自造分数。
- **评分重心**：40 分主项是"批注的原文片段、页码与位置与文件一致"（15）与"笔记论断有原文支撑、关联有依据"（15）。最容易丢分：锚点错位（挂到相邻段落/错误页）、批注类型与颜色语义被混成一类、OCR 后坐标漂移、笔记退化为摘抄没有来源定位、同一文献多版本把批注拆散。
- **裸 Agent 典型失败**：
  1. 抽出批注文字但丢了页与位置，或把双栏左栏批注挂到右栏对应段落；印刷页码与 PDF 物理页混用。
  2. 高亮、页边批注、手写标记一律当"高亮"，颜色含义抹平，标签全靠摘要猜。
  3. 笔记只写"这篇方法有创新"这类无定位论断；关联靠关键词重合，共用一个术语就连成一片。

## 2. 开源起点候选（GitHub）

| 仓库 | ★ | 语言 | 许可 | 定位 | 可复用点 |
|---|---:|---|---|---|---|
| logseq/logseq | 45648 | Clojure | AGPL-3.0 | 参考实现 | 以 block 为最小单元 + property 的笔记结构，可直接当"结构化笔记条目 + 双向引用"的落库格式 |
| obsidianmd/obsidian-releases | 23370 | — | — | 数据集 | 社区插件清单：快速筛出"批注/高亮/笔记同步"类插件的生态现状，避免重复造轮子 |
| zotero/zotero | 15980 | JavaScript | NOASSERTION | 参考实现 | annotation 数据模型（type/color/position rects/pageLabel/sortIndex/comment/tags）是"类型、颜色、坐标、页码标签"最完整的开源规范 |
| hypothesis/client | 744 | Mustache | NOASSERTION | 参考实现 | Web Annotation 数据模型 + 文本锚定（TextQuote/TextPosition/Range selector），解决"没有坐标时如何稳定锚定一段文字" |
| 0xabu/pdfannots | 676 | Python | MIT | 工具库 | 轻量纯 Python 抽取 PDF 标注：类型、页码、引文、批注内容，是锚点层的最小可靠底座 |
| cboulanger/citext | 4 | JavaScript | GPL-3.0 | 参考实现 | 基于 AnyStyle 的引文抽取前端，参考其"标注→结构化字段"的人工校正交互 |
| KunalKalawant/Engineering-Drawing- | 4 | Python | — | 参考实现 | OCR 文本抽取 + 交互式 PDF 标注 + CSV 导出，可参考其 OCR 坐标与页面坐标的换算处理 |
| Kakauiber/Cat_Highlighter | 2 | JavaScript | MIT | 工具库 | 网页高亮 + 页内随手记 + 知识导出，提供网页剪藏批注的抽取路径 |

## 3. SkillHub 现成 Skill 候选

| Skill | 作者 | 说明 | 来源 |
|---|---|---|---|
| bib-search-citation | bahayonghang | 在本地 BibTeX/Zotero 导出库中检索与引用，可把批注所属文献对齐到题录权威字段 | https://github.com/bahayonghang/academic-writing-skills/tree/main/academic-writing-skills/bib-search-citation |
| litbase | aipoch | 医学研究证据库技能，按证据层级组织文献与要点，参考其"要点挂证据"的条目结构 | https://github.com/aipoch/medical-research-skills#awesome-med-research-skills~Evidence Insight~litbase |
| radiology-reader | huang-sir1 | 生成中英对照、图表感知、来源可溯的全文 Markdown 阅读件，适合当批注锚点的规范化载体 | https://github.com/huang-sir1/radiology-skills#radiology-skills~modules~radiology-reader |
| vault-normalizer | poco-ai | 把既有研究目录规范成 Agentero vault 布局（论文/笔记/索引），直接对上"沉淀结果入规范结构" | https://github.com/poco-ai/Agentero#templates~vault~.agents~skills~vault-normalizer |
| immersive-reading | ryannli | 把长文材料转成可交互学习空间，参考其"原文片段↔笔记"的挂接方式 | https://github.com/ryannli/immersive-reading#skills~immersive-reading |
| pdf-co | openclaw | PDF 转换/拆分/编辑与数据抽取 API 集成，用于扫描件与异常 PDF 的兜底处理 | https://github.com/openclaw/skills#skills~byungkyu~pdf-co |
| project-note-json-to-epub | twhsi | 结构化笔记 JSON → EPUB/PDF 的产出链路，可把沉淀结果做成可交接阅读件 | https://github.com/twhsi/skills#skills~project-note-json-to-epub |

## 4. 推荐起点与复用方案

- **首选**：`0xabu/pdfannots` —— 唯一轻量、MIT、纯 Python 的 PDF 批注抽取工具，输出天然带"页 + 类型 + 引文 + 批注内容"，正好是本题锚点可靠性的最小底座，且不引入重量级依赖，符合 900 秒预算。复用动作：① 直接调用其抽取函数拿批注三元组（页/类型/文本），再补 bbox 与颜色；② 用它的"页内引文"做锚点校验的比对基准；③ 以其输出 schema 为 `annotations[]` 起点，扩展 `page_label`（印刷页）与 `anchor`。
- **次选**：`zotero/zotero` —— 补它缺的规范：annotation 的 `type`（highlight/underline/note/text/image）、`color`、`position.rects`、`pageLabel`、`sortIndex`、`tags` 是"类型不混类、颜色不丢义、印刷页码可追"的现成字段模型；同时提供"同一文献多版本/重复下载的批注归并"参考做法。
- **组合建议**：内核与适配层分开：

```
SKILL.md                    # 触发条件、抽取→结构化→关联→入库→检索 五步、失败分支
core/extract.py             # PDF/网页/Word/扫描件 → 批注记录（含 bbox、颜色、类型、page_label）
core/anchor.py              # 锚点对齐与校正：文字片段锚定 + 页偏移校准 + 双栏归属
core/notes.py               # 批注 → 结构化笔记（问题/方法/结论/要点/疑点），保留 annotation_id
core/link.py                # 实体与关联规则（依据 + 置信 + 过度连接抑制）
core/index.py               # 检索入口：主题/标签/实体/来源文献/时间
adapters/case_input.py      # case.json / task.md → 材料范围与笔记字段约定
adapters/type_color.py      # 批注类型与颜色语义表（可配置，来自题面或材料说明）
adapters/dupver.py          # 重复下载与多版本批注归并策略
adapters/report.py          # result.json 三段拼装
```

- **分工理由**：`core/anchor.py` 与 `core/link.py` 是本题真正的技术难点，独立成模块便于单测与跨题复用（044 的定位器、049 的证据表都能用）；`adapters/` 只放题面/材料相关的类型表与归并策略。

## 5. 增量设计（对齐 100+5 评分表）

| 评分维度 | 本题做法 |
|---|---|
| 实际效果 40 | 批注记录强制六要素（文件+哈希、物理页+印刷页、bbox 或文字锚、类型、颜色、原文片段）；笔记每条论断挂 `annotation_id`；关联边带依据类型（共引/同方法/同对象）与置信；入库后跑一次"抽样回链"自检：随机抽 N 条，按记录回原文比对字符 |
| 使用增益 25 | 固定对照：锚点正确率（抽样人工比对）、批注类型保留率、笔记条目可回链率、关联精确率（抽样）；同材料跑 Skill/裸 Agent 各 3 次，比同一抽样金标准，只报差值 |
| 创新增益 15 | ① 双锚定：有 bbox 用 bbox，无坐标用 TextQuote 文本锚（解决扫描件 OCR 坐标漂移）；② 页偏移自动校准（页脚/页码序列对齐，不写死 ±1）；③ 批注粒度去重——同一文献多版本按文本指纹归并为一条并标注"版本差异"，避免批注被拆散 |
| 可靠体验 10 | 失败分级 `encrypted`/`damaged`/`no_text_layer`/`ocr_low_conf`/`stale_annotation`（批注来自旧版本）/`duplicate`/`no_permission`；低置信批注单列不混入主结论；中断时先存已完成批注 + 未抽文件清单 |
| 生态贡献 10 | 交付：批注数据 schema、类型与颜色语义表、笔记字段字典、关联依据规范、锚点校验方法、最小示例（标明示例）、不适用边界（纯图无文本层、手写极难识别）；core 可跨题复用 |
| GitHub +5 | 仓库结构：`SKILL.md` / `core/` / `adapters/` / `schema/`（批注与笔记 JSON Schema）/ `docs/` / `examples/` / `tests/`（抽样回链回归）/ `GITHUB_URL.txt` |

## 6. 落地清单

- [ ] 读 `detail.md` 与 `/input/case.json`，确认材料类型、批注载体、笔记字段约定，写入 `adapters/` 与 `schema/`。
- [ ] 写 `core/extract.py`：接 `pdfannots` 抽 PDF 批注，补 bbox/颜色；网页与 Word 走各自解析；扫描件走 OCR 并记置信度。
- [ ] 写 `adapters/type_color.py`：类型枚举与颜色语义表（来自材料说明或用户配置），类型不混类。
- [ ] 写 `core/anchor.py`：文字锚 + bbox 双通道、页偏移校准、双栏归属判定，输出偏差量与置信。
- [ ] 写 `core/notes.py`：批注 → 结构化条目（问题/方法/结论/要点/疑点），保留 `annotation_id` 与出处。
- [ ] 写 `adapters/dupver.py` + `core/link.py`：多版本批注归并、关联规则与过度连接抑制。
- [ ] 写 `core/index.py`：主题/标签/实体/来源/时间五维检索入口，并给出误召回与漏召回的处置说明。
- [ ] 写 `SKILL.md` 与 `examples/`，跑通"抽取→结构化→入库→检索"一次闭环，抽样回链 10 条并记录结果。