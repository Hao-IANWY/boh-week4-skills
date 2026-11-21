#!/usr/bin/env python3
"""compact_sources.py —— 把调研用 sources_*.json 压到提交预算内（单文件 ≤500KB、合计 ≤2MB）。

只裁"冗长描述"这类展示字段，不动 name/url/stars/license/lang/pushed_at/via/seed 等事实字段。
用法：python3 answer/_shared/compact_sources.py [--dry]
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESC_MAX, TOPIC_MAX, SH_DESC_MAX = 220, 6, 320


def clip(text, n):
    s = (text or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def compact_gh(path: Path) -> tuple:
    d = json.loads(path.read_text(encoding="utf-8"))
    out = []
    seen = set()
    for c in d.get("candidates", []):
        fn = c.get("full_name", "")
        if not fn or fn in seen:
            continue
        seen.add(fn)
        out.append({
            "full_name": fn,
            "url": c.get("url", ""),
            "stars": c.get("stars", 0),
            "desc": clip(c.get("desc"), DESC_MAX),
            "topics": list(c.get("topics") or [])[:TOPIC_MAX],
            "lang": c.get("lang"),
            "license": c.get("license"),
            "pushed_at": c.get("pushed_at"),
            "via": list(c.get("via") or [])[:3],
            "seed": bool(c.get("seed", False)),
        })
    d["candidates"] = out
    text = json.dumps(d, ensure_ascii=False, indent=1) + "\n"
    return text, len(out)


def compact_sh(path: Path) -> tuple:
    d = json.loads(path.read_text(encoding="utf-8"))
    out, seen = [], set()
    for s in d.get("skills", []):
        key = s.get("slug") or s.get("id") or (s.get("name", "") + s.get("author", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": s.get("name", ""),
            "slug": s.get("slug", ""),
            "author": s.get("author", ""),
            "category": s.get("category", ""),
            "description": clip(s.get("description"), SH_DESC_MAX),
            "description_zh": clip(s.get("description_zh"), SH_DESC_MAX) if s.get("description_zh") else None,
            "repo_url": s.get("repo_url", ""),
            "github_stars": s.get("github_stars"),
        })
    d["skills"] = out
    text = json.dumps(d, ensure_ascii=False, indent=1) + "\n"
    return text, len(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    before = after = 0
    for p in sorted(ROOT.glob("*/sources_github.json")):
        size = p.stat().st_size
        text, n = compact_gh(p)
        if not args.dry:
            p.write_text(text, encoding="utf-8")
        before += size
        after += len(text.encode())
    for p in sorted(ROOT.glob("*/sources_skillhub.json")):
        size = p.stat().st_size
        text, n = compact_sh(p)
        if not args.dry:
            p.write_text(text, encoding="utf-8")
        before += size
        after += len(text.encode())
    print(f"sources 体积 {before/1024/1024:.2f}MB -> {after/1024/1024:.2f}MB")
    worst = sorted(((p.stat().st_size, p) for p in ROOT.glob("*/sources_*.json")), reverse=True)[:3]
    for size, p in worst:
        print(f"  {size/1024:7.1f}KB  {p.parent.name}/{p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
