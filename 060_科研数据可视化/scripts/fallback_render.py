#!/usr/bin/env python3
"""无 R 环境时的 matplotlib 降级渲染：只产出真实 PNG 图件与降级记录，不伪造任何统计。

输入与 plot.R 完全一致（run_plan.json + figure_N.tsv），输出：
  figure_N.png            —— 300dpi 以上真实渲染（简式主题）
  figure_N_fallback.json  —— 降级记录（实际图型、覆盖说明、原因）
原则：
  - 只画数据本身与声明的误差线（SD/SE/CI95 来自 prepare_data 已算列则用之，否则现算均值摘要）
  - 显著性/拟合曲线等如需统计一律现算且明确标注方法；任务提供的 significance 原样透传文本化
  - PDF/SVG 不伪造：降级期间只有 PNG，缺口由 pipeline 记入 format_gaps/status.unmet
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path


def load_plan(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_tsv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def numeric(value):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def wrap(text: str, width: int = 38) -> str:
    if not text:
        return ""
    out, line = [], ""
    for ch in str(text):
        line += ch
        if len(line) >= width and ch in " ）)":
            out.append(line)
            line = ""
    if line:
        out.append(line)
    return "\n".join(out)


def axis_label(figure: dict, which: str) -> str:
    if which == "x":
        label = figure.get("x_label") or figure.get("x") or ""
        unit = figure.get("x_unit") or ""
    else:
        label = figure.get("y_label") or figure.get("y") or ""
        unit = figure.get("unit") or figure.get("y_unit") or ""
    return f"{label} ({unit})" if unit and unit not in label else label


def pick_cjk_font():
    try:
        from matplotlib import font_manager
    except Exception:
        return None
    for name in ("PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "Source Han Sans SC",
                 "Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei", "Arial Unicode MS"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            return name
        except Exception:
            continue
    return None


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def error_half_width(rows_y, error_type):
    n = len(rows_y)
    if n < 2 or error_type in (None, "", "none"):
        return None
    mean = sum(rows_y) / n
    var = sum((v - mean) ** 2 for v in rows_y) / (n - 1)
    sd = math.sqrt(var)
    if error_type == "sd":
        return sd
    if error_type == "se":
        return sd / math.sqrt(n)
    if error_type == "ci95":
        try:
            from scipy import stats as sps
        except Exception:
            return sd / math.sqrt(n) * 1.96
        return sps.t.ppf(0.975, n - 1) * sd / math.sqrt(n)
    return None


def render(figure: dict, tsv_path: Path, out_dir: Path) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    chart = figure.get("chart_type") or ""
    columns, rows = read_tsv(tsv_path)
    index = figure.get("index")
    record = {"figure_id": figure.get("figure_id"), "chart_type": chart, "degraded": True,
              "renderer": "matplotlib-fallback", "n_rows": len(rows), "notes": []}
    texts = " ".join(str(figure.get(k) or "") for k in ("title", "x_label", "y_label"))
    cjk = has_cjk(texts)
    font = pick_cjk_font() if cjk else None
    if cjk and font:
        matplotlib.rcParams["font.family"] = [font]
        matplotlib.rcParams["axes.unicode_minus"] = False
    elif cjk:
        record["notes"].append("CJK 标签未找到中文字体，可能显示为方框；已在降级记录中如实说明")
    width_in = max(2.0, float(figure.get("width_mm") or 89) / 25.4)
    height_in = max(1.6, float(figure.get("height_mm") or 62) / 25.4)
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    title = figure.get("title") or f"{figure.get('figure_id', 'figure')}"
    ax.set_title(wrap(title), fontsize=min(13, 6 + width_in), fontweight="bold")
    error_type = str(figure.get("error_bar") or "none").lower()

    if chart == "correlation_heatmap":
        variables = [v for v in (figure.get("variables") or []) if v in columns]
        data = {v: [numeric(r.get(v)) for r in rows] for v in variables}
        import statistics
        n_var = len(variables)
        corr = [[None] * n_var for _ in range(n_var)]
        for i in range(n_var):
            for j in range(n_var):
                pairs = [(a, b) for a, b in zip(data[variables[i]], data[variables[j]]) if a is not None and b is not None]
                if len(pairs) >= 2:
                    xs, ys = zip(*pairs)
                    try:
                        sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
                        mx, my = statistics.mean(xs), statistics.mean(ys)
                        if sx > 0 and sy > 0:
                            cov = sum((a - mx) * (b - my) for a, b in pairs) / len(pairs)
                            corr[i][j] = cov / (sx * sy)
                    except Exception:
                        pass
        im = ax.imshow([[v if v is not None else float("nan") for v in row] for row in corr],
                       cmap="RdBu_r", vmin=-1, vmax=1, aspect="equal")
        ax.set_xticks(range(n_var))
        ax.set_yticks(range(n_var))
        labels = {v: v for v in variables}
        vl = figure.get("variable_labels") or {}
        labels.update({k: str(v) for k, v in vl.items()})
        names = [labels.get(v, v) for v in variables]
        ax.set_xticklabels(names, rotation=45 if n_var <= 4 else 90, ha="right" if n_var <= 4 else "center", fontsize=max(6, 10 - n_var // 4))
        ax.set_yticklabels(names, fontsize=max(6, 10 - n_var // 4))
        fig.colorbar(im, ax=ax, label="Pearson r", shrink=0.85)
        if n_var <= 12:
            for i in range(n_var):
                for j in range(n_var):
                    value = corr[i][j]
                    if value is not None:
                        ax.text(j, i, f"{value:.2f}", ha="center", va="center", fontsize=max(5, 9 - n_var // 2))
        record["actual_chart_type"] = "correlation_heatmap"

    elif chart == "category_count":
        counts: dict[str, int] = {}
        for row in rows:
            key = str(row.get("x") or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
        ordered = sorted(counts, key=lambda k: (counts[k], k))
        ax.barh(ordered, [counts[k] for k in ordered], color="#0072B2CC", edgecolor="black")
        ax.set_xlabel("Count")
        ax.set_ylabel(axis_label(figure, "x"))
        record["actual_chart_type"] = "category_count"

    elif chart == "histogram":
        values = [numeric(r.get("x_num", r.get("x"))) for r in rows]
        values = [v for v in values if v is not None]
        ax.hist(values, bins=max(5, min(40, int(math.ceil(math.log2(len(values) + 1)) + 1))),
                color="#0072B2CC", edgecolor="black")
        ax.set_xlabel(axis_label(figure, "x"))
        ax.set_ylabel("Count")
        record["actual_chart_type"] = "histogram"

    elif chart in ("scatter_with_fit", "line_series"):
        xs = [numeric(r.get("x_num", r.get("x"))) for r in rows]
        ys = [numeric(r.get("y")) for r in rows]
        groups = [str(r.get("group") or "").strip() for r in rows]
        series: dict[str, tuple[list, list]] = {}
        for x, y, g in zip(xs, ys, groups):
            if x is None or y is None:
                continue
            key = g or "all"
            series.setdefault(key, ([], []))
            series[key][0].append(x)
            series[key][1].append(y)
        palette = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
        for i, (key, (sx, sy)) in enumerate(sorted(series.items())):
            color = palette[i % len(palette)]
            if chart == "line_series":
                pairs = sorted(zip(sx, sy))
                ax.plot([p[0] for p in pairs], [p[1] for p in pairs], marker="o", markersize=3,
                        linewidth=1.2, color=color, label=key or None, alpha=0.85)
            else:
                ax.scatter(sx, sy, s=14, color=color, edgecolors="white", linewidths=0.3,
                           label=key if key != "all" else None, alpha=0.75)
                if len(sx) >= 3 and len(set(sx)) >= 2:
                    import statistics as st
                    mx, my = st.mean(sx), st.mean(sy)
                    sxx = sum((v - mx) ** 2 for v in sx)
                    if sxx > 0:
                        slope = sum((a - mx) * (b - my) for a, b in zip(sx, sy)) / sxx
                        intercept = my - slope * mx
                        lo, hi = min(sx), max(sx)
                        ax.plot([lo, hi], [intercept + slope * lo, intercept + slope * hi],
                                color=color, linewidth=1.0, linestyle="--", alpha=0.9)
                        record.setdefault("fits", []).append(
                            {"group": key, "slope": slope, "intercept": intercept,
                             "method": "numpy-free OLS on plotted points (degraded path)"})
        ax.set_xlabel(axis_label(figure, "x"))
        ax.set_ylabel(axis_label(figure, "y"))
        if len(series) > 1:
            ax.legend(title=figure.get("group_label") or figure.get("group") or "", fontsize=7)
        record["actual_chart_type"] = chart

    else:  # bar_with_error_points / comparison_auto / boxplot_points / violin_points / stacked_bar
        cats: dict[str, list] = {}
        for row in rows:
            key = str(row.get("x") or "").strip()
            y = numeric(row.get("y"))
            if key and y is not None:
                cats.setdefault(key, []).append(y)
        order = sorted(cats)
        means = [sum(v) / len(v) for v in (cats[k] for k in order)]
        errs = [error_half_width(cats[k], error_type) for k in order]
        if len(order) > 8:
            ax.barh(order, means, xerr=errs, color="#0072B2CC", edgecolor="black",
                    error_kw={"ecolor": "black", "capsize": 2})
            ax.set_ylabel(axis_label(figure, "x"))
            ax.set_xlabel(axis_label(figure, "y"))
            for i, k in enumerate(order):
                ys = cats[k]
                jitter = [((2 * ((idx * 37) % 23) / 22) - 1) * 0.12 for idx in range(len(ys))]
                ax.scatter([i + j for j in jitter], ys, s=8, color="#22222288", zorder=3)
        else:
            ax.bar(order, means, yerr=errs, capsize=3, color="#0072B2CC", edgecolor="black",
                   error_kw={"ecolor": "black", "capsize": 2})
            ax.set_xlabel(axis_label(figure, "x"))
            ax.set_ylabel(axis_label(figure, "y"))
            for i, k in enumerate(order):
                ys = cats[k]
                jitter = [((2 * ((idx * 37) % 23) / 22) - 1) * 0.12 for idx in range(len(ys))]
                ax.scatter([i + j for j in jitter], ys, s=9, color="#22222266", zorder=3)
        record["actual_chart_type"] = "bar_with_error_points" if chart == "bar_with_error_points" else "category_summary"
        record["notes"].append("降级路径按类别聚合原始点并绘制均值柱；误差线按声明的口径现算")

    significance = figure.get("significance")
    if significance:
        record["significance"] = significance
        record["notes"].append("任务提供的显著性结果原样记录于本 JSON；降级路径未在图上绘制标记")

    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    png = out_dir / f"figure_{index}.png"
    dpi = max(300, int(figure.get("dpi") or 600))
    fig.savefig(png, dpi=dpi)
    plt.close(fig)
    record["png"] = png.name
    record["dpi"] = dpi
    return record


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    plan_path = Path(args[args.index("--plan") + 1]) if "--plan" in args else Path("run_plan.json")
    out_dir = Path(args[args.index("--out") + 1]) if "--out" in args else Path(".")
    plan = load_plan(plan_path)
    records = []
    for figure in plan.get("figures") or []:
        tsv = out_dir / figure.get("data_file", f"figure_{figure.get('index')}.tsv")
        if not tsv.is_file():
            records.append({"figure_id": figure.get("figure_id"), "status": "missing_data", "reason": f"{tsv.name} 不存在"})
            continue
        try:
            records.append(render(figure, tsv, out_dir))
        except Exception as error:  # noqa: BLE001
            records.append({"figure_id": figure.get("figure_id"), "status": "error", "reason": str(error)})
    (out_dir / "fallback_render.json").write_text(
        json.dumps({"renderer": "matplotlib-fallback", "reason": "R 绘图依赖不可用时的降级渲染；仅 PNG，不伪造 PDF/SVG",
                    "figures": records}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "rendered": sum(1 for r in records if r.get("png")),
                      "failed": sum(1 for r in records if r.get("status") in ("error", "missing_data"))},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
