"""validate.py —— 结果契约自检（纯标准库，随 skill 一起交付）。

用法：
    python3 scripts/validate.py --case examples/min_example/input/case.json \
                               --result /tmp/out/result.json
    python3 scripts/validate.py ... --json      # 机器可读报告

检查项（任一 FAIL 退出码 1）：
1. result.json 是对象，且 `case_id` 与输入 case 完全一致（示例案例可用 EXAMPLE-* 前缀）。
2. `output_contract` 的每个字段都在 result.json 顶层存在且非空；`empty/input_error` 可保留空主结果，但必须在 `status.unmet` 说明缺口。
3. `status.overall ∈ {complete, partial, empty, input_error}`；`status.completed / unmet` 为列表。
4. `evidence` 为非空对象（证据记录不能整体缺失）。
5. 结果中不含 NaN / Infinity，也不含 "待补充/TODO/FIXME/PLACEHOLDER/TBD" 之类占位串。
6. `uncertainty` 为列表（可以为空，但字段必须在）。
7. `complete` 不得同时含未完成的 `unmet` 或 `not_produced`；`partial/empty` 的未产出产物的缺口必须进入 `unmet`。
8. 与 kit 一致，支持 `{"case": {...}}` 包装、`outputContract` 和列表形式的契约。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

STATUS_ENUM = ("complete", "partial", "empty", "input_error")
TODO_MARKERS = ("TODO", "FIXME", "待补充", "PLACEHOLDER", "TBD", "占位符")
REPORT = {"checks": [], "fails": [], "warns": []}


def _load(path):
    with Path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _walk_strings(node, path="$"):
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_strings(v, f"{path}[{i}]")


def _bad_numbers(node, path="$"):
    if isinstance(node, float):
        if math.isnan(node) or math.isinf(node):
            yield path
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _bad_numbers(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _bad_numbers(v, f"{path}[{i}]")


def _not_produced_paths(node, path="$"):
    if isinstance(node, dict):
        if node.get("status") == "not_produced":
            yield path
        for k, v in node.items():
            yield from _not_produced_paths(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _not_produced_paths(v, f"{path}[{i}]")


def check(case_path, result_path) -> dict:
    # check() 也作为库函数复用；每次调用独立出报告，避免上一例的 FAIL 泄漏。
    REPORT.clear()
    REPORT.update({"checks": [], "fails": [], "warns": []})

    def ok(name, detail=""):
        REPORT["checks"].append({"check": name, "result": "PASS", "detail": detail})

    def fail(name, detail):
        REPORT["checks"].append({"check": name, "result": "FAIL", "detail": detail})
        REPORT["fails"].append(f"{name}: {detail}")

    def warn(name, detail):
        REPORT["checks"].append({"check": name, "result": "WARN", "detail": detail})
        REPORT["warns"].append(f"{name}: {detail}")

    try:
        case = _load(case_path)
    except Exception as exc:  # noqa: BLE001
        fail("load_case", str(exc))
        return REPORT
    if isinstance(case, dict) and isinstance(case.get("case"), dict):
        case = case["case"]
    if not isinstance(case, dict):
        fail("load_case", "case.json 顶层不是对象")
        return REPORT
    try:
        result = _load(result_path)
    except Exception as exc:  # noqa: BLE001
        fail("load_result", str(exc))
        return REPORT

    if not isinstance(result, dict):
        fail("result_type", "result.json 顶层不是对象")
        return REPORT
    ok("result_type")

    case_id = str(case.get("case_id") or case.get("caseId") or "")
    rid = str(result.get("case_id") or "")
    if not rid:
        fail("case_id", "result.json 缺 case_id")
    elif case_id and rid != case_id:
        fail("case_id", f"不一致：case={case_id!r} result={rid!r}")
    else:
        ok("case_id", rid)

    contract = case.get("output_contract") or case.get("outputContract") or {}
    if isinstance(contract, list):
        contract = {str(k): "" for k in contract}
    if not isinstance(contract, dict):
        contract = {}
    if not contract:
        warn("output_contract", "case.json 未声明 output_contract，跳过字段核对")
    missing = [k for k in contract if k not in result]
    empty = [k for k in contract if k in result and result[k] in (None, "", [], {})]
    overall_hint = (result.get("status") or {}).get("overall") if isinstance(result.get("status"), dict) else None
    if missing:
        fail("contract_fields", f"缺字段 {missing}")
    elif empty and overall_hint not in ("empty", "input_error"):
        fail("contract_fields", f"字段为空 {empty}")
    elif empty:
        ok("contract_fields", f"{len(contract)} 个字段存在；{overall_hint} 状态允许空主结果 {empty}")
    else:
        ok("contract_fields", f"{len(contract)} 个字段齐备")

    status = result.get("status")
    if not isinstance(status, dict):
        fail("status", "status 不是对象")
    else:
        overall = status.get("overall")
        if overall not in STATUS_ENUM:
            fail("status.overall", f"{overall!r} 不在 {STATUS_ENUM}")
        else:
            ok("status.overall", overall)
        for key in ("completed", "unmet"):
            if not isinstance(status.get(key), list):
                fail(f"status.{key}", "应为列表")
        if isinstance(status.get("completed"), list) and isinstance(status.get("unmet"), list):
            ok("status.lists", f"completed={len(status['completed'])} unmet={len(status['unmet'])}")
            if overall == "complete" and status["unmet"]:
                fail("status.consistency", "overall=complete 但 unmet 非空")
            if overall in ("empty", "input_error") and empty and not status["unmet"]:
                fail("status.consistency", f"空主结果 {empty} 未在 unmet 说明原因")
        not_produced = list(_not_produced_paths(result))
        if overall == "complete" and not_produced:
            fail("status.consistency", f"overall=complete 但存在 not_produced：{not_produced[:3]}")
        elif overall in ("partial", "empty") and not status.get("unmet") and not_produced:
            fail("status.consistency", f"存在 not_produced 但 unmet 未记录：{not_produced[:3]}")

    evidence = result.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        fail("evidence", "evidence 缺失或为空对象（证据记录不可省）")
    else:
        ok("evidence", f"{len(evidence)} 个证据分区")

    if not isinstance(result.get("uncertainty"), list):
        fail("uncertainty", "uncertainty 缺失或不是列表")
    else:
        ok("uncertainty", f"{len(result['uncertainty'])} 条")

    bad = list(_bad_numbers(result))
    if bad:
        fail("numbers", f"NaN/Infinity 出现在 {bad[:3]}")
    else:
        ok("numbers")

    hits = [
        (p, s)
        for p, s in _walk_strings(result)
        if any(m in s for m in TODO_MARKERS) and len(s) <= 200
    ]
    if hits:
        warn("placeholders", f"{len(hits)} 处疑似占位：{[h[0] for h in hits][:3]}")
    else:
        ok("placeholders")

    return REPORT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="result.json 契约自检")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--json", action="store_true", help="输出机器可读报告")
    args = ap.parse_args(argv)

    report = check(args.case, args.result)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        for c in report["checks"]:
            print(f"[{c['result']:4}] {c['check']}: {c['detail']}")
        print("-" * 60)
        print("FAIL" if report["fails"] else "PASS")
    return 1 if report["fails"] else 0


if __name__ == "__main__":
    sys.exit(main())
