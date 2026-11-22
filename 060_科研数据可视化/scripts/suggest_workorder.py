#!/usr/bin/env python3
"""自然语言任务 → 结构化出图工单（确定性建议器）。

用途：案例只有 task_instruction 而缺 figure_requests 时，由本脚本从数据字典与任务
文本生成一份可复核的工单建议，并写出可直接喂给 pipeline.py 的工作案例（保留原
case_id、数据引用与 output_contract）。生成的是"建议"，Agent 应复核后微调；
所有假设（默认误差线口径、自动选列等）逐条记录在 work_case["workorder_assumptions"]。

用法：
  python3 -B scripts/suggest_workorder.py --case /input/case.json --out /app/work/case.json
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path


def load_case(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("case"), dict):
        data = data["case"]
    return data


def dataset_table(case: dict, case_dir: Path) -> tuple[list[str], list[dict], str]:
    """读取内联 rows 或外部 CSV/TSV/JSON/SQLite 的列与行（与 pipeline 同源规则）。"""
    dataset = case.get("dataset") or case.get("data") or {}
    if isinstance(dataset, dict) and not any(k in dataset for k in ("path", "file", "csv_path", "sqlite_path", "database_path")):
        columns = [str(c) for c in (dataset.get("columns") or [])]
        rows = dataset.get("rows") or []
        if not columns and rows:
            columns = [str(k) for k in rows[0]]
        return columns, rows, "inline"
    ref = None
    if isinstance(dataset, str):
        ref = dataset
    else:
        ref = next((dataset.get(k) for k in ("path", "file", "csv_path", "sqlite_path", "database_path") if dataset.get(k)), None)
    if not ref:
        return [], [], "missing"
    path = Path(ref)
    if not path.is_absolute():
        path = case_dir / path
    if not path.is_file():
        ref_path = Path(ref)
        candidates = [case_dir / ref_path]
        for base in [*case_dir.parents, Path.cwd(), Path("/input"), Path("/app")]:
            candidates.append(base / "input" / ref_path)   # 保留相对结构：/input/iris/iris.sqlite
            candidates.append(base / "input" / ref_path.name)  # 只按文件名：/input/iris.sqlite
            candidates.append(base / ref_path)
        for candidate in candidates:
            if candidate.is_file():
                return read_file(candidate)
        # 文件名递归搜索兜底（限深 4）：原案例与数据同目录平铺引用、工作案例被迁移时命中
        name = ref_path.name
        for base in [*case_dir.parents, Path.cwd(), Path("/input"), Path("/app")]:
            search_root = base / "input" if (base / "input").is_dir() else base
            try:
                if search_root.is_dir():
                    for hit in sorted(search_root.rglob(name)):
                        if hit.is_file() and len(hit.relative_to(search_root).parts) <= 4:
                            return read_file(hit)
            except OSError:
                continue
        return [], [], "unresolved"
    return read_file(path)


def read_file(path: Path):
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t" if suffix == ".tsv" else ",")
            rows = list(reader)
            return list(reader.fieldnames or []), rows, str(path)
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            columns = [str(c) for c in (data.get("columns") or (list(data["rows"][0]) if data["rows"] else []))]
            return columns, data["rows"], str(path)
    if suffix in (".sqlite", ".db", ".sqlite3"):
        import sqlite3
        con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        try:
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            if tables:
                cur = con.execute(f'SELECT * FROM "{tables[0]}" LIMIT 500')
                columns = [d[0] for d in cur.description]
                return columns, [dict(zip(columns, r)) for r in cur.fetchall()], f"{path}#{tables[0]}"
        finally:
            con.close()
    return [], [], str(path)


def is_number(value) -> bool:
    try:
        return math.isfinite(float(str(value).strip()))
    except (TypeError, ValueError):
        return False


import math  # noqa: E402  (is_number 使用)


def infer_types(columns, rows, dictionary) -> dict:
    """列类型：字典声明优先，否则按值推断。返回 {column: {type,label,unit}}。"""
    info = {}
    for col in columns:
        spec = dictionary.get(col) if isinstance(dictionary.get(col), dict) else {}
        decl = str(spec.get("type") or "").lower()
        entry = {"label": str(spec.get("label") or col), "unit": str(spec.get("unit") or ""),
                 "allowed_values": spec.get("allowed_values")}
        if decl in ("continuous", "categorical", "temporal", "key", "ordinal"):
            entry["type"] = decl
        elif decl:
            entry["type"] = decl
        else:
            values = [r.get(col) for r in rows if r.get(col) not in (None, "")]
            numeric_ratio = (sum(is_number(v) for v in values) / len(values)) if values else 0
            uniq = len({str(v) for v in values})
            date_like = bool(values) and all(re.match(r"^\d{4}-\d{2}-\d{2}", str(v)) for v in values[:20])
            if date_like:
                entry["type"] = "temporal"
            elif numeric_ratio >= 0.8:
                entry["type"] = "continuous"
            elif values and uniq <= max(12, len(values) // 5):
                entry["type"] = "categorical"
            else:
                entry["type"] = "key"
        info[col] = entry
    return info


def resolve_columns(text: str, info: dict) -> dict:
    """从任务文本里找到被点名的列（按列名或字典标签匹配）。返回 {col: mention}。"""
    found = {}
    for col, entry in info.items():
        for alias in {col, entry.get("label") or col}:
            if alias and alias in text:
                found[col] = alias
                break
    return found


KEYWORDS = [
    ("comparison", ["比较", "差异", "对比", "对比度", "高于", "低于", "comparison", "compare", "versus", "vs", "difference"]),
    ("correlation", ["相关", "关联", "关系", "相关结构", "correlation", "correlate", "association", "relationship"]),
    ("trend", ["趋势", "随时间", "时间变化", "随日期", "逐日", "纵向", "trend", "over time", "longitudinal"]),
    ("distribution", ["分布", "直方", "频数", "distribution", "histogram", "spread"]),
    ("composition", ["构成", "占比", "组成", "composition", "proportion", "share"]),
]
GROUP_WORDS = ["按", "分组", "分色", "各", "不同"]


def suggest(text: str, info: dict, columns, rows) -> tuple[list[dict], list[str]]:
    purposes = [p for p, words in KEYWORDS if any(w in text.lower() for w in words)]
    assumptions: list[str] = []
    mentioned = resolve_columns(text, info)
    continuous = [c for c, e in info.items() if e["type"] == "continuous"]
    categorical = [c for c, e in info.items() if e["type"] == "categorical"]
    temporal = [c for c, e in info.items() if e["type"] == "temporal"]
    figures: list[dict] = []
    fid = 0

    def pick(kind, prefer_mentioned=True):
        pool = {"continuous": continuous, "categorical": categorical, "temporal": temporal}[kind]
        named = [c for c in mentioned if c in pool]
        if prefer_mentioned and named:
            return named[0]
        return pool[0] if pool else None

    def error_bar_for(sentence):
        if re.search(r"标准误|SE", sentence, re.I):
            return "se"
        if re.search(r"置信区间|95%|CI", sentence, re.I):
            return "ci95"
        if re.search(r"标准差|SD", sentence, re.I):
            return "sd"
        assumptions.append("任务未声明误差线口径：默认 SD（均值柱 ±1 SD），如需 SE/CI 请在工单修改")
        return "sd"

    def group_col(sentence):
        named_cat = [c for c in mentioned if c in categorical]
        if re.search(r"按.{1,8}(分色|分组|着色)", sentence) and named_cat:
            return named_cat[0]
        if any(w in sentence for w in GROUP_WORDS) and named_cat:
            return named_cat[0]
        return None

    for purpose in purposes:
        sentence = text
        if purpose == "comparison" and categorical and continuous:
            fid += 1
            figures.append({
                "figure_id": f"F{fid}", "purpose": "comparison",
                "chart_hint": "bar", "x": pick("categorical"), "y": pick("continuous"),
                "error_bar": error_bar_for(sentence),
                "x_label": info[pick("categorical")]["label"],
                "y_label": info[pick("continuous")]["label"],
                "unit": info[pick("continuous")]["unit"] or "",
                "title": f"{info[pick('continuous')]['label']}按{info[pick('categorical')]['label']}比较",
            })
            assumptions.append(f"F{fid}: 比较类任务路由为均值柱+原始点；组数>8 将自动转横向布局")
        elif purpose == "correlation":
            named_cont = [c for c in mentioned if c in continuous]
            fid += 1
            if len(named_cont) >= 2:
                figures.append({
                    "figure_id": f"F{fid}", "purpose": "correlation", "chart_hint": "scatter",
                    "x": named_cont[0], "y": named_cont[1],
                    "group": group_col(sentence) or "",
                    "fit_group": bool(group_col(sentence)),
                    "x_label": info[named_cont[0]]["label"], "y_label": info[named_cont[1]]["label"],
                    "title": f"{info[named_cont[0]]['label']}与{info[named_cont[1]]['label']}的关系",
                })
            elif len(continuous) >= 3:
                figures.append({
                    "figure_id": f"F{fid}", "purpose": "correlation", "chart_hint": "heatmap",
                    "title": "全部连续变量的 Pearson 相关矩阵",
                })
                assumptions.append(f"F{fid}: 任务未点名变量，热图使用全部 {len(continuous)} 个连续列；如需子集请修改 variables")
            elif len(continuous) == 2:
                figures.append({
                    "figure_id": f"F{fid}", "purpose": "correlation", "chart_hint": "scatter",
                    "x": continuous[0], "y": continuous[1],
                    "x_label": info[continuous[0]]["label"], "y_label": info[continuous[1]]["label"],
                    "title": f"{info[continuous[0]]['label']}与{info[continuous[1]]['label']}的关系",
                })
            else:
                assumptions.append("correlation 意图被识别但连续列不足 2 个，未生成图")
        elif purpose == "trend" and (temporal or continuous) and continuous:
            fid += 1
            x_col = temporal[0] if temporal else pick("continuous")
            figures.append({
                "figure_id": f"F{fid}", "purpose": "trend", "chart_hint": "line",
                "x": x_col, "y": pick("continuous"), "group": group_col(sentence) or "",
                "x_label": info[x_col]["label"], "y_label": info[pick("continuous")]["label"],
                "title": f"{info[pick('continuous')]['label']}随{x_info(x_col, info)}变化",
            })
            assumptions.append(f"F{fid}: 趋势类任务路由为均值折线+原始点；拒绝并列柱（有序时间应保留斜率）")
        elif purpose == "distribution" and continuous:
            fid += 1
            figures.append({
                "figure_id": f"F{fid}", "purpose": "distribution",
                "x": pick("continuous"), "x_label": info[pick("continuous")]["label"],
                "title": f"{info[pick('continuous')]['label']}的分布",
            })
            assumptions.append(f"F{fid}: 分布任务路由为直方图（Sturges 分箱）；拒绝饼图")
        elif purpose == "composition" and categorical:
            fid += 1
            figures.append({
                "figure_id": f"F{fid}", "purpose": "composition", "chart_hint": "count",
                "x": pick("categorical"), "x_label": info[pick("categorical")]["label"],
                "title": f"{info[pick('categorical')]['label']}构成",
            })
    # 独立意图：拟合/置信带/分色展示 → 散点+分组拟合（与热图不互斥，各自回应任务的一条提问）
    if re.search(r"拟合|置信带|分色|着色|散点|回归", text) and len(continuous) >= 2:
        named_cont = [c for c in mentioned if c in continuous]
        x_col, y_col = (named_cont[0], named_cont[1]) if len(named_cont) >= 2 else (continuous[0], continuous[-1])
        group = group_col(text) or (categorical[0] if categorical else "")
        fid += 1
        figures.append({
            "figure_id": f"F{fid}", "purpose": "correlation", "chart_hint": "scatter",
            "x": x_col, "y": y_col, "group": group, "fit_group": bool(group),
            "x_label": info[x_col]["label"], "y_label": info[y_col]["label"],
            "title": f"{info[x_col]['label']}与{info[y_col]['label']}的关系"
                     + (f"（按{info[group]['label']}）" if group else ""),
        })
        assumptions.append(f"F{fid}: 拟合/分色意图 → 散点+分组OLS与95%置信带；拒绝折线（无序观测不连线）")
    return figures, assumptions


def x_info(col, info):
    return info[col]["label"] if col in info else (col or "时间")


def resolve_dataset_path(case: dict, case_dir: Path, work_dir: Path) -> dict:
    """把数据引用改写为从工作案例目录可解析的路径（优先绝对路径，避免迁移断链）。"""
    fixed = dict(case)
    dataset = case.get("dataset")
    if isinstance(dataset, dict) and dataset.get("path"):
        p = Path(str(dataset["path"]))
        if not p.is_absolute():
            p = case_dir / p
        if p.is_file():
            dataset = dict(dataset)
            dataset["path"] = str(p.resolve())
            fixed["dataset"] = dataset
    fixed.pop("figure_requests", None)
    return fixed


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    case_path = Path(args[args.index("--case") + 1]) if "--case" in args else Path("case.json")
    out_path = Path(args[args.index("--out") + 1]) if "--out" in args else Path("work_case.json")
    case = load_case(case_path)
    case_dir = case_path.resolve().parent
    text = str(case.get("task_instruction") or case.get("task") or "")
    dictionary = case.get("data_dictionary") or {}
    columns, rows, source = dataset_table(case, case_dir)
    if not columns:
        print(json.dumps({"ok": False, "error": f"无法读取数据表（source={source}）"}, ensure_ascii=False))
        return 1
    info = infer_types(columns, rows, dictionary)
    figures, assumptions = suggest(text, info, columns, rows)
    work = resolve_dataset_path(case, case_dir, out_path.parent)
    work["figure_requests"] = figures
    work["workorder_assumptions"] = assumptions
    work["workorder_source"] = {
        "generator": "scripts/suggest_workorder.py (deterministic; review before reuse)",
        "task_text_chars": len(text), "data_source": source, "columns_inferred": info,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(work, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "work_case": str(out_path), "figures": len(figures),
                      "purposes": [f["purpose"] for f in figures],
                      "assumptions": assumptions}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
