# 011 专利检索判定口径（rules/rubric.md）

本文件是 `scripts/pipeline.py` 的判定说明与评审对照表。可执行版本在 `field_dictionary.json`（字段口径）、
`ipc_topics.json`（分类号与检索式模板）、`scoring.json`（排序/族归并/风险分级参数）。

## 1. 号码口径（本题最易丢分处）

| 类型 | 判据 | 常见错误 |
|---|---|---|
| 授权公告号 `grant` | 国家码 + 7~8 位号码 + 授权类种类码（B1/B2/U/S/E） | 把申请公开号当授权号引用 |
| 申请公开号 `application_publication` | 国家码 + 4 位年份 + 7 位序号 + A1/A2 | 声称"已授权"却引用 A1 文本 |
| 申请号 `application` | 含斜杠的序列号（16/123,456） | 把申请号当公开号引用 |
| 优先权号 `priority` | 首次申请国家码 + 申请号 + 优先权日 | 把优先权日当申请日 |

- 归一动作：先 NFKC 全角→半角，再去空格与千分位、抽国家码、抽种类码、抽纯数字；输出 `canonical`（如 `US10123456B2`）。
- 号码位数不足或缺种类码 → `number_kind=unknown`，输出 `alternatives[]` 并把 `ambiguous=true`，**不猜**。
- WO 号 OCR 合并（丢斜杠丢空格，如 `WO2018123456A1`）：canonical 照常输出以便跨写法命中同一记录，
  但标 `wo_merged=true + ambiguous=true` 并给「年份/序号」拆解候选（`wo_split`），拆解必须回 WIPO/受理局确认，不把单一拆解写成确定结论。
- 公开号 / 申请号 / 优先权号分别成字段，任何结论都要写清引用的是哪一个。

## 2. 检索式

- 号码型：`PN=(US10123456B2)` / `AN=(US16123456)` / `PR=(US16123456)`。
- 主题型：`(IPC=(H01M10/0562) OR CPC=(...)) AND (TI=(solid electrolyte) OR AB=(...) OR CLM=(...))`。
- 申请人型：`PA=(assignee) AND (TI=(kw) OR AB=(kw))`。
- 每条检索式落 `search_expressions[]`，含 `query_id`、`type`、`expression`、`matched_record_ids`。
  关键词与分类号命**分开计分**（`score_breakdown.keyword_match` / `ipc_match`），避免只靠关键词把不同主题混成一族。

## 3. 专利族

- 第一判据：优先权号归一后相同。
- 辅助判据：申请人相同 + 标题强归一 Jaccard ≥ `title_jaccard_min` + 优先权日差 ≤ `family_year_window`。
- 每个族给 `family_id`、`basis`、`members[]`，并**固定写** `scope="observed_not_exhaustive"`：
  本轮只声明"观察到的同族关系"，不声称穷尽 INPADOC 简单族/扩展族，也不声称等同权项范围。
- 同族不同成员的权项版本可能不同（A1 公开权项 vs B2 授权权项），权项引用必须带 `version`。

## 4. 法律状态（硬纪律）

- 默认值 `unverified`；只有来源分级为 `official_registry` **且**带 `queried_at` 时才允许写确定值。
- 二手聚合页只登记为 `observed`（附来源与日期），输出值仍是 `unverified`，并给 `recheck_path`。
- 任何"该专利有效/已失效"的断言若来源非官方登记 → 记 `R_OVERSCOPED_CLAIM` 并计入风险分级 `high`。
- 状态字段固定带：`value`、`verified`、`basis`、`source`、`queried_at`、`recheck_path`。

## 5. 权利要求

- 每个权项给 `claim_no`、`version`、`source_ref`（`inline_field` / `facsimile:<file>` 带 `page:N` / `ocr_sidecar:<file>`）、
  `quote`（必须是其来源文本内 `char_span` 指向的**精确子串**，无省略号、无空白归一）、`char_span`、`ocr_boundary`。
- 权项优先来自记录内联字段；缺内联时解析挂接的影印件/侧车文本（只认「行首 N. 」从 1 连续递增锚点）；
  内联权项在影印件文本同文出现时记 `facsimile_corroborated`。
- 记录标 `ocr=true` 或来源为 `.ocr.txt` 侧车时，权项引用必须标 `ocr_boundary=true`，并在缺口里登记"OCR 边界，可能残句"。
- 定位失败 → `R_CLAIM_NOT_LOCATED`，不摘引、不改写；专利类记录完全无权项来源 → `R_CLAIM_SOURCE_UNAVAILABLE`
  （技术文献本无权利要求，不算缺口）。
- 主题型/申请人型查询另对材料正文做关键词命中 + 原文短引（`evidence_map.literature_hits`，quote 同样为精确切片），
  与 records 打分分开呈现。

## 6. 来源与授权边界

- 每条结论挂来源四元组：`db` + `queried_at` + `source_tier`（+ 可选 `version`/`license`/`access_note`）。
- 受限商业库（`display_policy.restricted_sources`）只登记元数据与访问条件，不抓正文、不外显权项原文。

## 7. 风险分级（只描述证据强度）

`high`：越权断言、号码类型混用；`medium`：族不穷尽 / OCR 边界 / 关键字段缺失；`low`：字段齐备且来源为官方登记。
风险分级**不构成** FTO 或侵权结论。
