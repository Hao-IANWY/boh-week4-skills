# 玻尔产品周赛·第四期 —— 科研 Agent Skills（5 题）

本仓库是 2026-11 「玻尔产品周赛·第四期」5 道赛题的参赛 Skill 本体与全部可复现资源。每个题目录即一份完整提交包（`/app/submission` 布局），可独立运行、独立验收。

| 目录 | 赛题 | 赛道 | Skill name | 一句话 |
|---|---|---|---|---|
| [`011_专利与技术文献检索`](011_专利与技术文献检索) | challenge-ad3e9357 | 文献检索 | `patent-literature-retrieval` | 号码规范化（含 WO 合并拆解）、族归并、权项来源定位（PDF 传真件/OCR 侧车 + 字符区间）、法律状态诚实 `unverified` |
| [`047_阅读批注与知识沉淀`](047_阅读批注与知识沉淀) | challenge-c050aaed | 资料管理 | `annotation-distiller` | 9 文件库扫描（SHA-256/别名/近重复/版本组）、六态批注锚点校验、BM25 检索回链、恢复操作只建议不动原件 |
| [`048_深度研究 Agent`](048_深度研究%20Agent) | agent-f8fb8bda | 深度研究与文献综述 | `deep-research-agent` | sources/records 双结构、前瞻协议先落盘后筛选、逐字证据表（char_span 校验）、方向词典冲突双列、研究空白只来自检索缺口 |
| [`056_论文逻辑与论证审查`](056_论文逻辑与论证审查) | challenge-16d4414d | 学术审查 | `argument-auditor` | 完整论文全文审查：章节切分 + 句级定位、facets 动态裁剪、13 分型 detector + 规则化 severity、假设/转述/对照零误报（suppressed 可查） |
| [`060_科研数据可视化`](060_科研数据可视化) | challenge-9562cc8d | 科研数据分析 | `research-figure-builder` | R/ggplot2 统计绘图链：行级血缘、按数据与目的选图、PDF/600dpi PNG/SVG 三格式、独立数值复核与全链哈希复现 |

[`_shared/`](_shared) 是跨题工程标准层（契约容器、BM25、验收门 `verify.py`）；每题目录内的 `scripts/kit.py`、`validate.py` 是其逐字节一致副本。

## 快速开始

依赖：Python ≥ 3.10（纯标准库）；060 另需 R + ggplot2（缺依赖时诚实降级为 partial，不伪装成功）。

```bash
# 单题最小示例（回归基线，非正式案例）
cd 011_专利与技术文献检索
python3 -B scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/demo
python3 -B scripts/validate.py --case examples/min_example/input/case.json --result /tmp/demo/result.json

# 多案例回归（含真实画像仿真案例）
python3 -B scripts/test_pipeline.py

# 全部验收门（结构 / 资源预算 ≤50 文件·500KB·2MB / 可运行 / 契约 / 幂等）
python3 _shared/verify.py --only 011 047 048 056 060
```

## 设计约定（全部题目通用）

- **输入**：`/input/case.json` + `task.md`；材料支持内联数据、侧车文本（`*.txt` / `*.ocr.txt`）、PDF（自研标准库提取）、DOCX（zip+xml）与只读 SQLite。
- **输出**：`/app/output/result.json` 保留 `case_id` 与原 `output_contract` 全部字段，附 `evidence`（可回指的证据链）与 `status`/`uncertainty`（诚实的缺口说明）。
- **阈值与判定全部外置**到各题 `rules/*.json`，评分口径可复算。
- **诚实降级**：缺依赖、无文本层、提取失败一律如实标缺口（`partial` / `empty` + reason code），不补造数据、不虚报完成度。
- **幂等**：同一输入两次运行除 `generated_at` 外逐字节一致（verify 门强制）。

## 验证记录

每题的「真实案例画像 → 缺口 → 修复 → 运行证据 → 残留风险」全过程见各题 `README.md` 与回归测试 `scripts/test_pipeline.py`；提交前均通过 `_shared/verify.py` 验收门与 6–16 项多案例回归。

## 方法来源与许可

各题 `README.md` 内注明借鉴的公开项目与改动点（如 060 复用 [Rimagination/easyplot](https://github.com/Rimagination/easyplot) d150e01 的元数据/色板审计，MIT 原文随附于其 `scripts/easyplot-LICENSE.txt`；011 骨架借鉴 `Obviously-Not/patent-skills`（MIT）的子技能划分并改写为四段留痕）。全部脚本为本仓库原创或注明出处的改编，无未授权数据。
