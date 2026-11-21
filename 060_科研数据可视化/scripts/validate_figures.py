#!/usr/bin/env python3
"""060-specific artifact gate; run after the shared contract validator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(result_path: Path) -> list[str]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    failures = []
    overall = (result.get("status") or {}).get("overall")
    figures_obj = result.get("figures") or {}
    figures = figures_obj.get("figures")
    if not isinstance(figures, list):
        return ["figures.figures 缺失或不是列表"]
    if figures_obj.get("n_delivered") != sum(item.get("status") == "delivered" for item in figures):
        failures.append("n_delivered 与逐图状态不一致")
    status_view = result.get("状态说明")
    if isinstance(status_view, dict) and status_view.get("overall") != overall:
        failures.append("状态说明.overall 与 status.overall 不一致")
    for index, figure in enumerate(figures):
        fid = str(figure.get("figure_id") or index + 1)
        if figure.get("status") != "delivered":
            if overall == "complete":
                failures.append(f"{fid}: complete 中仍有 {figure.get('status')}")
            continue
        if not (figure.get("caption") or {}).get("standalone_text"):
            failures.append(f"{fid}: 缺独立图注")
        if not (figure.get("qa") or {}).get("all_pass"):
            failures.append(f"{fid}: QA 未全过")
        if not (figure.get("reproduce") or {}).get("end_to_end_sha_match"):
            failures.append(f"{fid}: 缺端到端重跑一致证据")
        for extension in ("pdf", "png", "svg"):
            entry = (figure.get("files") or {}).get(extension) or {}
            if not entry.get("file") or not entry.get("exists") or not (result_path.parent / entry["file"]).is_file():
                failures.append(f"{fid}: {extension} 未实际落盘")
        for resource, filename in (figure.get("resources") or {}).items():
            if not filename:
                continue
            if not (result_path.parent / filename).is_file():
                failures.append(f"{fid}: 资源 {resource}={filename} 未实际落盘")
    evidence = result.get("evidence_map") or {}
    snapshot = (evidence.get("source") or {}).get("snapshot")
    if snapshot and not (result_path.parent / snapshot).is_file():
        failures.append("原始数据快照缺失")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", default="/app/output/result.json")
    args = parser.parse_args()
    path = Path(args.result)
    try:
        failures = validate(path)
    except Exception as error:
        failures = [str(error)]
    for failure in failures:
        print(f"[FAIL] {failure}")
    print("PASS" if not failures else f"FAIL ({len(failures)})")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
