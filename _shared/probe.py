#!/usr/bin/env python3
"""对最小示例之外的通用输入外形做轻量稳健性探针。

- empty：只保留 case_id、task_instruction、output_contract；必须诚实降级，不能 complete。
- wrapped：把原示例包进 {"case": ...}，并复制示例材料以保持相对路径；状态、case_id 与契约校验应与原示例一致。

用法：python3 answer/_shared/probe.py [--only 001 021]
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
STATUS = {"complete", "partial", "empty", "input_error"}


def run_probe(directory: Path, kind: str, case: dict, expected_status: str = "", material_source: Path | None = None) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        case_dir = Path(tmp) / "input"
        if material_source is not None:
            shutil.copytree(material_source, case_dir)
        else:
            case_dir.mkdir()
        case_path = case_dir / "case.json"
        case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
        out = Path(tmp) / "out"
        try:
            command = [sys.executable, "scripts/pipeline.py", "--case", str(case_path), "--out", str(out)]
            proc = subprocess.run(
                command,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=40,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            result_path = out / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            return f"{kind}: {type(exc).__name__}: {exc}"
        status = (result or {}).get("status", {}).get("overall")
        if proc.returncode != 0 or not isinstance(result, dict) or status not in STATUS:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:]
            return f"{kind}: rc={proc.returncode}, status={status!r}, {tail}"
        raw_case = case.get("case", case)
        expected_case_id = str(raw_case.get("case_id") or raw_case.get("caseId") or "")
        if result.get("case_id") != expected_case_id:
            return f"{kind}: case_id 不一致：{result.get('case_id')!r} != {expected_case_id!r}"
        if kind == "empty" and status == "complete":
            return "empty: 空材料被错误标为 complete"
        if kind == "wrapped" and expected_status and status != expected_status:
            return f"wrapped: 包装改变结果状态：{status!r} != {expected_status!r}"
        validation = subprocess.run(
            [sys.executable, "scripts/validate.py", "--case", str(case_path), "--result", str(result_path)],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if validation.returncode != 0:
            tail = [line for line in validation.stdout.splitlines() if line.startswith("[FAIL")][:3]
            return f"{kind}: validate FAIL: {tail or validation.stderr.strip()[:200]}"
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    want = {x.zfill(3) for x in args.only} if args.only else None
    dirs = sorted(p for p in ROOT.glob("[0-9][0-9][0-9]_*") if p.is_dir())
    if want:
        dirs = [p for p in dirs if p.name[:3] in want]
    failures = []
    probes = 0
    for directory in dirs:
        source = directory / "examples/min_example/input/case.json"
        original = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(original, dict) and isinstance(original.get("case"), dict):
            original = original["case"]
        expected_path = directory / "examples/min_example/expected/result.json"
        expected_status = ""
        if expected_path.exists():
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
            expected_status = (expected.get("status") or {}).get("overall", "")
        cases = {
            "empty": {
                "case_id": "EXAMPLE-EMPTY",
                "task_instruction": original.get("task_instruction", ""),
                "output_contract": original.get("output_contract", {}),
            },
            "wrapped": {"case": original},
        }
        for kind, case in cases.items():
            probes += 1
            error = run_probe(
                directory,
                kind,
                case,
                expected_status=expected_status,
                material_source=source.parent if kind == "wrapped" else None,
            )
            if error:
                failures.append((directory.name, error))
                print(f"[FAIL] {directory.name}: {error}")
    print(f"探针 {probes} 项：通过 {probes - len(failures)}，失败 {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
