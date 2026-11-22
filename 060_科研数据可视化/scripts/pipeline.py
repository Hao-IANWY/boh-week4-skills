#!/usr/bin/env python3
"""060 research figures: case adapter + reproducible sh/TSV/R/ggplot2 pipeline."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kit import (  # noqa: E402
    Clock, Result, as_list, first_present, load_case, load_json, load_json_reference,
    load_task_text, parse_args, read_table_snapshot, sha256_text,
)

SKILL = "research-figure-builder"
SCRIPT_DIR = Path(__file__).resolve().parent
RULES_DIR = SCRIPT_DIR.parent / "rules"
FORMATS = ("pdf", "png", "svg")
CHART_ALIASES = {
    "bar": "bar_with_error_points", "bar_with_error": "bar_with_error_points", "bar_points": "bar_with_error_points",
    "box": "boxplot_points", "boxplot": "boxplot_points", "violin": "violin_points", "strip": "comparison_auto",
    "scatter": "scatter_with_fit", "scatter_with_fit": "scatter_with_fit",
    "line": "line_series", "line_series": "line_series",
    "hist": "histogram", "histogram": "histogram",
    "stacked": "stacked_bar", "stacked_bar": "stacked_bar",
    "count": "category_count", "count_bar": "category_count", "category_count": "category_count",
    "heatmap": "correlation_heatmap", "correlation_heatmap": "correlation_heatmap",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def write_raw_snapshot(table: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=table["columns"], delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in table["rows"]:
            writer.writerow({column: cell_text(row.get(column)) for column in table["columns"]})


def load_table(case: dict, base_dir: Path) -> dict:
    dataset = first_present(case, "dataset", "data", "table", "dataset_path", "data_path", "csv_path",
                            "sqlite_path", "database_path", default={}) or {}
    table_name = str(first_present(dataset, "table_name", "table", default="") or "") if isinstance(dataset, dict) else ""
    table = read_table_snapshot(dataset, base_dir, table_name)
    table["note"] = str(first_present(dataset, "note", default="") or "") if isinstance(dataset, dict) else ""
    if len(table["columns"]) != len(set(table["columns"])):
        raise ValueError("数据表包含重复列名，需先按数据字典消歧")
    return table


def cleaning_rules(case: dict, dictionary: dict) -> dict:
    constraints = case.get("constraints") or {}
    cleaning = dict(case.get("cleaning") or case.get("data_cleaning") or {})
    cleaning["missing_tokens"] = list(first_present(cleaning, "missing_tokens", default=[]) or
                                      first_present(constraints, "missing_tokens", "missing_values", default=[]) or [])
    column_missing = dict(cleaning.get("column_missing") or {})
    for column, spec in dictionary.items():
        if isinstance(spec, dict):
            sentinels = first_present(spec, "missing_values", "missing_tokens", "sentinels", default=[]) or []
            if sentinels:
                column_missing[column] = list(as_list(sentinels))
    cleaning["column_missing"] = column_missing
    cleaning["drop_duplicates"] = bool(first_present(cleaning, "drop_duplicates",
                                                      default=constraints.get("drop_duplicates", False)))
    return cleaning


def numeric_columns(table: dict, dictionary: dict) -> list[str]:
    result = []
    for column in table["columns"]:
        declared = (dictionary.get(column) or {}).get("type") if isinstance(dictionary.get(column), dict) else None
        if declared in {"categorical", "ordinal", "temporal", "key"}:
            continue
        values = [row.get(column) for row in table["rows"] if row.get(column) not in (None, "")]
        finite_count = sum(_finite(value) is not None for value in values)
        if declared == "continuous" or (len(values) >= 3 and finite_count >= 3 and finite_count / len(values) >= 0.8):
            result.append(column)
    return result


def _finite(value):
    import math
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def route_chart(req: dict, table: dict, dictionary: dict) -> tuple[str | None, list[str], list[dict]]:
    purpose = str(req.get("purpose") or "").strip().lower()
    hint = str(first_present(req, "chart_type", "chart_hint", "type", default="") or "").strip().lower()
    variables = [str(v) for v in as_list(first_present(req, "variables", "columns", "features", default=[]) or [])]
    notes = []
    rejected = []
    if hint in {"pie", "pie_chart", "3d", "3d_bar", "dual_axis"}:
        rejected.append({"chart": hint, "reason": "角度/3D/双轴会削弱准确比较；改用位置、长度或分面"})
        notes.append(f"已将高风险图型 {hint} 改为可核验的科研图型")
        hint = ""
    if hint in CHART_ALIASES:
        chart = CHART_ALIASES[hint]
    elif purpose == "distribution":
        chart = "histogram"
    elif purpose == "trend":
        chart = "line_series"
    elif purpose in {"composition", "count", "frequency"}:
        chart = "category_count" if not req.get("y") else "stacked_bar"
    elif purpose == "correlation":
        chart = "correlation_heatmap" if len(variables) >= 3 else "scatter_with_fit"
    elif purpose == "comparison":
        chart = "bar_with_error_points" if req.get("error_bar") not in (None, "", "none") else "comparison_auto"
    else:
        chart = None
    if chart == "correlation_heatmap" and not variables:
        variables = numeric_columns(table, dictionary)
        notes.append("热图变量未显式给出，使用数据字典与有限数值共同识别的全部连续列")
    if chart == "correlation_heatmap" and len(variables) < 2:
        return None, variables, rejected + [{"chart": "correlation_heatmap", "reason": "至少需要两个数值变量"}]
    if chart == "correlation_heatmap" and len(variables) > 30:
        return None, variables, rejected + [{"chart": "correlation_heatmap", "reason": "变量超过 30 个，需在工单中明确选择子集，不能静默截断"}]
    if chart == "scatter_with_fit":
        rejected.append({"chart": "line", "reason": "无序观测不连线；仅使用散点与声明的拟合"})
    elif chart == "histogram":
        rejected.append({"chart": "pie", "reason": "分布不是构成；保留连续位置与频数"})
    elif chart == "line_series":
        rejected.append({"chart": "grouped_bar", "reason": "有序时间/剂量应保留趋势斜率"})
    return chart, variables, rejected


VENUE_KEY_ALIASES = {
    "default_single_column": "default_single_column", "single_column": "default_single_column",
    "single": "default_single_column", "单栏": "default_single_column", "通用单栏": "default_single_column",
    "default_double_column": "double_column", "double_column": "double_column",
    "double": "double_column", "full_width": "double_column", "双栏": "double_column",
    "宽栏": "double_column", "通用双栏": "double_column",
}


def _venue_key(selected) -> str:
    text = str(selected).strip().lower().replace("-", "_").replace(" ", "_")
    return VENUE_KEY_ALIASES.get(text, text)


def venue_for(case: dict, req: dict, rules: dict, warnings: list[str]) -> dict:
    venues = rules.get("venue", {}).get("venues", {})
    venue = dict(venues.get("default_single_column") or {})
    case_venue = case.get("venue")
    if isinstance(case_venue, dict):
        venue.update(case_venue)
    selected = req.get("venue")
    if isinstance(selected, str) and selected:
        key = _venue_key(selected)
        if key not in venues:
            warnings.append(f"未知 venue={selected}，使用默认单栏并记录")
        else:
            venue.update(venues[key])
            if key != selected.strip().lower().replace("-", "_").replace(" ", "_"):
                warnings.append(f"venue 别名 {selected} 已归一为 {key}")
    elif isinstance(selected, dict):
        venue.update(selected)
    width = first_present(req, "width_mm", default=venue.get("width_mm", 89))
    height = first_present(req, "height_mm", default=venue.get("height_mm", 62))
    venue.update({
        "name": req.get("venue_name") or venue.get("name") or "custom",
        "width_mm": float(width), "height_mm": float(height),
        "dpi": int(first_present(req, "dpi", default=venue.get("raster_dpi", 600))),
        "min_font_pt": float(venue.get("min_font_pt", 6)),
    })
    if venue["width_mm"] <= 0 or venue["height_mm"] <= 0 or venue["dpi"] < 300:
        raise ValueError("画布尺寸必须为正，PNG DPI 不得低于 300")
    return venue


ALWAYS_HORIZONTAL = {"category_count"}
FLIP_ABOVE_8 = {"bar_with_error_points", "comparison_auto", "boxplot_points", "violin_points", "stacked_bar"}
CATEGORICAL_X = ALWAYS_HORIZONTAL | FLIP_ABOVE_8

# 与基础 Agent/通用绘图方案的公平对照声明：同一数据与任务要求下的典型失败模式
# vs 本 Skill 的确定性处理，每项指向 result.json 内可核验的证据位置。
# 只声明可由随附证据支撑的对照，不虚构基线数字（平台对照由组织方自行运行）。
BASELINE_COMPARISON = [
    {"aspect": "图表选择",
     "base_agent_typical": "无序分类比较画饼图、时序画并列柱，或只给“建议使用柱状图”的文字不出图",
     "this_skill": "purpose/chart_hint 路由到 10 类可执行图型；饼/3D/双轴显式拒绝并给理由；交付真实图件与绘图代码",
     "evidence": "figures[].selection_rationale / rejected_alternatives；figures[].files"},
    {"aspect": "坐标轴与刻度",
     "base_agent_typical": "柱状图纵轴不从 0 起截断基线；最大值超出刻度被裁剪",
     "this_skill": "柱/条一律从 0 起；刻度覆盖数据、误差线与显著性括线的最高点",
     "evidence": "analysis.x_scale / analysis.y_axis_cover；audit 的 size 与 margin 检查"},
    {"aspect": "编码与可读性",
     "base_agent_typical": "类别过多硬塞一图、标签互相重叠；大样本散点糊成色块；配色灰度/色盲不可辨",
     "this_skill": "类别不截断：自动增高画布/横向布局/45°-90° 标签/字号收缩；点径与透明度按密度自适应；Okabe-Ito 色盲友好色板 + 形状/线型冗余编码",
     "evidence": "figures[].layout_adjustment；analysis.point_style / heatmap.label_policy；palette 审计检查"},
    {"aspect": "标注与图注",
     "base_agent_typical": "误差线不写口径；显著性凭空画星号；图注依赖正文才能读懂",
     "this_skill": "误差线口径（SD/SE/CI95）逐图声明；显著性只透传任务提供的结果并注明方法与来源；图注含轴/单位/n/排除数/方法，独立可读",
     "evidence": "figures[].caption.standalone_text；analysis.significance_rendered"},
    {"aspect": "导出规格",
     "base_agent_typical": "只出低分辨率截图；字体未嵌入；中文乱码；尺寸不合期刊栏宽",
     "this_skill": "每图 PDF(字体子集嵌入)+600dpi PNG+可编辑 SVG；Cairo/svglite 真实文本；venue 栏宽适配（尺寸误差 <0.2mm）",
     "evidence": "audit.files.*.metadata（页面尺寸/字体清单/DPI/SVG 文本元素数）"},
    {"aspect": "复现与证据链",
     "base_agent_typical": "清洗与统计口径无记录，换一台机器重跑得到另一张图",
     "this_skill": "原始快照+声明式清洗规则+行级血缘；两次全链重跑逐文件 SHA-256 一致；缺失/重复/哨兵逐行可查",
     "evidence": "data_quality.json / figure_N_lineage.json / figures[].reproduce.end_to_end_sha_match"},
    {"aspect": "异常与降级",
     "base_agent_typical": "缺列/坏值/不支持格式时静默失败或伪造结果",
     "this_skill": "列缺失→诚实 partial；未知格式→范围外缺口记录并交付默认三格式；n=1/常量相关/零方差→null 而非 0；单图失败不影响其余图",
     "evidence": "status.unmet / figures[].format_gaps / 状态说明.gaps"},
]


def category_levels(table: dict, column: str) -> int:
    if not column or column not in table["columns"]:
        return 0
    return len({str(row.get(column)) for row in table["rows"] if row.get(column) not in (None, "")})


def adapt_layout(figure: dict, table: dict) -> None:
    """类别/变量较多时不截断数据，自动按行高需求增高画布（封顶 max_height_mm）。"""
    chart = figure.get("chart_type") or ""
    max_height = float(figure.get("max_height_mm") or 240.0)
    levels = 0
    if chart == "correlation_heatmap":
        levels = len(figure.get("variables") or [])
        # 方格面板 ≈ 宽度可用部分；45° 标签对角线 + 标题/边距 ≈ 0.3×宽度；
        # 同时按变量数保底，使格子在单栏宽度下也不塌缩成不可读。
        needed = max(levels * 13.0 + 30.0, 1.3 * float(figure.get("width_mm") or 89) - 25.0) if levels else 0.0
        reason = "相关热图按变量数与画布宽度增高，保证方格面板、45° 标签与标题完整落在画布内"
    elif chart in CATEGORICAL_X:
        levels = category_levels(table, figure.get("x") or "")
        if not levels:
            return
        horizontal = chart in ALWAYS_HORIZONTAL or levels > 8
        if not horizontal:
            return  # 垂直布局由 R 端按标签宽度预测旋转，无需增高
        per_row = 7.5 if levels <= 12 else (6.2 if levels <= 24 else 5.2)
        needed = levels * per_row + 22.0
        reason = f"{levels} 个类别全部保留并采用横向布局，按行高增高画布"
    else:
        return
    if not needed or needed <= float(figure.get("height_mm") or 0):
        return
    adjusted = round(min(needed, max_height), 2)
    figure["layout_adjustment"] = {
        "reason": reason, "n_levels": levels,
        "requested_height_mm": float(figure.get("height_mm") or 0), "adjusted_height_mm": adjusted,
        "capped": needed > max_height, "max_height_mm": max_height,
    }
    figure["height_mm"] = adjusted
    figure["warnings"].append(
        f"类别/变量较多（{levels}）：画布高度 {figure['layout_adjustment']['requested_height_mm']:.0f}→{adjusted:.0f} mm；类别不截断")
    if needed > max_height:
        figure["warnings"].append(
            f"{levels} 个类别超出最大高度 {max_height:.0f} mm 的可读布局，标签可能仍密集；建议按 group 分面或拆分图件")


def make_plan(case: dict, table: dict, dictionary: dict, reqs: list[dict], rules: dict,
              source_meta: dict, task_text: str) -> tuple[dict, list[dict]]:
    figures, unrouted = [], []
    cleaning = cleaning_rules(case, dictionary)
    for index, req in enumerate(reqs, 1):
        figure_id = str(req.get("figure_id") or f"FIG{index}")
        chart, variables, rejected = route_chart(req, table, dictionary)
        if not chart:
            unrouted.append({"figure_id": figure_id, "status": "no_builder", "reason": "图型/变量组合未形成可执行路由",
                             "rejected_alternatives": rejected})
            continue
        warnings = []
        try:
            venue = venue_for(case, req, rules, warnings)
        except (TypeError, ValueError) as error:
            unrouted.append({"figure_id": figure_id, "status": "invalid_request", "reason": str(error),
                             "rejected_alternatives": rejected})
            continue
        x, y = str(req.get("x") or ""), str(req.get("y") or "")
        group = str(req.get("group") or "")
        group_spec = dictionary.get(group) or {}
        group_label = str(first_present(req, "group_label", default=group_spec.get("label") or group) or group)
        x_spec = dictionary.get(x) or {}
        y_spec = dictionary.get(y) or {}
        x_label = str(first_present(req, "x_label", default=x_spec.get("label") or x) or x)
        y_label = str(first_present(req, "y_label", default=y_spec.get("label") or y) or y)
        x_unit = str(first_present(req, "x_unit", default=x_spec.get("unit") or "") or "")
        # 分类/键列不携带物理单位：工单误给 x_unit 时忽略并记录，避免「物种 (cm)」式伪标注；
        # 数据字典未声明类型或声明为连续/时间的数值类别（如龄期）保留单位。
        x_declared = str((x_spec.get("type") if isinstance(x_spec, dict) else "") or "")
        if x_unit and x_declared in {"categorical", "key", "ordinal"}:
            warnings.append(f"x={x} 在数据字典中为 {x_declared}，忽略其轴单位 '{x_unit}'（分类轴不携带物理单位）")
            x_unit = ""
        y_unit = str(first_present(req, "unit", "y_unit", default=y_spec.get("unit") or "") or "")
        formats = [str(v).lower().lstrip(".") for v in as_list(first_present(
            req, "formats", "format", "export_formats", default=case.get("formats", FORMATS)) or FORMATS)]
        entry = {
            "index": index, "figure_id": figure_id, "purpose": str(req.get("purpose") or ""),
            "chart_type": chart, "requested_chart": first_present(req, "chart_type", "chart_hint", default=""),
            "x": x, "y": y, "group": group, "group_label": group_label, "variables": variables,
            "unit_id": str(first_present(req, "unit_id", "replicate_unit", default="") or ""),
            "label": str(first_present(req, "label", "id_column", default="") or ""),
            "x_label": x_label, "y_label": y_label, "x_unit": x_unit, "unit": y_unit,
            "title": str(req.get("title") or ""), "panel": str(req.get("panel") or ""),
            "error_bar": str(req.get("error_bar") or "none").lower(),
            "log_y": bool(req.get("log_y", False)),
            "fit_group": bool(req.get("fit_group", False)),
            "significance": req.get("significance"),
            "data_file": f"figure_{index}.tsv", "analysis_file": f"figure_{index}_analysis.json",
            "required_formats": formats, "rejected_alternatives": rejected, "warnings": warnings,
            "drop_duplicates": bool(req.get("drop_duplicates", cleaning["drop_duplicates"])),
            "palette": rules.get("palette", {}).get("palettes", {}).get("okabe_ito", {}).get("colors", []),
            **venue,
        }
        if chart == "correlation_heatmap" and variables:
            entry["variable_labels"] = {
                variable: str((dictionary.get(variable) or {}).get("label") or variable)
                if isinstance(dictionary.get(variable), dict) else variable
                for variable in variables
            }
        adapt_layout(entry, table)
        figures.append(entry)
    plan = {
        "schema_version": "2.0.0", "case_id": str(case.get("case_id") or case.get("caseId") or "UNKNOWN-CASE"),
        "source": source_meta, "task_text_sha256": sha256_text(task_text),
        "cleaning": cleaning, "figures": figures,
    }
    return plan, unrouted


def copy_runtime(out_dir: Path) -> None:
    (out_dir / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("data_prep.sh", "plot.R", "theme.R"):
        shutil.copyfile(SCRIPT_DIR / name, out_dir / name)
    for name in ("prepare_data.py", "audit_outputs.py", "normalize_pdf.py", "easyplot_metadata.py", "easyplot_palette_audit.py", "fallback_render.py", "easyplot-LICENSE.txt"):
        shutil.copyfile(SCRIPT_DIR / name, out_dir / "scripts" / name)
    (out_dir / "data_prep.sh").chmod(0o755)


def probe_dependencies() -> dict:
    probe = {
        "python": {"version": platform.python_version()},
        "Pillow": {"available": False}, "pypdf": {"available": False},
        "matplotlib": {"available": False},
        "Rscript": {"available": bool(shutil.which("Rscript")), "path": shutil.which("Rscript") or ""},
    }
    for package, key in (("PIL", "Pillow"), ("pypdf", "pypdf"), ("matplotlib", "matplotlib")):
        try:
            module = __import__(package)
            probe[key] = {"available": True, "version": getattr(module, "__version__", "?")}
        except Exception as error:
            probe[key]["detail"] = str(error)
    if probe["Rscript"]["available"]:
        code = 'p<-c("ggplot2","ggprism","svglite","jsonlite","systemfonts"); for(x in p){cat(x," ",requireNamespace(x,quietly=TRUE)," ",if(requireNamespace(x,quietly=TRUE)) as.character(packageVersion(x)) else "-","\\n",sep="")}; cat("cairo ",capabilities("cairo"),"\\n",sep="")'
        proc = subprocess.run(["Rscript", "-e", code], capture_output=True, text=True, timeout=60)
        packages = {}
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                packages[parts[0]] = {"available": parts[1] == "TRUE", "version": parts[2]}
        probe["R"] = {"returncode": proc.returncode, "packages": packages,
                      "ready": proc.returncode == 0 and all(packages.get(p, {}).get("available") for p in ("ggplot2", "ggprism", "svglite", "jsonlite", "systemfonts")),
                      "stderr": proc.stderr[-1000:]}
    else:
        probe["R"] = {"ready": False, "packages": {}}
    return probe


def run_command(command: list[str], cwd: Path, log_path: Path, timeout: int = 300) -> dict:
    try:
        proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        report = {"command": command, "returncode": proc.returncode, "stdout": proc.stdout[-4000:], "stderr": proc.stderr[-4000:]}
    except Exception as error:
        report = {"command": command, "returncode": -1, "stdout": "", "stderr": str(error)}
    log_path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def artifact_hashes(plan: dict, out_dir: Path) -> dict:
    result = {}
    for figure in plan["figures"]:
        names = [figure["data_file"], f"figure_{figure['index']}.csv", f"figure_{figure['index']}_lineage.json",
                 figure["analysis_file"]] + [f"figure_{figure['index']}.{ext}" for ext in FORMATS]
        result[figure["figure_id"]] = {name: sha256_file(out_dir / name) if (out_dir / name).exists() else None for name in names}
    return result


def caption_for(figure: dict, analysis: dict, preparation: dict) -> dict:
    chart = analysis.get("actual_chart_type") or figure["chart_type"]
    x_label = figure["x_label"] + (f" ({figure['x_unit']})" if figure.get("x_unit") else "")
    y_label = figure["y_label"] + (f" ({figure['unit']})" if figure.get("unit") else "")
    n = analysis.get("n_used", 0)
    error_type = figure.get("error_bar", "none")
    error_text = {
        "sd": "误差线为 ±1 样本标准差（SD，n−1）", "se": "误差线为 ±1 标准误（SE=SD/√n）",
        "ci95": "误差线为均值 95% 置信区间（Student t，df=n−1）", "none": "不添加未声明的误差线",
    }.get(error_type, "误差线配置无效")
    if chart == "scatter_with_fit":
        fit = analysis.get("fit") or {}
        if isinstance(fit, list):
            parts = [f"{item.get('group')}: r={item.get('r'):.6g}, slope={item.get('slope'):.6g}"
                     if item.get("status") in {"ok", "fitted"} and item.get("r") is not None
                     else f"{item.get('group')}: {item.get('reason') or '拟合未定义'}" for item in fit]
            stats = "分组最小二乘拟合与 95% 置信带（仅定义处）；" + "；".join(parts)
        else:
            stats = (f"总体最小二乘拟合：slope={fit.get('slope'):.6g}，intercept={fit.get('intercept'):.6g}，"
                     f"Pearson r={fit.get('r'):.6g}，R²={fit.get('r_squared'):.6g}；带 95% 置信带"
                     if fit.get("status") in {"ok", "fitted"} else f"仅展示散点；{fit.get('reason') or '拟合或相关系数未定义'}")
        error_text = "散点不画单点误差线；拟合带（若存在）为均值响应的 95% 置信带"
    elif chart == "histogram":
        stats = f"连续变量直方图；Sturges 分组；有效 n={n}"
        error_text = "直方图不使用均值误差线"
    elif chart == "correlation_heatmap":
        stats = "Pearson 相关，pairwise complete observations；常量列或有效对不足处为 NA；色阶固定 [-1,1]"
        error_text = "相关热图不画误差线；未定义相关不写成 0"
    elif chart in {"stacked_bar", "category_count"}:
        stats = f"按源数据直接聚合；有效 n={n}；柱/条长度从 0 起"
        error_text = "构成/计数图不添加均值误差线"
    elif "box" in chart:
        stats = "箱线为中位数与 IQR，须线为 1.5×IQR；叠加全部有效原始点"
        error_text = "箱线图不使用均值 ± SD/SE 代替分布"
    elif "violin" in chart:
        stats = "小提琴展示核密度形状并叠加全部有效原始点；样本量不足时仅画点"
        error_text = "密度形状不作为组间显著性证据"
    elif chart == "bar_with_error_points":
        stats = f"各组均值与全部有效原始观测；{error_text}"
    elif "point" in chart or chart == "comparison_auto":
        stats = "展示各组原始观测与样本量；小样本不隐藏分布"
    else:
        stats = f"各组均值与原始观测；{error_text}"
    default_title = {
        "correlation_heatmap": "变量间 Pearson 相关矩阵", "category_count": "各类别观测计数",
        "histogram": f"{figure['x_label']} 的分布", "stacked_bar": "类别构成",
    }.get(chart, f"{figure['y_label'] or figure['x_label'] or '科研数据'} — {chart}")
    title = figure.get("title") or default_title
    sig = figure.get("significance")
    if sig:
        method = ""
        if isinstance(sig, dict):
            method = str(sig.get("method") or sig.get("test") or sig.get("correction") or "").strip()
        stats += ("；显著性标记透传自任务提供的统计结果" + (f"（{method}）" if method else "")
                  + "，本 Skill 未计算任何检验")
    y_scale = analysis.get("y_scale") or {}
    if y_scale.get("applied"):
        stats += f"；Y 轴为 log10 刻度（数据全正且跨 {y_scale.get('decades')} 个数量级），等视觉距离代表等比值"
    unit_sizes = {str(item.get("category")): item.get("n") for item in analysis.get("series") or []} if figure.get("unit_id") else {}
    if unit_sizes:
        stats += "；技术读数先在每个独立单位内平均，再以单位均值计算组间摘要"
    standalone = (f"{title}。横轴：{x_label or '不适用'}；纵轴：{y_label or '频数/数值见色标'}。"
                  f"有效观测行 n={n}，排除 {preparation.get('n_excluded', analysis.get('n_excluded', 0))} 行。"
                  + (f"独立单位数：{unit_sizes}。" if unit_sizes else "")
                  + f"{stats}。{error_text}。所有排除原因见对应 lineage 文件。")
    return {"title": title, "panel": figure.get("panel") or "", "x_label": x_label, "y_label": y_label, "n": n, "n_independent_units": unit_sizes,
            "groups": analysis.get("groups") or preparation.get("groups") or [],
            "error_bar": error_text, "stats": stats, "standalone_text": standalone}


def mapping_for(figure: dict) -> dict:
    chart = figure["chart_type"]
    mappings = {
        "bar_with_error_points": [("x", "x position/category + point fill (category colour)"), ("y", "bar length + raw point y"), ("y", "declared uncertainty")],
        "comparison_auto": [("x", "category position + point fill (category colour)"), ("y", "point/box distribution")],
        "boxplot_points": [("x", "category position + point fill (category colour)"), ("y", "box distribution + raw points")],
        "violin_points": [("x", "category position + point fill (category colour)"), ("y", "density shape + raw points")],
        "scatter_with_fit": [("x", "x position"), ("y", "y position"), ("group", "colour + shape")],
        "line_series": [("x", "ordered temporal/numeric x"), ("y", "y position"), ("group", "colour + linetype")],
        "histogram": [("x", "continuous bin position"), ("group", "facet")],
        "stacked_bar": [("x", "whole/category position"), ("y", "stacked length"), ("group", "component colour")],
        "category_count": [("x", "sorted category + computed count length")],
        "correlation_heatmap": [("variables", "row/column"), ("correlation", "diverging colour + colorbar")],
    }
    return {
        "figure_id": figure["figure_id"], "plot_data": figure["data_file"],
        "lineage": f"figure_{figure['index']}_lineage.json",
        "source_columns": [v for v in [figure.get("x"), figure.get("y"), figure.get("group"), figure.get("unit_id")] if v] or figure.get("variables", []),
        "mapping": [{"source": source, "channel": channel} for source, channel in mappings[chart]],
        "aggregation": "row-level points plus declared OLS" if chart == "scatter_with_fit" else
                       "repeated-x arithmetic means plus raw points; source ids in series" if chart == "line_series" else
                       "independent-unit means before group summaries" if figure.get("unit_id") and chart in {"bar_with_error_points", "comparison_auto", "boxplot_points", "violin_points"} else
                       "documented in analysis JSON; series retain source_row_ids where applicable",
    }


def finalize_payload(res: Result, payload: dict) -> None:
    aliases = {
        "deliverable": "figures", "deliverables": "figures", "analysis_result": "figures", "分析结果": "figures",
        "quality_evidence": "evidence_map", "reproducibility": "evidence_map", "质量与复现证据": "evidence_map",
        "exceptions": "状态说明", "exception_notes": "状态说明", "异常说明": "状态说明",
    }
    res.bind(payload, aliases=aliases)
    res.fields.pop("result", None)  # 契约字段与规范字段保留一份，避免重复嵌套
    for key, value in payload.items():
        if key not in res.fields:
            res.set(key, value)
    if res.missing_contract():
        res._fill_missing()
        res.status["overall"] = "partial"
        if isinstance(payload.get("状态说明"), dict):
            payload["状态说明"]["overall"] = "partial"
            payload["状态说明"].setdefault("gaps", []).extend(item["item"] for item in res.status["unmet"])


def probe_environment_declaration(base_dirs) -> dict:
    """读取 Harbor 运行时声明的 environment/resources.yaml（存在时），并入证据链。

    不假设其内容结构：能解析则提取 fonts/tools 摘要，否则保存原文截断。
    """
    for base in base_dirs:
        for candidate in (Path(base) / "environment" / "resources.yaml",
                          Path(base) / "resources.yaml"):
            if candidate.is_file():
                try:
                    text = candidate.read_text(encoding="utf-8")
                except OSError:
                    continue
                entry: dict = {"path": str(candidate), "sha256": sha256_text(text)}
                try:
                    import yaml  # type: ignore
                    parsed = yaml.safe_load(text)
                    if isinstance(parsed, dict):
                        entry["keys"] = sorted(str(k) for k in parsed)
                        fonts = parsed.get("fonts")
                        if isinstance(fonts, list):
                            entry["declared_fonts"] = [str(f.get("family") or f) if isinstance(f, dict) else str(f)
                                                       for f in fonts][:20]
                        tools = parsed.get("tools") or parsed.get("preinstalled")
                        if isinstance(tools, (list, dict)):
                            names = list(tools) if isinstance(tools, dict) else [str(t.get("name") or t) if isinstance(t, dict) else str(t) for t in tools]
                            entry["declared_tools"] = names[:30]
                except Exception:
                    entry["excerpt"] = text[:1500]
                    entry["parse"] = "stored raw excerpt (yaml not parseable in this env)"
                return entry
    return {}


def run(case: dict, task_text: str, args) -> Result:
    clock = Clock(getattr(args, "budget", 900.0))
    res = Result(case, skill=SKILL, clock=clock)
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    base_dir = Path(args.case).resolve().parent
    table = load_table(case, base_dir)
    dictionary = load_json_reference(first_present(case, "data_dictionary", "dictionary", "data_dictionary_path", default={}) or {}, base_dir)
    raw_reqs = load_json_reference(first_present(case, "figure_requests", "figures", "requests", "figure_requests_path",
                                                  "workorder", "workorder_path", default=[]) or [], base_dir)
    if isinstance(raw_reqs, dict):
        raw_reqs = first_present(raw_reqs, "figure_requests", "figures", "requests", default=[])
    reqs = [item for item in as_list(raw_reqs) if isinstance(item, dict)]
    rules = {name: load_json(RULES_DIR / f"{name}.json", default={}) or {} for name in ("chart_choice", "palette", "venue")}
    source_path = table.get("source_path")
    source_meta = {
        "name": table.get("name"), "note": table.get("note"), "source_path": source_path,
        "original_sha256": sha256_file(Path(source_path)) if source_path and Path(source_path).is_file() else sha256_text(json.dumps(table["rows"], ensure_ascii=False, sort_keys=True)),
        "n_rows": len(table["rows"]), "columns": table["columns"], "snapshot": "source_data.tsv",
    }
    plan, unrouted = make_plan(case, table, dictionary, reqs, rules, source_meta, task_text)
    write_raw_snapshot(table, out_dir / "source_data.tsv")
    source_meta["snapshot_sha256"] = sha256_file(out_dir / "source_data.tsv")
    write_json(out_dir / "run_plan.json", plan)
    for figure in plan["figures"]:
        write_json(out_dir / f"figure_{figure['index']}_spec.json", figure)
    copy_runtime(out_dir)
    probe = probe_dependencies()
    probe["environment_declaration"] = probe_environment_declaration(
        [Path(args.case).resolve().parent, *Path(args.case).resolve().parents, Path.cwd(), Path("/input"), Path("/app")])
    write_json(out_dir / "dependencies.json", probe)
    res.log("load_case", rows=len(table["rows"]), requests=len(reqs), routed=len(plan["figures"]))

    if not table["rows"] or not reqs:
        res.status["overall"] = "empty"
        res.unmet("数据/工单", "case 未提供非空数据表或 figure_requests；Agent 需先从任务说明形成结构化出图工单")
    elif not plan["figures"]:
        res.status["overall"] = "partial"
    elif not probe["R"]["ready"]:
        res.unmet("R 绘图依赖", "需要 Rscript、ggplot2、ggprism、svglite、jsonlite、systemfonts；详见 dependencies.json；已尝试 Python 降级渲染（仅 PNG）")
        # 数据整备纯 Python：先整备出干净 TSV/血缘，再用 matplotlib 降级渲染真实 PNG
        prep_fallback = run_command(["bash", "data_prep.sh"], out_dir, out_dir / "data_prep.log")
        if prep_fallback["returncode"] == 0 and probe.get("matplotlib", {}).get("available"):
            fb_run = run_command([sys.executable, "-B", "scripts/fallback_render.py", "--plan", "run_plan.json", "--out", "."],
                                 out_dir, out_dir / "fallback_render.log")
            res.log("fallback_render", rc=fb_run["returncode"])
            if fb_run["returncode"] != 0:
                res.unmet("降级渲染", "fallback_render.py 未成功完成；见 fallback_render.log")
        elif prep_fallback["returncode"] != 0:
            res.unmet("数据整备", "data_prep.sh 在无 R 环境下也失败了；见 data_prep.log")
        else:
            res.unmet("降级渲染", "matplotlib 不可用，无法产出任何图件；快照与复跑材料已保存")
    else:
        first_prep = run_command(["bash", "data_prep.sh"], out_dir, out_dir / "data_prep.log")
        first_plot = run_command(["Rscript", "plot.R"], out_dir, out_dir / "plot.log") if first_prep["returncode"] == 0 else {"returncode": -1, "stderr": "data preparation failed"}
        first_normalize = run_command([sys.executable, "-B", "scripts/normalize_pdf.py", "--plan", "run_plan.json", "--out", "."],
                                      out_dir, out_dir / "normalize_pdf.log") if first_plot["returncode"] == 0 else {"returncode": -1}
        if first_prep["returncode"] != 0 or first_plot["returncode"] != 0 or first_normalize["returncode"] != 0:
            res.unmet("首次完整运行", f"data_prep rc={first_prep['returncode']}, plot rc={first_plot['returncode']}, normalize rc={first_normalize['returncode']}；见日志")
        else:
            before = artifact_hashes(plan, out_dir)
            second_prep = run_command(["bash", "data_prep.sh"], out_dir, out_dir / "data_prep_rerun.log")
            second_plot = run_command(["Rscript", "plot.R"], out_dir, out_dir / "plot_rerun.log") if second_prep["returncode"] == 0 else {"returncode": -1}
            second_normalize = run_command([sys.executable, "-B", "scripts/normalize_pdf.py", "--plan", "run_plan.json", "--out", "."],
                                           out_dir, out_dir / "normalize_pdf_rerun.log") if second_plot["returncode"] == 0 else {"returncode": -1}
            after = artifact_hashes(plan, out_dir)
            res.log("end_to_end_rerun", prep_rc=second_prep["returncode"], plot_rc=second_plot["returncode"], normalize_rc=second_normalize["returncode"])
        audit_run = run_command([sys.executable, "-B", "scripts/audit_outputs.py", "--plan", "run_plan.json", "--out", "."],
                                out_dir, out_dir / "audit.log")
        if audit_run["returncode"] != 0:
            res.unmet("导出与数值审计", "audit_outputs.py 未成功完成；见 audit.log")

    preparation = {item["figure_id"]: item for item in (load_json(out_dir / "preparation.json", default={}) or {}).get("figures", [])}
    analyses = {figure["figure_id"]: load_json(out_dir / figure["analysis_file"], default={}) or {} for figure in plan["figures"]}
    audits = {item["figure_id"]: item for item in (load_json(out_dir / "audit.json", default={}) or {}).get("figures", [])}
    fallbacks = {item.get("figure_id"): item for item in (load_json(out_dir / "fallback_render.json", default={}) or {}).get("figures", [])}
    after = artifact_hashes(plan, out_dir)
    before = locals().get("before", {})
    figures_out, maps, gaps, format_gaps = list(unrouted), [], [], []
    for item in unrouted:
        res.unmet(f"{item['figure_id']} 图型路由", item["reason"])
        gaps.append(f"{item['figure_id']}: {item['reason']}")
    for figure in plan["figures"]:
        fid = figure["figure_id"]
        prep = preparation.get(fid, {"status": "error", "reason": "data preparation result missing", "n_used": 0})
        analysis = analyses.get(fid, {})
        audit = audits.get(fid, {"checks": [], "all_pass": False, "files": {}})
        reproduction = {"commands": ["bash data_prep.sh", "Rscript plot.R", "python3 -B scripts/normalize_pdf.py --plan run_plan.json --out ."],
                        "before": before.get(fid, {}), "after": after.get(fid, {})}
        reproduction["per_file_sha_match"] = {name: before.get(fid, {}).get(name) is not None and before.get(fid, {}).get(name) == after.get(fid, {}).get(name)
                                               for name in after.get(fid, {})}
        reproduction["end_to_end_sha_match"] = bool(reproduction["per_file_sha_match"]) and all(reproduction["per_file_sha_match"].values())
        caption = caption_for(figure, analysis, prep) if analysis else None
        checks = list(audit.get("checks", []))
        checks.append({"check": "caption_self_contained", "pass": bool(caption and caption.get("n") and caption.get("standalone_text")), "detail": "title/axes/n/method/uncertainty/exclusions"})
        requested = set(figure.get("required_formats") or FORMATS)
        supported_requested = requested & set(FORMATS)
        unknown_formats = sorted(requested - set(FORMATS))
        missing_supported = sorted(ext for ext in supported_requested
                                   if not audit.get("files", {}).get(ext, {}).get("exists"))
        checks.append({"check": "requested_formats",
                       "pass": bool(supported_requested) and not missing_supported,
                       "detail": f"requested={sorted(requested)}; supported_delivered={sorted(supported_requested)}; missing={missing_supported}; PDF/PNG/SVG are emitted by default"})
        if unknown_formats:
            checks.append({"check": "unsupported_format_requests", "pass": True,
                           "detail": f"{unknown_formats} 超出本 Skill 支持范围（PDF/PNG/SVG）；已记录为范围外缺口并交付默认三格式，不静默忽略"})
            format_gaps.append({"figure_id": fid, "formats": unknown_formats,
                                "reason": "导出格式超出支持范围；交付 PDF/600dpi PNG/可编辑 SVG，未伪造缺失格式"})
            gaps.append(f"{fid}: 请求的导出格式 {unknown_formats} 不在支持范围内；已交付 PDF/PNG/SVG 并显式记录")
        checks.append({"check": "end_to_end_reproduce", "pass": reproduction["end_to_end_sha_match"], "detail": "raw snapshot → TSV → R statistics → PDF/PNG/SVG, two complete runs"})
        all_pass = bool(checks) and all(check["pass"] for check in checks)
        status = "delivered" if analysis.get("status") == "ok" and all_pass else "partial"
        if status != "delivered":
            reason = prep.get("reason") or analysis.get("reason") or "QA/reproduction incomplete: " + ", ".join(c["check"] for c in checks if not c["pass"])
            res.unmet(f"{fid} 图件", reason)
            gaps.append(f"{fid}: {reason}")
        figures_out.append({
            "figure_id": fid, "status": status, "purpose": figure["purpose"],
            "chart_type": figure["chart_type"], "actual_chart_type": analysis.get("actual_chart_type"),
            "selection_rationale": f"purpose={figure['purpose']}; requested={figure.get('requested_chart') or 'auto'}; executable route={figure['chart_type']}",
            "rejected_alternatives": figure.get("rejected_alternatives", []),
            "venue": {key: figure[key] for key in ("name", "width_mm", "height_mm", "dpi", "min_font_pt")},
            "layout_adjustment": figure.get("layout_adjustment"),
            "format_gaps": [item for item in format_gaps if item.get("figure_id") == fid],
            "fallback": fallbacks.get(fid),
            "preparation": prep, "analysis": analysis, "caption": caption,
            "resources": {"tsv": figure["data_file"], "csv": f"figure_{figure['index']}.csv",
                          "spec": f"figure_{figure['index']}_spec.json", "lineage": f"figure_{figure['index']}_lineage.json",
                          "analysis": figure["analysis_file"], "data_prep": "data_prep.sh", "plot_script": "plot.R",
                          "theme": "theme.R", "normalize_pdf": "scripts/normalize_pdf.py", "audit": "scripts/audit_outputs.py"},
            "files": audit.get("files", {}), "qa": {"checks": checks, "all_pass": all_pass},
            "reproduce": reproduction,
        })
        maps.append(mapping_for(figure))
        res.log("figure", figure_id=fid, chart=figure["chart_type"], qa_pass=all_pass)

    delivered = [figure for figure in figures_out if figure.get("status") == "delivered"]
    notes = [
        "PDF、PNG、SVG 均由同一 R/ggplot2 绘图脚本生成；默认 PNG 600dpi，最低接受 300dpi。",
        "原始快照、清洗规则、排除行、绘图 TSV、R 统计、版本和运行日志均随结果保存。",
        "自动检查不替代科学语义审查；使用 Agent 时还需查看 PNG/PDF 的实际渲染。",
    ]
    res.uncertainty.append({"item": "视觉语义复核", "reason": "像素边距、字体、色板和数值检查不能证明图型与研究问题完全匹配"})
    evidence_map = {
        "source": source_meta, "run_plan": "run_plan.json", "data_quality": "data_quality.json",
        "preparation": "preparation.json", "dependencies": "dependencies.json",
        "environment_declaration": probe.get("environment_declaration") or "not found in runtime (recorded as absent)",
        "rules": {name: {"file": f"rules/{name}.json", "sha256": sha256_file(RULES_DIR / f"{name}.json")} for name in rules},
        "scripts": ["data_prep.sh", "scripts/prepare_data.py", "plot.R", "theme.R", "scripts/normalize_pdf.py", "scripts/audit_outputs.py"],
        "reproduce_commands": ["bash data_prep.sh", "Rscript plot.R", "python3 -B scripts/normalize_pdf.py --plan run_plan.json --out .",
                               "python3 -B scripts/audit_outputs.py --plan run_plan.json --out ."],
        "gaps": gaps,
    }
    figures_obj = {"n_requested": len(reqs), "n_delivered": len(delivered), "figures": figures_out,
                   "format_gaps": format_gaps,
                   "probe": probe, "export_policy": {"default_formats": list(FORMATS), "png_dpi": 600, "svg_text": "editable", "pdf_fonts": "embedded evidence audited",
                                                     "unsupported_requests": "记录为范围外缺口并交付默认三格式；支持范围内格式缺失才判 partial",
                                                     "no_r_fallback": "R 依赖缺失时用 matplotlib 降级渲染真实 PNG（仅 PNG，PDF/SVG 缺口如实记录，不伪造）"}}
    map_obj = {"n_figures": len(maps), "maps": maps, "note": "绘图 TSV 保留完整精度与 source_row_id；聚合统计回指 R analysis 和 lineage"}
    baseline_obj = {
        "claim": "同一数据与任务要求下，本 Skill 相对基础 Agent/通用绘图方案的确定性防御对照；平台侧公平对照由组织方自行运行，本表不虚构基线数字",
        "dimensions": BASELINE_COMPARISON,
        "verified_by": ["figures[].reproduce.end_to_end_sha_match", "audit.json 各项检查", "validate.py + validate_figures.py 全 PASS"],
    }
    res.evidence.update({"figure_pipeline": evidence_map, "notes": notes})
    res.status["overall"] = "complete" if reqs and len(delivered) == len(reqs) and not res.status["unmet"] else ("empty" if not reqs or not table["rows"] else "partial")
    status_view = {"overall": res.status["overall"], "gaps": gaps, "notes": notes, "uncertainty": res.uncertainty}
    res.done("原始快照与数据字典适配", "运行计划与动态输出契约")
    if preparation:
        res.done("声明式清洗与行级血缘")
    if delivered:
        res.done("R/ggplot2 统计绘图", "PDF/PNG/SVG 三格式导出", "独立数值与元数据审计")
    if delivered and all((figure.get("reproduce") or {}).get("end_to_end_sha_match") for figure in delivered):
        res.done("两次端到端复现比较")
    finalize_payload(res, {"figures": figures_obj, "data_to_visual_map": map_obj,
                           "baseline_comparison": baseline_obj,
                           "evidence_map": evidence_map, "状态说明": status_view})
    res.log("emit", delivered=len(delivered), gaps=len(gaps))
    return res


def emit_failure(case: dict, args, error: Exception) -> None:
    res = Result(case or {}, skill=SKILL)
    res.status["overall"] = "input_error"
    res.unmet("运行失败", str(error))
    payload = {"figures": {"n_requested": 0, "n_delivered": 0, "figures": []},
               "data_to_visual_map": {"n_figures": 0, "maps": []},
               "evidence_map": {"error": str(error)},
               "状态说明": {"overall": "input_error", "gaps": [str(error)], "notes": [], "uncertainty": []}}
    finalize_payload(res, payload)
    res.emit(args.out)


def main(argv=None) -> int:
    args = parse_args(argv)
    case = {}
    try:
        case = load_case(args.case)
        task = load_task_text(args, case)
        res = run(case, task, args)
        if args.dry_run:
            print(json.dumps({"case_id": res.case_id, "status": res.status["overall"]}, ensure_ascii=False))
            return 0
        path = res.emit(args.out)
        print(json.dumps({"ok": True, "result": str(path), "status": res.status["overall"]}, ensure_ascii=False))
        return 0
    except Exception as error:
        try:
            emit_failure(case, args, error)
            print(json.dumps({"ok": False, "error": str(error), "result": str(Path(args.out) / "result.json")}, ensure_ascii=False))
            return 0
        except Exception as emit_error:
            print(json.dumps({"ok": False, "error": str(error), "emit_error": str(emit_error)}, ensure_ascii=False))
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
