#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""综合层回归：最小示例 → synthesis_input → 一句综合稿 → merge_synthesis 校验。

不调用模型；用证据表第一行拼一句带引用的综合稿，验证：
  * 管线自动生成 work/synthesis_input.json；
  * merge_synthesis.py 接受合法引用并写出 review.md；
  * review.md 含被引 evidence_id，audit 记录引用覆盖。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
CASE = SKILL / "examples" / "min_example" / "input" / "case.json"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="synth-test-") as tmp:
        root = Path(tmp)
        work, out = root / "work", root / "out"
        proc = subprocess.run([sys.executable, "-B", str(HERE / "pipeline.py"),
                               "--case", str(CASE), "--work", str(work), "--out", str(out)],
                              capture_output=True, text=True, env=ENV, timeout=180)
        if proc.returncode != 0:
            print(proc.stdout + proc.stderr)
            return 1
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        rows = result.get("evidence_table") or result.get("evidence_matrix") or []
        if not rows:
            print("FAIL: 最小示例没有证据行")
            return 1
        row = rows[0]
        eid = str(row["evidence_id"])
        syn = {
            "title": "综合层回归（合成示例）",
            "method_note": "仅用于验证综合层，不代表任何正式案例结论。",
            "sections": [{"heading": "证据回链", "paragraphs": [
                {"text": f"证据句：{row.get('quote', '')} [{eid}]", "evidence_ids": [eid]}]}],
            "conflicts": [], "gaps": [], "limitations": "合成示例，仅验证引用与数字回查。",
        }
        syn_path = root / "synthesis.json"
        syn_path.write_text(json.dumps(syn, ensure_ascii=False), encoding="utf-8")
        review = out / "review.md"
        audit = out / "synthesis_audit.json"
        proc = subprocess.run([sys.executable, "-B", str(HERE / "merge_synthesis.py"),
                               "--result", str(out / "result.json"), "--synthesis", str(syn_path),
                               "--out", str(review), "--audit-out", str(audit)],
                              capture_output=True, text=True, env=ENV, timeout=120)
        if proc.returncode != 0:
            print(proc.stdout + proc.stderr)
            return 1
        if eid not in review.read_text(encoding="utf-8"):
            print("FAIL: review.md 未含被引 evidence_id")
            return 1
        ok = json.loads(audit.read_text(encoding="utf-8"))
        if eid not in ok.get("cited_evidence", []):
            print("FAIL: audit 未记录被引 evidence_id")
            return 1
        print(json.dumps({"ok": True, "evidence_id": eid, "review": str(review),
                          "cited": len(ok.get("cited_evidence", []))}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
