# `_shared/` —— 77 个 Skill 的统一工程标准

本目录不属于任何一道题，是 77 个 Skill 共用的**运行时内核 + 校验器 + 工程规范**。
每个题目录（`answer/NNN_*/`）按本文档把 skill 做出来；`kit.py` / `validate.py` 需要**整文件复制**进各自 `scripts/`，保证每题可独立打包提交。

---

## 0. 目录即提交根

```
answer/NNN_题目名/            ← 复制整个目录即 /app/submission/
├── SKILL.md                  ← 必需：合法 YAML frontmatter（name / description）
├── scripts/
│   ├── kit.py                ← 复制自 _shared/kit.py（勿改接口）
│   ├── pipeline.py           ← 本题主流程（读 case.json → 写 result.json）
│   └── validate.py           ← 复制自 _shared/validate.py
├── rules/                    ← 本题规则库（判定标准、reason code、评分映射、schema）
├── examples/min_example/
│   ├── input/case.json       ← 示例输入（必须标注 EXAMPLE，不是正式测试案例）
│   └── expected/result.json  ← 示例输出（跑 pipeline 生成，作为回归基线）
├── README.md                 ← 开源起点调研（已有，不要动内容结构）
├── GITHUB_URL.txt            ← 提交前填写真实公开链接；未发布前保留为交付缺口，不伪造 URL
└── sources_github.json / sources_skillhub.json   ← 调研原始数据（已有）
```

资源红线（赛制硬约束）：**≤50 个 UTF-8 文本文件 / 单文件 ≤500 KB / 合计 ≤2 MB / 禁止软链接**。

---

## 1. `SKILL.md` 规范

```markdown
---
name: literature-search-planner
description: 把科研问题拆成可执行的检索计划，在给定候选池上做可复算的排序与覆盖/遗漏分析，输出可追溯候选集与证据记录。
---

# <中文标题>

## 何时使用
## 输入（/input/case.json + task.md）
## 流程（步骤 + 每步产物）
## 输出（result.json 字段：主结果 / 证据记录 / 状态说明）
## 异常处理（空结果 / 材料缺失 / 字段不符 / 超预算）
## 自查
## 最小调用示例（示例，非正式案例）
```

- `name`：小写字母/数字/连字符，`^[a-z][a-z0-9-]{1,63}$`，与题目一一对应。
- `description`：一句话说清「做什么 + 什么条件下触发」，≤300 字符，不写空话。
- 正文必须写清**调用方式**与**失败路径**，因为评估方是"加载 skill 的 agent"，不是人。

---

## 2. `scripts/pipeline.py` 规范

```python
#!/usr/bin/env python3
"""本题主流程：读 /input/case.json → 写 /app/output/result.json。"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kit import Result, Clock, load_case, parse_args, load_task_text   # 以及本题要用的工具

def run(case, task_text, args):
    res = Result(case, skill="<name>")
    res.log("load_case", case_id=res.case_id, contract=sorted(res.contract))
    # ... 本题逻辑，逐段 res.set("<contract 字段>", value) / res.evidence[...] = ...
    res.status["overall"] = "complete"      # 或 partial / empty / input_error
    return res

def main(argv=None):
    args = parse_args(argv)
    case = load_case(args.case)
    task = load_task_text(args, case)
    res = run(case, task, args)
    payload = res.build()
    if args.dry_run:
        print(json.dumps({"case_id": payload["case_id"], "status": payload["status"]["overall"]}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"]}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

硬规则：

1. **运行时为 Python 3.10+，默认只用标准库**。共享内核与部分脚本使用 `X | None` 类型标注，不能宣称兼容 Python 3.9。除非本题必须（如 pptx/xlsx），否则不引入第三方依赖；必须引入时，在 SKILL.md 写清包名、版本、安装方式与降级路径。
2. **不联网**。案例材料一律从 `case.json` / `task.md` / 工作区文件里读；需要"检索"时，把给定候选池当语料做确定性打分。
3. **不猜**。字段拿不到就 `res.unmet(...)`，绝不填空字符串或编造数值。
4. **可复算**。所有分数、阈值、公式写进 `rules/`，代码里不出现魔法数。
5. **幂等**。同一输入重复运行，输出逐字节一致（除 `generated_at`）。
6. **预算**。900 秒内可完成；大材料先摘要再判定，`Clock` 留 30 秒余量。
7. **契约驱动**。`output_contract` 的键必须全部出现在 result.json 顶层；`Result.build()` 会自动把缺失键填成 `not_produced` 并记 `status.unmet`，但**能用真实数据填的必须真填**。

---

## 3. 示例（`examples/min_example/`）

- `input/case.json`：字段外形照抄真实 `case.json`（`case_id` / `task_instruction` / `output_contract` / `source` / 题目材料），`case_id` 用 `EXAMPLE-*` 前缀。
- `expected/result.json`：由 pipeline 真实跑出来的结果，作为回归基线（**不是手写答案**）。
- 在 SKILL.md 的「最小调用示例」里写明运行命令，并注明「示例，非正式测试案例」。

回归命令（每题都一样）：

```bash
cd answer/NNN_题目名
python3 scripts/pipeline.py --case examples/min_example/input/case.json --out /tmp/NNN_out
python3 scripts/validate.py --case examples/min_example/input/case.json --result /tmp/NNN_out/result.json
```

### 3.1 Office / 图表产物（`office_min.py`）

题面要求"可编辑 Office 产物 + 真实渲染预览"时，把 `_shared/office_min.py` 一并复制进 `scripts/`：

| API | 产物 |
|---|---|
| `write_pptx(path, slides)` | 最小可用 `.pptx`（标题 + 要点 + 备注），可被 PowerPoint/LibreOffice 打开编辑 |
| `write_docx(path, blocks)` | 最小可用 `.docx`（标题层级 / 段落 / 项目符号 / 表格） |
| `write_xlsx(path, sheets)` | 最小可用 `.xlsx`（多表 + 数字/文本） |
| `read_pptx_text / read_docx_text` | 不依赖第三方库读回文本做自检 |

已用真实 `python-pptx / python-docx / openpyxl` 回读验证（2 页、3 段、表格、数值均正确）。
若环境已装官方库，优先走官方库并在 SKILL.md 写明"官方库 → office_min 降级"两条路径。

---

## 4. `kit.py` 速查

| API | 用途 |
|---|---|
| `parse_args()` | `--case/--work/--out/--task/--budget/--dry-run/--self-check` |
| `load_case(path)` | 读 case.json（容忍 `{"case": {...}}` 包装），缺文件抛 `ContractError` |
| `contract_of(case)` / `case_id_of(case)` | 取 `output_contract` / `case_id` |
| `load_task_text(args, case)` | 读 task.md（`--task` → case 同目录 `task.md`） |
| `Result(case, skill=...)` | 结果容器：`set / log / done / unmet / note / unsure / evidence / status / emit` |
| `Result.build()` | 补齐缺失 contract 字段（`not_produced`）→ 组装三段式 JSON |
| `tokens / norm / norm_title / jaccard` | 中英文分词、归一、重合度；BM25 文档侧用 `tokens(text, unique=False)` 保留词频 |
| `bm25_score(q, d, corpus)` | 可复算的相关性打分；查询词去重，文档及 corpus 保留重复 token |
| `snippet(text, s, e)` | 生成带省略号的引文片段（证据定位必须逐字可核） |
| `stable_id / sha256_text / rounded / clamp` | 稳定 ID、哈希、数值处理 |
| `Clock(budget)` | 预算守卫：`remaining() / out_of_budget()` |
| `deep_get / first_present / as_list / flatten_text` | 容错读字段（题面字段名不统一） |
| `load_json_reference` | 读取内联 JSON 或 case 引用的实际工单文件 |
| `read_table_snapshot / sqlite_table_names` | 内联表、CSV/TSV/JSON 与只读 SQLite 的真实数据适配，保留来源路径 |

### 4.1 共享内核回归与状态一致性

```bash
cd answer/_shared
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v test_shared.py
cd ../..
python3 answer/_shared/probe.py
```

`probe.py` 额外运行空材料与 `{"case": ...}` 包装输入：前者必须诚实降级，后者必须遵循 `load_case` 的兼容约定。它补足“只跑一个精心构造的 happy path”的盲区。

- `Result.build()` 是状态的最后守门：只要 `status.unmet` 非空，最终状态就不得为 `complete`，避免“先记缺口、后覆盖状态”的赋值顺序错误。
- 契约启发式映射里的“页 / 图 / 表”等单字只做精确匹配；复杂字段必须靠同名或显式别名绑定，防止静默错绑。
- `validate.py` 支持包装后的 case、`outputContract` 与列表契约，并检查 `complete/unmet/not_produced` 的交叉一致性。`empty/input_error` 可以保留空主结果，但必须在 `unmet` 解释；其他状态仍要求契约主结果非空。
- 修改 `_shared/kit.py` 或 `_shared/validate.py` 后，必须同步全部 77 个副本并重跑全量验收；`verify.py` 会阻止副本漂移。

---

## 5. 完成定义（DoD）

一题算完成，必须同时满足：

1. `SKILL.md` frontmatter 合法，正文六段齐全（何时使用 / 输入 / 流程 / 输出 / 异常处理 / 自查 + 示例）。
2. `scripts/pipeline.py --case examples/min_example/input/case.json --out <tmp>` 退出码 0，生成 `result.json`。
3. `scripts/validate.py` 输出 `PASS`。
4. `examples/min_example/expected/result.json` 与该次运行结果一致（重新生成后 diff 为空）。
5. 目录内文本文件 ≤50 个、单文件 ≤500 KB、合计 ≤2 MB、无软链接。
6. `python3 _shared/verify.py --only NNN` 通过。

## 6. 禁止事项

- 编造文献 / DOI / 数值 / 实验结论 / benchmark 分数 / 软件状态。
- 把"没找到"写成"不存在"；拿自己检索到的结果当覆盖率分母。
- 在 skill 里写死某个具体案例的答案（示例只能标注为示例）。
- 依赖作者本机路径、私有 API key、联网接口作为唯一路径。
- 把正式案例材料塞进仓库。
