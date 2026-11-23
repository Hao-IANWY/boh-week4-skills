# 044 自查清单（对齐 100 + 5 评分表）

跑 `python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o` 后逐条自问，任何一条答"否"先修再提交。

## 实际效果 40

- [ ] **任务完成度 15**：完成"跨文件检索 → 章节定位 → 过滤执行 → 证据回链"整条交付，不是只给文件名或整篇文档。
- [ ] **结果正确性 15**：`matches[].quote` 是原文逐字子串（`quote_check.exact_match=true`）；`section_path`、`line_from/line_to`、`char_range` 与实际切页口径一致；`page` 与 `page_label` 未混用。
- [ ] **结果完整性 5**：过滤维度（年份/项目/类型/批次/版本/权限/扫描件）逐项出现在 `evidence.filters.per_query`，无文本层文件逐个列名。
- [ ] **结果可核验性 5**：任一命中可按 `evidence.index.chunk_table` 复原 chunk 边界；`evidence.retrieval.scoring` 足以复算 `score`。

## 使用增益 25

- [ ] **质量增益 15**：相对"文件名 grep + 整篇返回"，命中落到章节与页、行，且副本/旧版不重复计入证据。
- [ ] **效率增益 5**：一轮脚本给出定位 + 过滤判定表 + 缺口，人工只需复核 `gaps[]` 与 `excluded_files[]`。
- [ ] **增益可验证性 5**：同一输入重复运行结果一致（除 `generated_at`），可做对照实验。

## 创新增益 15

- [ ] **新增能力 5**：`R_FILTER_BLOCKED_HITS` —— 能区分"库内没有"与"库内有但不在提问范围内"，裸 Agent 通常混淆两者。
- [ ] **方法改进 5**：双记页码 + 标题树 chunk + 最小覆盖窗口邻近度 + 回链自检，四件事都落成可复算字段。
- [ ] **创新证据 5**：`evidence.canonical_text` 写明规范化口径，`quote_check` 给逐字复核结论。

## 可靠体验 10

- [ ] **易用性 3**：SKILL.md 有输入表、7 步流程、输出字段表、命令与失败分支。
- [ ] **稳定性 4**：空库 / 全无文本层 / 全被过滤 / 坏 JSON 各有确定行为（`empty` / `empty` / `partial` / `input_error`）。
- [ ] **异常处理 3**：无权限、加密、无文本层、重名副本、空结果都落到 `status.unmet` 或理由码，不写成完成。

## 生态贡献 10

- [ ] **文档 3**：`SKILL.md` + 本清单 + `examples/min_example/README.md` + `rules/*.json`。
- [ ] **可复用性 4**：`scripts/kit.py` 原样可复制到其它题；换学科只需改 `rules/retrieval_rules.json` 的 stopwords 与 heading_patterns。
- [ ] **推广价值 3**：过滤字典与理由码表可直接用于 043/045/046 的资料库类题目。

## GitHub +5

- [ ] 提交目录根有 `GITHUB_URL.txt`，首行为公开仓库链接，链接无需登录即可打开并定位到本题目录。
