#!/usr/bin/env python3
"""Audit R figure exports, numerical summaries, palettes and raster margins."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from easyplot_palette_audit import audit_palette

DEFAULT_PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E64B35", "#7E6148"]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_tsv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def mean(values):
    return sum(values) / len(values) if values else None


def sample_sd(values):
    if len(values) < 2:
        return None
    center = mean(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / (len(values) - 1))


def same(actual, expected, tolerance=2e-6):
    if actual is None or expected is None:
        return actual is None and expected is None
    actual, expected = finite(actual), finite(expected)
    if actual is None or expected is None:
        return False
    return abs(actual - expected) <= tolerance * max(1.0, abs(actual), abs(expected))


def numeric_comparison(rows: list[dict], analysis: dict) -> tuple[bool, list[str]]:
    issues = []
    grouped = defaultdict(list)
    has_units = any(str(row.get("unit_id") or "") for row in rows)
    if has_units:
        technical = defaultdict(list)
        for row in rows:
            value = finite(row.get("y"))
            if row.get("x") not in (None, "") and value is not None and str(row.get("unit_id") or ""):
                technical[(str(row["x"]), str(row["unit_id"]))].append(value)
        for (category, _unit), values in technical.items():
            grouped[category].append(mean(values))
    else:
        for row in rows:
            value = finite(row.get("y"))
            if row.get("x") not in (None, "") and value is not None:
                grouped[str(row["x"])].append(value)
    by_category = {str(item.get("category")): item for item in analysis.get("series") or []}
    if set(grouped) != set(by_category):
        issues.append("category sets differ between TSV and R analysis")
    for category, values in grouped.items():
        item = by_category.get(category)
        if not item:
            continue
        n = len(values)
        avg = mean(values)
        sd = sample_sd(values)
        se = sd / math.sqrt(n) if sd is not None else None
        for field, expected in (("n", n), ("mean", avg), ("sd", sd), ("se", se)):
            if not same(item.get(field), expected):
                issues.append(f"{category}.{field}: R={item.get(field)!r}, audit={expected!r}")
        error_type = item.get("error_type")
        error = item.get("error")
        if error_type == "sd" and not same(error, sd):
            issues.append(f"{category}.error is not SD")
        elif error_type == "se" and not same(error, se):
            issues.append(f"{category}.error is not SE")
        elif error_type == "ci95":
            if n < 2 or sd is None:
                if error is not None:
                    issues.append(f"{category}.ci95 must be null for n<2")
            elif error is None or not math.isfinite(float(error)) or float(error) < 0:
                issues.append(f"{category}.ci95 is invalid")
    return not issues, issues


def numeric_scatter(rows: list[dict], analysis: dict) -> tuple[bool, list[str]]:
    points = [(finite(row.get("x_num")), finite(row.get("y"))) for row in rows]
    points = [(x, y) for x, y in points if x is not None and y is not None]
    fit = analysis.get("fit") or {}
    if isinstance(fit, list):
        issues = []
        for record in fit:
            group = str(record.get("group") or "")
            subset = [row for row in rows if str(row.get("group") or "") == group]
            ok, sub_issues = numeric_scatter(subset, {"fit": record})
            issues.extend(f"{group}: {issue}" for issue in sub_issues)
        return not issues, issues
    n = len(points)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    sxx = sum((x - mean(xs)) ** 2 for x in xs) if n else 0.0
    syy = sum((y - mean(ys)) ** 2 for y in ys) if n else 0.0
    if n < 3 or sxx <= 0 or syy <= 0:
        undefined = all(fit.get(key) is None for key in ("r", "slope", "intercept"))
        return (undefined, [] if undefined else ["undefined correlation/fit must be null, not zero"])
    mx, my = mean(xs), mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in points)
    slope = sxy / sxx
    intercept = my - slope * mx
    correlation = sxy / math.sqrt(sxx * syy)
    issues = []
    for field, expected in (("n", n), ("slope", slope), ("intercept", intercept), ("r", correlation), ("r_squared", correlation ** 2)):
        if not same(fit.get(field), expected):
            issues.append(f"fit.{field}: R={fit.get(field)!r}, audit={expected!r}")
    return not issues, issues


def correlation(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    x_values, y_values = [p[0] for p in pairs], [p[1] for p in pairs]
    mx, my = mean(x_values), mean(y_values)
    sxx = sum((x - mx) ** 2 for x in x_values)
    syy = sum((y - my) ** 2 for y in y_values)
    if sxx <= 0 or syy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(sxx * syy)


def numeric_heatmap(rows: list[dict], variables: list[str], analysis: dict) -> tuple[bool, list[str]]:
    heatmap = analysis.get("heatmap") or {}
    matrix = heatmap.get("matrix")
    if not isinstance(matrix, list) or len(matrix) != len(variables):
        return False, ["heatmap matrix is absent or has wrong dimensions"]
    issues = []
    for i, first in enumerate(variables):
        for j, second in enumerate(variables):
            expected = 1.0 if i == j and sum(finite(row.get(first)) is not None for row in rows) >= 3 and sample_sd([finite(row.get(first)) for row in rows if finite(row.get(first)) is not None]) not in (None, 0.0) else correlation(
                [finite(row.get(first)) for row in rows], [finite(row.get(second)) for row in rows]
            )
            try:
                actual = matrix[i][j]
            except (IndexError, TypeError):
                issues.append(f"missing matrix cell {first}/{second}")
                continue
            if not same(actual, expected):
                issues.append(f"matrix {first}/{second}: R={actual!r}, audit={expected!r}")
    return not issues, issues


CLIP_MARGIN_IN = 0.02   # 距画布边缘小于该值视为贴边/裁切，判失败
TIGHT_MARGIN_IN = 0.10  # 低于该值记 tight 标志（提示留白偏紧），不单独判失败


def raster_margins(path: Path, target_width_mm: float, target_height_mm: float) -> dict:
    from PIL import Image, ImageChops
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        bbox = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white")).getbbox()
        if not bbox:
            return {"blank": True, "pass": False, "policy": "fail=blank-or-clipped"}
        left, top, right, bottom = bbox
        dpi_x = image.info.get("dpi", (image.width / (target_width_mm / 25.4), 0))[0]
        dpi_y = image.info.get("dpi", (0, image.height / (target_height_mm / 25.4)))[1]
        dpi_x = dpi_x or image.width / (target_width_mm / 25.4)
        dpi_y = dpi_y or image.height / (target_height_mm / 25.4)
        margins = {
            "left_in": left / dpi_x, "top_in": top / dpi_y,
            "right_in": (image.width - right) / dpi_x, "bottom_in": (image.height - bottom) / dpi_y,
        }
        clipped = min(margins.values()) < CLIP_MARGIN_IN
        return {"blank": False, "bbox_px": list(bbox), "margins": margins,
                "min_margin_in": min(margins.values()),
                "clipped": clipped, "tight": min(margins.values()) < TIGHT_MARGIN_IN,
                "clip_margin_in": CLIP_MARGIN_IN, "tight_margin_in": TIGHT_MARGIN_IN,
                "pass": not clipped, "policy": "fail=blank-or-clipped; tight margins are reported but do not fail a figure"}


def audit_figure(figure: dict, preparation: dict, out_dir: Path) -> dict:
    figure_id = figure["figure_id"]
    analysis = read_json(out_dir / figure["analysis_file"], {}) or {}
    checks = []

    def check(name, passed, detail=""):
        checks.append({"check": name, "pass": bool(passed), "detail": detail})

    check("r_analysis_status", analysis.get("status") == "ok", analysis.get("reason", ""))
    check("row_accounting", analysis.get("n_used") == preparation.get("n_used"),
          f"R n_used={analysis.get('n_used')} preparation n_used={preparation.get('n_used')}")
    check("sample_size_present", bool(analysis.get("n_used")), f"n={analysis.get('n_used')}")
    font = analysis.get("font") or {}
    if font.get("cjk"):
        family = str(analysis.get("font_family") or font.get("family") or "")
        check("cjk_font_family", family in {"PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei", "Arial Unicode MS"},
              f"CJK text requires a real CJK family; actual={family}")
    if analysis.get("status") == "ok" and preparation.get("n_used"):
        rows = read_tsv(out_dir / figure["data_file"])
        chart = figure["chart_type"]
        if chart == "bar_with_error_points" or (chart in {"comparison_auto", "boxplot_points", "violin_points"} and analysis.get("series") is not None):
            ok, issues = numeric_comparison(rows, analysis)
            check("independent_numeric_recheck", ok, "; ".join(issues))
        elif chart == "scatter_with_fit":
            ok, issues = numeric_scatter(rows, analysis)
            check("independent_numeric_recheck", ok, "; ".join(issues))
        elif chart == "correlation_heatmap":
            ok, issues = numeric_heatmap(rows, figure.get("variables") or [], analysis)
            check("independent_numeric_recheck", ok, "; ".join(issues))

    files = {}
    for extension in ("pdf", "png", "svg"):
        path = out_dir / f"figure_{figure['index']}.{extension}"
        files[extension] = {"file": path.name, "exists": path.exists(), "bytes": path.stat().st_size if path.exists() else 0,
                            "sha256": file_sha256(path) if path.exists() else None}
        check(f"export_{extension}", path.exists() and path.stat().st_size > 0, path.name)

    try:
        from easyplot_metadata import inspect_file
        for extension in ("pdf", "png", "svg"):
            path = out_dir / files[extension]["file"]
            if not path.exists():
                continue
            report = inspect_file(
                path, min_dpi=float(figure.get("dpi") or 600) if extension == "png" else None,
                target_width_mm=float(figure["width_mm"]), target_height_mm=float(figure["height_mm"]),
                expected_svg_text="editable" if extension == "svg" else None,
                require_embedded_fonts=extension == "pdf",
                size_tolerance_mm=0.36 if extension == "pdf" else 0.2,  # Cairo rounds pages to integer points
            )
            report["path"] = path.name
            files[extension]["metadata"] = report
            check(f"metadata_{extension}", not report["errors"] and not report["warnings"],
                  "; ".join(report["errors"] + report["warnings"]))
    except Exception as error:
        check("metadata_audit_available", False, str(error))

    png_path = out_dir / files["png"]["file"]
    if png_path.exists():
        try:
            margins = raster_margins(png_path, float(figure["width_mm"]), float(figure["height_mm"]))
            files["png"]["visual_margins"] = margins
            check("png_not_blank_and_margins", margins["pass"], json.dumps(margins, ensure_ascii=False))
        except Exception as error:
            check("raster_visual_audit", False, str(error))

    svg_path = out_dir / files["svg"]["file"]
    if svg_path.exists():
        try:
            import xml.etree.ElementTree as ET
            root = ET.parse(svg_path).getroot()
            rendered_text = "\n".join("".join(node.itertext()) for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "text")
            expected_labels = [str(value) for value in (figure.get("x_label"), figure.get("y_label")) if value]
            missing_labels = [label for label in expected_labels if label not in rendered_text]
            check("svg_expected_axis_labels", not missing_labels, "missing: " + ", ".join(missing_labels))
        except Exception as error:
            check("svg_expected_axis_labels", False, str(error))

    chart = figure.get("chart_type", "")
    if chart == "correlation_heatmap":
        heatmap = analysis.get("heatmap") or {}
        check("heatmap_diverging_colorbar", heatmap.get("colorbar") is True and heatmap.get("limits") == [-1, 1],
              "Pearson r uses a labelled diverging colorbar fixed to [-1,1]")
    else:
        groups = analysis.get("groups") or preparation.get("groups") or []
        palette = figure.get("palette") or DEFAULT_PALETTE
        colours = {str(group): palette[i % len(palette)] for i, group in enumerate(groups)} or {"data": palette[0]}
        geometry = "scatter" if chart == "scatter_with_fit" else "line" if chart == "line_series" else "box" if chart in {"comparison_auto", "boxplot_points", "violin_points"} else "bar"
        cues = {"markers": True} if geometry == "scatter" else {"markers": True, "linestyles": True} if geometry == "line" else {"position-labels": True, "outline": True}
        if len(groups) > 6:
            cues["facets"] = True
        try:
            palette_report = audit_palette(colours, geometry=geometry, cues=tuple(cues), foreground="#000000",
                                           foreground_placement="outside-marks")
            files["palette_audit"] = palette_report
            check("palette_context_screen", not palette_report["warnings"], "; ".join(palette_report["warnings"]))
        except Exception as error:
            check("palette_context_screen", False, str(error))

    check("no_silent_category_truncation", not figure.get("truncated_categories"), "all categories retained or explicitly aggregated")
    return {"figure_id": figure_id, "checks": checks, "all_pass": all(item["pass"] for item in checks), "files": files}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="run_plan.json")
    parser.add_argument("--out", default=".")
    args = parser.parse_args()
    out_dir = Path(args.out).resolve()
    plan = read_json(out_dir / args.plan, {}) if not Path(args.plan).is_absolute() else read_json(Path(args.plan), {})
    preparation = {item["figure_id"]: item for item in (read_json(out_dir / "preparation.json", {}) or {}).get("figures", [])}
    reports = [audit_figure(figure, preparation.get(figure["figure_id"], {}), out_dir) for figure in plan.get("figures", [])]
    (out_dir / "audit.json").write_text(json.dumps({"figures": reports}, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "figures": len(reports), "all_pass": all(item["all_pass"] for item in reports)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
