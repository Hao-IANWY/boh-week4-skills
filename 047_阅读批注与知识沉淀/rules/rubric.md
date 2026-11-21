# 047 自查清单（对齐 100 + 5 评分表）

跑 `python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o` 后逐条自问，任何一条答"否"先修再提交。

## 实际效果 40

- [ ] **任务完成度 15**：走完"批注抽取 → 锚点校正 → 笔记结构化 → 关联 → 检索入口"闭环，交付可用结果而不是批注列表。
- [ ] **结果正确性 15**：`verified` 批注的引文在指定页可逐字找到；`page_mismatch` 给了校正页；`not_found` 没硬挂位置；笔记 `claim` 有 `support_quote` 支撑。
- [ ] **结果完整性 5**：材料（含不可读）、批注类型/颜色、锚点五态、笔记↔批注映射、关联与抑制记录都在产物里。
- [ ] **结果可核验性 5**：`evidence.anchors.per_annotation` 可逐条复核；材料 `sha256` 与版本可对；`confidence_formula` 可手算。

## 使用增益 25

- [ ] **质量增益 15**：相对"导出批注文本"，本 skill 保留页/位置/类型/颜色语义，并把错位锚点校正出来。
- [ ] **效率增益 5**：一轮脚本给出笔记库 + 索引 + 召回演示，人工只需复核 `not_found` 与 `ambiguous`。
- [ ] **增益可验证性 5**：阈值都在 `rules/distill_rules.json`；重复运行一致，可做对照。

## 创新增益 15

- [ ] **新增能力 5**：五态锚点校正（verified/page_mismatch/ambiguous/not_found/unverifiable），把"批注挂错页"变成可报告的结论。
- [ ] **方法改进 5**：类型先分形态再谈语义；颜色语义表化；连边按强度贪心 + 抑制记录。
- [ ] **创新证据 5**：`evidence.links` 记录保留/抑制数量与规则，`evidence.anchors.method` 写明校正口径。

## 可靠体验 10

- [ ] **易用性 3**：SKILL.md 有输入表、5 步流程、输出字段表、失败分支。
- [ ] **稳定性 4**：无批注 / 无文本层 / 全 not_found / 坏 JSON 各有确定行为；幂等。
- [ ] **异常处理 3**：未知类型/颜色、OCR 漂移、跨版本副本、连边上限都落理由码，不写成完成。

## 生态贡献 10

- [ ] **文档 3**：`SKILL.md` + 本清单 + `examples/min_example/README.md` + `rules/*.json`。
- [ ] **可复用性 4**：`annotation_taxonomy.json` 可直接换成别的阅读器（Zotero/MarginNote）的字段映射。
- [ ] **推广价值 3**：锚点五态 + 笔记 schema 可作为"阅读沉淀"类题目的通用骨架（044/046/049 共用坐标口径）。

## GitHub +5

- [ ] 提交目录根有 `GITHUB_URL.txt`，首行为公开仓库链接，链接无需登录即可打开并定位到本题目录。
