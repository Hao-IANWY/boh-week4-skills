# 045 自查清单（对齐 100 + 5 评分表）

跑 `python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/o` 后逐条自问，任何一条答"否"先修再提交。

## 实际效果 40

- [ ] **任务完成度 15**：真正给出目录归属 + 标签 + 新文件名 + 重复组 + 版本族 + 操作计划，不是"建议分类表"。
- [ ] **结果正确性 15**：类别判定可回指 `doc_type.labels[].hits[]`（metadata/term/negative + 字段 + 位置）；标签只来自词表；命名缺字段时用 null_token 且 `degraded=true`，**没有补造 DOI/作者/年份**。
- [ ] **结果完整性 5**：批次内文件全覆盖（`classification[]` + `unclassified[]` + `inventory.unreadable` 三者之并 = 输入文件数）。
- [ ] **结果可核验性 5**：每步操作有 `idempotency_key` 与 `rollback`；`evidence.inventory.hash_groups` 可核对副本判定。

## 使用增益 25

- [ ] **质量增益 15**：相对"聊天给建议"，本 skill 直接产出可执行计划并可 `--apply` 落盘，且副本/版本不重复计证据。
- [ ] **效率增益 5**：一轮脚本给出全部文件的归属、命名与操作，人工只需复核 `unclassified` / `low_confidence`。
- [ ] **增益可验证性 5**：阈值与权重全在 `rules/classification_rules.json`；改参数后重跑即可对照。

## 创新增益 15

- [ ] **新增能力 5**：把"判不出"制度化 —— 未分类与低置信单列，附最接近候选与缺失字段。
- [ ] **方法改进 5**：饱和置信度（`raw/(raw+k)`）+ 词边界匹配（避免 retrosynthesis⊃thesis 这类子串误判）+ 版本角色排序。
- [ ] **创新证据 5**：`evidence.rules.formula` 与 `label_evidence` 让每个分数可手算复核。

## 可靠体验 10

- [ ] **易用性 3**：SKILL.md 有输入表、7 步流程、输出字段表、`--apply` 用法与失败分支。
- [ ] **稳定性 4**：空批 / 全无文本 / 全落 review / 坏 JSON 各有确定行为；重复运行幂等。
- [ ] **异常处理 3**：不可读、重名不同版本、目标冲突、权限不足都落理由码，不写成完成。

## 生态贡献 10

- [ ] **文档 3**：`SKILL.md` + 本清单 + `examples/min_example/README.md` + `rules/*.json`。
- [ ] **可复用性 4**：换学科只需改 `taxonomy.json` / `tag_vocabulary.json`；`classification_rules.json` 的命名模板可跨题复用。
- [ ] **推广价值 3**：规则表 + 理由码 + 操作日志结构可直接用于 043/044/046 的资料整理类题目。

## GitHub +5

- [ ] 提交目录根有 `GITHUB_URL.txt`，首行为公开仓库链接，链接无需登录即可打开并定位到本题目录。
