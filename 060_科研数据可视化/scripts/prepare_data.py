#!/usr/bin/env python3
"""Prepare clean TSV inputs and row-level lineage for the R/ggplot2 backend.

This script deliberately performs no plotting. It reads the raw snapshot and the
frozen run plan, applies declared missing/duplicate policies, and writes one TSV
per requested figure plus machine-readable quality and lineage evidence.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from collections import Counter
from pathlib import Path

BASE_COLUMNS = ["source_row_id", "x", "x_num", "y", "group", "unit_id", "label"]
DEFAULT_MISSING = {"", "na", "n/a", "nan", "null", "none", "."}


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False) + "\n", encoding="utf-8")


def write_tsv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in columns})


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in columns})


def finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def number_text(value) -> str:
    number = finite_number(value)
    return "" if number is None else format(number, ".17g")


def temporal_number(value):
    number = finite_number(value)
    if number is not None:
        return number
    if value is None:
        return None
    text = str(value).strip()
    try:
        if re_full_date(text):
            date = dt.date.fromisoformat(text[:10])
            return float((date - dt.date(1970, 1, 1)).days)
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
        epoch = dt.datetime(1970, 1, 1)
        return (parsed - epoch).total_seconds() / 86400.0
    except (ValueError, OverflowError, OSError):
        return None


def re_full_date(text: str) -> bool:
    if len(text) < 10:
        return False
    return text[4:5] == "-" and text[7:8] == "-" and text[:4].isdigit() and text[5:7].isdigit() and text[8:10].isdigit()


def declared_tokens(cleaning: dict, column: str) -> set[str]:
    tokens = set(DEFAULT_MISSING)
    tokens.update(str(x).strip().casefold() for x in cleaning.get("missing_tokens", []))
    column_rules = cleaning.get("column_missing", {}).get(column, [])
    if not isinstance(column_rules, list):
        column_rules = [column_rules]
    tokens.update(str(x).strip().casefold() for x in column_rules)
    return tokens


def load_raw(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        columns = list(reader.fieldnames or [])
        if not columns or len(columns) != len(set(columns)):
            raise ValueError("source_data.tsv requires unique, nonempty column names")
        rows = [dict(row) for row in reader]
    return columns, rows


def clean_rows(columns: list[str], raw_rows: list[dict], cleaning: dict) -> tuple[list[dict], dict]:
    missing_by_column = {column: 0 for column in columns}
    cleaned = []
    for index, raw in enumerate(raw_rows, 1):
        row = {"source_row_id": index}
        for column in columns:
            value = raw.get(column)
            text = "" if value is None else str(value).strip()
            if text.casefold() in declared_tokens(cleaning, column):
                value = None
                missing_by_column[column] += 1
            else:
                value = text
            row[column] = value
        cleaned.append(row)

    first_seen = {}
    duplicate_groups = Counter()
    for row in cleaned:
        key = tuple(row.get(column) for column in columns)
        if key in first_seen:
            row["duplicate_of"] = first_seen[key]
            row["is_duplicate"] = 1
            duplicate_groups[first_seen[key]] += 1
        else:
            row["duplicate_of"] = ""
            row["is_duplicate"] = 0
            first_seen[key] = row["source_row_id"]
    quality = {
        "n_source": len(raw_rows),
        "n_after_declared_duplicate_policy": 0,
        "n_duplicate_rows": sum(duplicate_groups.values()),
        "duplicate_rows": [
            {"source_row_id": row["source_row_id"], "duplicate_of": row["duplicate_of"]}
            for row in cleaned if row["is_duplicate"]
        ],
        "missing_by_column": missing_by_column,
        "missing_tokens": sorted(DEFAULT_MISSING),
        "column_missing": cleaning.get("column_missing", {}),
        "drop_duplicates": bool(cleaning.get("drop_duplicates", False)),
        "duplicate_policy": "drop exact cleaned-row duplicates" if cleaning.get("drop_duplicates") else "retain and report exact cleaned-row duplicates",
        "notes": [
            "Only declared sentinel values are treated as missing; numeric sentinels such as -999 require an explicit column rule.",
            "Duplicate identity is computed after declared missing-value normalization; near duplicates are not silently merged.",
        ],
    }
    return cleaned, quality


def relevant_columns(figure: dict) -> list[str]:
    chart = figure.get("chart_type")
    if chart == "correlation_heatmap":
        return list(figure.get("variables") or [])
    fields = [figure.get("x"), figure.get("y"), figure.get("group"), figure.get("unit_id"), figure.get("label")]
    return [str(field) for field in fields if field]


def prepare_figure(figure: dict, rows: list[dict], columns: list[str], out_dir: Path) -> dict:
    index = int(figure["index"])
    chart = figure["chart_type"]
    required = relevant_columns(figure)
    missing_columns = [column for column in required if column not in columns]
    data_path = out_dir / figure["data_file"]
    lineage_path = out_dir / f"figure_{index}_lineage.json"
    if missing_columns:
        write_tsv(data_path, BASE_COLUMNS, [])
        result = {
            "figure_id": figure["figure_id"], "status": "error", "reason": "missing columns: " + ", ".join(missing_columns),
            "n_input": 0, "n_used": 0, "n_excluded": len(rows), "missing_columns": missing_columns,
            "data_file": data_path.name, "lineage_file": lineage_path.name,
        }
        write_json(lineage_path, {"figure_id": figure["figure_id"], "rows": [], "excluded": []})
        return result

    active = [row for row in rows if not row.get("is_duplicate") or not figure.get("drop_duplicates", False)]
    output = []
    excluded = []
    numeric_invalid = Counter()
    numeric_missing = Counter()
    x_field, y_field = figure.get("x"), figure.get("y")
    group_field = figure.get("group")
    unit_field = figure.get("unit_id")
    label_field = figure.get("label")

    def exclude(row, reason):
        excluded.append({"source_row_id": row["source_row_id"], "reason": reason})

    for row in active:
        source_id = row["source_row_id"]
        if chart == "correlation_heatmap":
            variables = figure.get("variables") or []
            converted = {}
            n_present = 0
            for variable in variables:
                value = row.get(variable)
                number = finite_number(value)
                if number is None:
                    if value is None:
                        numeric_missing[variable] += 1
                    else:
                        numeric_invalid[variable] += 1
                    converted[variable] = ""
                else:
                    converted[variable] = format(number, ".17g")
                    n_present += 1
            if n_present < 2:
                exclude(row, "fewer than two finite heatmap variables")
                continue
            output.append({"source_row_id": source_id, **converted})
            continue

        raw_x = row.get(x_field) if x_field else None
        raw_y = row.get(y_field) if y_field else None
        if chart in {"scatter_with_fit", "line_series", "histogram"}:
            parser = temporal_number if chart == "line_series" else finite_number
            x_num = parser(raw_x)
        else:
            x_num = None
        y_num = finite_number(raw_y) if y_field else None

        if chart == "category_count":
            valid = raw_x is not None
            reason = "missing category" if not valid else ""
        elif chart == "correlation_heatmap":
            valid = True
            reason = ""
        else:
            x_valid = x_num is not None if chart in {"scatter_with_fit", "line_series", "histogram"} else raw_x is not None
            y_required = chart not in {"histogram"}
            y_valid = (not y_required) or y_num is not None
            group_valid = not group_field or row.get(group_field) is not None
            valid = x_valid and y_valid and group_valid
            reasons = []
            if not x_valid:
                reasons.append("missing/nonfinite x")
                if raw_x is not None:
                    numeric_invalid[str(x_field)] += 1
                else:
                    numeric_missing[str(x_field)] += 1
            if not y_valid:
                reasons.append("missing/nonfinite y")
                if raw_y is not None:
                    numeric_invalid[str(y_field)] += 1
                else:
                    numeric_missing[str(y_field)] += 1
            if not group_valid:
                reasons.append("missing declared group")
            reason = "; ".join(reasons)
        if not valid:
            exclude(row, reason)
            continue
        output.append({
            "source_row_id": source_id,
            "x": "" if raw_x is None else str(raw_x),
            "x_num": "" if x_num is None else format(x_num, ".17g"),
            "y": "" if y_num is None else format(y_num, ".17g"),
            "group": "" if not group_field or row.get(group_field) is None else str(row.get(group_field)),
            "unit_id": "" if not unit_field or row.get(unit_field) is None else str(row.get(unit_field)),
            "label": str(source_id) if not label_field or row.get(label_field) is None else str(row.get(label_field)),
        })

    out_columns = ["source_row_id"] + list(figure.get("variables") or []) if chart == "correlation_heatmap" else BASE_COLUMNS
    write_tsv(data_path, out_columns, output)
    write_csv(out_dir / f"figure_{index}.csv", out_columns, output)
    groups = sorted({str(row.get("group", "")) for row in output if row.get("group", "")})
    categories = sorted({str(row.get("x", "")) for row in output if row.get("x", "")})
    warnings = []
    if chart in {"bar_with_error_points", "comparison_auto", "stacked_bar"} and len(categories) > 8:
        warnings.append(f"{len(categories)} categories retained; use horizontal/faceted layout rather than truncation")
    if len(groups) > 6:
        warnings.append(f"{len(groups)} groups retained; colour must not be the only identity channel")
    lineage = {
        "figure_id": figure["figure_id"],
        "source_snapshot": "source_data.tsv",
        "rows": [
            {"source_row_id": row["source_row_id"], "x": row.get("x", ""), "x_num": row.get("x_num", ""),
             "y": row.get("y", ""), "group": row.get("group", "")}
            for row in output if chart != "correlation_heatmap"
        ] if chart != "correlation_heatmap" else [{"source_row_id": row["source_row_id"]} for row in output],
        "excluded": excluded,
    }
    write_json(lineage_path, lineage)
    return {
        "figure_id": figure["figure_id"], "status": "ready" if output else "empty",
        "reason": "" if output else "no plottable rows after declared rules",
        "n_input": len(active), "n_used": len(output), "n_excluded": len(excluded),
        "missing_columns": [], "groups": groups, "n_categories": len(categories),
        "numeric_invalid": dict(numeric_invalid), "numeric_missing": dict(numeric_missing),
        "data_file": data_path.name, "csv_file": f"figure_{index}.csv", "lineage_file": lineage_path.name, "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="run_plan.json")
    parser.add_argument("--out", default=".")
    args = parser.parse_args()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    columns, raw_rows = load_raw(out_dir / "source_data.tsv")
    cleaned, quality = clean_rows(columns, raw_rows, plan.get("cleaning", {}))
    active = [row for row in cleaned if not row["is_duplicate"] or not plan.get("cleaning", {}).get("drop_duplicates", False)]
    quality["n_after_declared_duplicate_policy"] = len(active)
    write_tsv(out_dir / "cleaned_data.tsv", ["source_row_id", "duplicate_of", "is_duplicate"] + columns, cleaned)
    write_json(out_dir / "data_quality.json", quality)
    reports = [prepare_figure(figure, cleaned, columns, out_dir) for figure in plan.get("figures", [])]
    write_json(out_dir / "preparation.json", {"figures": reports})
    print(json.dumps({"ok": True, "rows": len(cleaned), "figures": len(reports)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
