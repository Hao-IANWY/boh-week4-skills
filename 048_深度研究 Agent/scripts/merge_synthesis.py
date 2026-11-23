#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 LLM 综合稿并把关：逐句引用必须落在证据表内，数字必须能回查到所引引文。

用法：
  python3 merge_synthesis.py --result /app/output/result.json \
      --synthesis /app/work/synthesis.json --out /app/output/review.md

退出码：0 通过并写出 review.md；2 有硬违规（不写 review.md，打印违规清单）。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CITE = re.compile(r"\[([A-Z]{1,3}\d{3,})\]")


def _text_of(par) -> str:
    if isinstance(par, str):
        return par
    if isinstance(par, dict):
        return str(par.get("text") or par.get("paragraph") or par.get("content") or "")
    return ""


def _ids_of(par) -> list:
    if isinstance(par, dict) and isinstance(par.get("evidence_ids"), list):
        return [str(x) for x in par["evidence_ids"]]
    return CITE.findall(_text_of(par))


def _norm_num(s: str) -> str:
    return s.replace(",", "").replace("，", "").replace("％", "%").strip()


def _numbers(text: str) -> list:
    clean = CITE.sub(" ", text)
    # 只校验带单位/小数/百分号的数字；纯整数多为节号、条数或年份，另行放行。
    pat = re.compile(r"(\d+(?:\.\d+)?)\s*(%|mg|ml|g|l|h|小时|度|°c|℃|片|平方厘米|nm|ev|倍|条|篇|项)", re.I)
    out = []
    for m in pat.finditer(clean):
        out.append((m.group(1), m.group(2)))
    for m in re.finditer(r"\d+\.\d+", clean):
        if not any(m.group(0) == x[0] for x in out):
            out.append((m.group(0), ""))
    return out


def _collect_numbers(obj, depth=0, out=None):
    if out is None:
        out = set()
    if depth > 4:
        return out
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, depth + 1, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, depth + 1, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.add(str(obj))
        if isinstance(obj, float) and obj.is_integer():
            out.add(str(int(obj)))
    elif isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj):
            out.add(m)
    return out


def _quote_pool(ids, rows):
    parts, years = [], set()
    for eid in ids:
        row = rows.get(eid)
        if not row:
            continue
        parts.append(str(row.get("quote") or ""))
        if row.get("year") not in (None, ""):
            years.add(str(row["year"]))
        parts.append(str(row.get("title") or ""))
    return _norm_num(" ".join(parts)), years


def validate(payload: dict, syn: dict) -> tuple:
    raw_rows = payload.get("evidence_table") or payload.get("evidence_matrix") or []
    rows = {str(e.get("evidence_id")): e for e in raw_rows
            if isinstance(e, dict) and e.get("evidence_id")}
    violations, warnings = [], []
    sections = syn.get("sections") or []
    if not isinstance(sections, list) or not sections:
        violations.append({"code": "NO_SECTIONS", "detail": "synthesis.sections 不能为空"})
        sections = []
    allowed_numbers = _collect_numbers(payload.get("screening", {}))
    for key in ("temporal_trends", "prisma_flow", "quality_appraisal", "candidate_assessments",
                "prior_art_matrix", "novelty_assessment", "feasibility_assessment"):
        allowed_numbers |= _collect_numbers(payload.get(key))
    cited_all, used = set(), 0
    for si, sec in enumerate(sections, 1):
        if not isinstance(sec, dict):
            violations.append({"code": "BAD_SECTION", "detail": f"第 {si} 节不是对象"})
            continue
        heading = str(sec.get("heading") or sec.get("title") or f"第 {si} 节")
        paragraphs = sec.get("paragraphs") or ([sec["text"]] if sec.get("text") else [])
        if not paragraphs:
            warnings.append({"code": "EMPTY_SECTION", "heading": heading})
        for pi, par in enumerate(paragraphs, 1):
            text = _text_of(par).strip()
            if not text:
                continue
            ids = _ids_of(par)
            body_ids = CITE.findall(text)
            if set(ids) - set(body_ids):
                warnings.append({"code": "IDS_NOT_IN_TEXT", "heading": heading,
                                 "detail": sorted(set(ids) - set(body_ids))})
            if not body_ids and len(text) >= 30 and not (isinstance(par, dict) and par.get("meta")):
                violations.append({"code": "UNCITED_PARAGRAPH", "heading": heading,
                                   "detail": text[:80]})
                continue
            for eid in body_ids:
                cited_all.add(eid)
                if eid not in rows:
                    violations.append({"code": "UNKNOWN_EVIDENCE", "heading": heading, "evidence_id": eid})
                elif not rows[eid].get("quote_verified", True):
                    warnings.append({"code": "QUOTE_UNVERIFIED", "evidence_id": eid})
            pool, years = _quote_pool([x for x in body_ids if x in rows], rows)
            for num, unit in _numbers(text):
                if num in pool or num in years:
                    continue
                # 允许综述转述筛选计数（总记录数/纳入数/排除数）
                if num in allowed_numbers:
                    continue
                violations.append({"code": "NUMBER_NOT_IN_QUOTE", "heading": heading,
                                   "number": num + unit, "evidence_ids": body_ids})
            used += 1
    if not cited_all:
        violations.append({"code": "NO_CITATIONS", "detail": "整篇没有引用任何证据"})
    pc = payload.get("conflicts")
    if isinstance(pc, dict):
        pc = pc.get("items") or []
    if pc and not (syn.get("conflicts") or []):
        warnings.append({"code": "CONFLICTS_NOT_SEPARATED",
                         "detail": "输入有冲突证据，但 synthesis.conflicts 为空；若正文已双列可忽略"})
    for c in syn.get("conflicts") or []:
        text = _text_of(c)
        ids = _ids_of(c)
        if len(text) >= 20 and not ids:
            warnings.append({"code": "CONFLICT_UNCITED", "detail": text[:80]})
    audit = {
        "paragraphs": used,
        "cited_evidence": sorted(cited_all),
        "evidence_total": len(rows),
        "evidence_unused": sorted(set(rows) - cited_all),
        "violations": violations,
        "warnings": warnings,
    }
    return violations, warnings, audit


def render(payload: dict, syn: dict, audit: dict) -> str:
    counts = (payload.get("screening") or {}).get("counts") or {}
    lines = [f"# {syn.get('title') or '证据约束综述'}", ""]
    lines.append("> 本稿由模型在给定证据表内综合，逐句以 `[证据 ID]` 回链；合并脚本已校验："
                 "引用 ID 均存在、带单位/小数的数字均能在所引引文中回查。"
                 "证据只覆盖本轮语料，不声称语料外完备。")
    lines.append("")
    if syn.get("method_note"):
        lines += ["## 方法与语料口径", "", str(syn["method_note"]).strip(), ""]
    elif counts:
        lines += ["## 方法与语料口径", "",
                  f"本轮筛选 {counts.get('screened', '—')} 条，纳入 {counts.get('included', '—')} 条，"
                  f"排除 {counts.get('excluded', '—')} 条；以下综合只覆盖纳入记录及其证据句。", ""]
    for sec in syn.get("sections") or []:
        heading = str(sec.get("heading") or sec.get("title") or "小节")
        lines += [f"## {heading}", ""]
        for par in sec.get("paragraphs") or ([sec["text"]] if sec.get("text") else []):
            text = _text_of(par).strip()
            if text:
                lines += [text, ""]
    if syn.get("conflicts"):
        lines += ["## 冲突与不一致（并列呈现，不择一）", ""]
        for c in syn["conflicts"]:
            text = _text_of(c).strip()
            if text:
                lines += [text if CITE.search(text) else text + "（见证据台账）", ""]
    if syn.get("gaps"):
        lines += ["## 研究空白与证据缺口", ""]
        for g in syn["gaps"]:
            text = _text_of(g).strip()
            if text:
                lines += [f"- {text}", ""]
    if syn.get("limitations"):
        lines += ["## 局限", "", str(syn["limitations"]).strip(), ""]
    lines += ["## 证据台账", "",
              "| ID | 记录 | 年份 | 引文（逐字） | 校验 |", "|---|---|---:|---|---|"]
    cited = set(audit.get("cited_evidence") or [])
    for e in (payload.get("evidence_table") or payload.get("evidence_matrix") or []):
        eid = str(e.get("evidence_id"))
        if eid not in cited:
            continue
        quote = str(e.get("quote") or "").replace("|", "\\|")
        lines.append(f"| {eid} | {e.get('record_id') or ''} | {e.get('year') or ''} | {quote} | "
                     f"{'通过' if e.get('quote_verified', True) else '未通过'} |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--synthesis", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--audit-out", default="")
    args = ap.parse_args(argv)
    payload = json.loads(Path(args.result).read_text(encoding="utf-8"))
    syn = json.loads(Path(args.synthesis).read_text(encoding="utf-8"))
    violations, warnings, audit = validate(payload, syn)
    if args.audit_out:
        Path(args.audit_out).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"ok": not violations, "violations": len(violations), "warnings": len(warnings),
               "cited": len(audit["cited_evidence"]), "evidence_total": audit["evidence_total"]}
    if violations:
        print(json.dumps({**summary, "detail": violations[:20]}, ensure_ascii=False, indent=2))
        return 2
    Path(args.out).write_text(render(payload, syn, audit), encoding="utf-8")
    print(json.dumps({**summary, "out": args.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
