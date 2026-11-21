#!/usr/bin/env python3
"""verify.py —— 77 个 Skill 的统一验收门（结构 + 资源预算 + 可运行 + 契约 + 幂等）。

用法：
    python3 answer/_shared/verify.py                # 全量
    python3 answer/_shared/verify.py --only 001 015 # 只查指定题号
    python3 answer/_shared/verify.py --structure    # 只查结构与预算，不跑代码

判定项（任一 FAIL 该题即不通过）：
  1. SKILL.md 存在，frontmatter 有合法 name（^[a-z][a-z0-9-]{1,63}$）与 description（1–300 字符），必要章节齐全
  2. scripts/pipeline.py、scripts/kit.py、scripts/validate.py 存在；Python 可静态解析，共享副本未漂移
  3. rules/ 至少 1 个文件；examples/min_example/input/case.json 存在且使用 EXAMPLE-* ID
  4. 文本文件 ≤50 个、单文件 ≤500 KB、合计 ≤2 MB、无软链接
  5. `pipeline.py --case <示例>` 退出码 0 且生成 result.json
  6. `validate.py` 退出码 0（契约、case_id、evidence、status 及状态一致性全通过）
  7. 与 examples/min_example/expected/result.json 一致（忽略 generated_at）
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # answer/
SKIP = {"_shared"}
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 50, 500 * 1024, 2 * 1024 * 1024


def parse_frontmatter(text: str):
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = text[3:end]
    meta = {}
    for line in block.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta


def check_structure(d: Path) -> list:
    fails = []
    skill_md = d / "SKILL.md"
    if not skill_md.exists():
        fails.append("缺 SKILL.md")
    else:
        meta = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        if not meta:
            fails.append("SKILL.md 无合法 frontmatter")
        else:
            name, desc = meta.get("name", ""), meta.get("description", "")
            if not NAME_RE.match(name or ""):
                fails.append(f"name 非法：{name!r}")
            if not (1 <= len(desc) <= 300):
                fails.append(f"description 长度 {len(desc)} 不在 1–300")
        body = skill_md.read_text(encoding="utf-8", errors="replace")
        if len(body.splitlines()) < 40:
            fails.append(f"SKILL.md 过短（{len(body.splitlines())} 行）")
        required_sections = ("何时使用", "输入", "流程", "输出", "异常处理", "自查", "最小调用示例")
        missing_sections = [s for s in required_sections if s not in body]
        if missing_sections:
            fails.append(f"SKILL.md 缺章节：{missing_sections}")
        for ref in sorted(set(re.findall(r"`((?:rules|scripts|examples)/[^`\s]+)`", body))):
            clean = ref.split("#", 1)[0].rstrip("，。；、）)")
            # 兼容 rules/file.json.some.path 的 JSON 路径写法，同时抓住漏写 .json。
            if ".json." in clean:
                clean = clean.split(".json.", 1)[0] + ".json"
            if any(ch in clean for ch in "*{}<>") or clean.endswith("/"):
                continue
            if not (d / clean).exists():
                fails.append(f"SKILL.md 本地资源引用不存在：{ref}")

    for rel in ("scripts/pipeline.py", "scripts/kit.py", "scripts/validate.py"):
        if not (d / rel).exists():
            fails.append(f"缺 {rel}")
    for rel in ("scripts/kit.py", "scripts/validate.py", "scripts/office_min.py"):
        local = d / rel
        shared = ROOT / "_shared" / Path(rel).name
        if local.exists() and shared.exists() and local.read_bytes() != shared.read_bytes():
            fails.append(f"{rel} 与 _shared 副本不一致，可能漏同步修复")
    for source in d.glob("scripts/*.py"):
        try:
            ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (SyntaxError, UnicodeDecodeError) as exc:
            fails.append(f"Python 静态解析失败：{source.relative_to(d)}: {exc}")
    if not list((d / "rules").glob("*")):
        fails.append("rules/ 为空")
    if not (d / "examples/min_example/input/case.json").exists():
        fails.append("缺 examples/min_example/input/case.json")
    if not (d / "examples/min_example/expected/result.json").exists():
        fails.append("缺 examples/min_example/expected/result.json")
    example_case = d / "examples/min_example/input/case.json"
    if example_case.exists():
        try:
            raw_case = json.loads(example_case.read_text(encoding="utf-8"))
            if isinstance(raw_case, dict) and isinstance(raw_case.get("case"), dict):
                raw_case = raw_case["case"]
            example_id = str(raw_case.get("case_id") or raw_case.get("caseId") or "")
            if not example_id.startswith("EXAMPLE-"):
                fails.append(f"最小示例 case_id 未用 EXAMPLE-* 前缀：{example_id!r}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            fails.append(f"最小示例 case.json 不可解析：{exc}")

    files, total = [], 0
    for p in d.rglob("*"):
        if p.is_symlink():
            fails.append(f"存在软链接：{p.relative_to(d)}")
            continue
        if p.is_file():
            size = p.stat().st_size
            files.append((p, size))
            total += size
            if size > MAX_FILE_BYTES:
                fails.append(f"单文件超 500KB：{p.relative_to(d)} ({size // 1024}KB)")
    if len(files) > MAX_FILES:
        fails.append(f"文件数 {len(files)} 超 50")
    if total > MAX_TOTAL_BYTES:
        fails.append(f"合计 {total / 1024 / 1024:.2f}MB 超 2MB")
    return fails


def run_case(d: Path) -> list:
    fails = []
    case = d / "examples/min_example/input/case.json"
    expected = d / "examples/min_example/expected/result.json"
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [sys.executable, "scripts/pipeline.py", "--case", str(case), "--out", tmp],
            cwd=str(d), capture_output=True, text=True, timeout=300,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if proc.returncode != 0:
            return [f"pipeline 退出码 {proc.returncode}: {(proc.stderr or proc.stdout).strip().splitlines()[-1:] or ''}"]
        out = Path(tmp) / "result.json"
        if not out.exists():
            return ["pipeline 未生成 result.json"]
        val = subprocess.run(
            [sys.executable, "scripts/validate.py", "--case", str(case), "--result", str(out)],
            cwd=str(d), capture_output=True, text=True, timeout=300,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if val.returncode != 0:
            tail = [l for l in (val.stdout or "").splitlines() if l.startswith("[FAIL")][:3]
            fails.append(f"validate FAIL: {tail or val.stderr.strip()[:160]}")
        try:
            got = json.loads(out.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return fails + [f"result.json 不可解析：{exc}"]
        if expected.exists():
            try:
                exp = json.loads(expected.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                return fails + [f"expected 不可解析：{exc}"]
            got.pop("generated_at", None)
            exp.pop("generated_at", None)
            if json.dumps(got, ensure_ascii=False, sort_keys=True) != json.dumps(exp, ensure_ascii=False, sort_keys=True):
                fails.append("结果与 expected 不一致（不幂等或改过规则未重生成基线）")
        status = (got.get("status") or {}).get("overall")
        if status not in ("complete", "partial", "empty", "input_error"):
            fails.append(f"status.overall 非法：{status}")
        if status == "input_error":
            fails.append("示例运行落到 input_error")
    return fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="77 个 skill 的验收门")
    ap.add_argument("--only", nargs="*", default=None, help="只查这些题号，如 001 015")
    ap.add_argument("--structure", action="store_true", help="只查结构与预算")
    args = ap.parse_args(argv)

    dirs = sorted(p for p in ROOT.iterdir() if p.is_dir() and p.name[:3].isdigit() and p.name not in SKIP)
    if args.only:
        want = {x.zfill(3) for x in args.only}
        dirs = [p for p in dirs if p.name[:3] in want]

    rows, bad = [], 0
    for d in dirs:
        fails = check_structure(d)
        if not fails and not args.structure:
            fails += run_case(d)
        rows.append((d.name, fails))
        if fails:
            bad += 1

    for name, fails in rows:
        if fails:
            print(f"[FAIL] {name}")
            for f in fails[:6]:
                print(f"        - {f}")
        else:
            print(f"[ OK ] {name}")
    print("-" * 72)
    print(f"合计 {len(rows)} 题：通过 {len(rows) - bad}，失败 {bad}")
    missing_github = []
    for d in dirs:
        detail = ROOT.parent / "77_challenges" / "challenges" / d.name / "detail.md"
        required = detail.exists() and "GITHUB_URL.txt" in detail.read_text(encoding="utf-8", errors="replace")
        if required and not (d / "GITHUB_URL.txt").exists():
            missing_github.append(d.name)
    if missing_github:
        print(f"[WARN] {len(missing_github)} 道明确要求公开链接的题缺 GITHUB_URL.txt；须填写真实地址，不能以占位符代交")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
