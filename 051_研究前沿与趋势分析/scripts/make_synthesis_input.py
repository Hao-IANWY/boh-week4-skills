#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 result.json 压缩成 LLM 可读的“综合输入”：只含证据表、记录、口径与冲突/空白。

不生成任何结论；综合由调用方（Harness 中的模型）完成，随后用 merge_synthesis.py 校验。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _first(payload, *keys, default=None):
    for k in keys:
        v = payload.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _question(payload):
    q = payload.get("question")
    if isinstance(q, dict):
        return q.get("text") or q.get("research_question") or q.get("value")
    if isinstance(q, str):
        return q
    return _first(payload, "research_question", "task", "objective", default="")


def _items(value):
    if isinstance(value, dict):
        for k in ("items", "conflicts", "rows", "records", "list"):
            v = value.get(k)
            if isinstance(v, list):
                return v
        return []
    return value if isinstance(value, list) else []


def compact(payload: dict) -> dict:
    scr = payload.get("screening") if isinstance(payload.get("screening"), dict) else {}
    raw_ev = _first(payload, "evidence_table", "evidence_rows", "evidence_matrix", default=None)
    if raw_ev is None:
        evsec = payload.get("evidence")
        raw_ev = evsec.get("table") if isinstance(evsec, dict) else (evsec if isinstance(evsec, list) else [])
    ev = []
    for e in raw_ev or []:
        if not isinstance(e, dict):
            continue
        ev.append({k: e.get(k) for k in
                   ("evidence_id", "record_id", "title", "year", "venue", "doi", "quote",
                    "quote_field", "quote_verified", "direction", "support_level", "topic_id",
                    "matched_keys", "key_coverage", "source_file")
                   if k in e})
    records = []
    raw_recs = payload.get("records") or scr.get("records") or scr.get("included_records") or []
    if isinstance(raw_recs, dict):
        raw_recs = raw_recs.get("items") or raw_recs.get("records") or []
    for r in raw_recs or []:
        if not isinstance(r, dict):
            continue
        records.append({k: r.get(k) for k in
                        ("record_id", "title", "year", "venue", "doi", "type", "outcome",
                         "source_file", "body_chars", "extraction_method")
                        if k in r})
    if not records:
        seen = {}
        for e in ev:
            rid = e.get("record_id")
            if rid and rid not in seen:
                seen[rid] = {"record_id": rid, "title": e.get("title"), "year": e.get("year"),
                             "venue": e.get("venue"), "doi": e.get("doi")}
        records = list(seen.values())
    out = {
        "skill": _first(payload, "skill", default=""),
        "case_id": payload.get("case_id"),
        "question": _question(payload),
        "screening": _first(payload, "screening", default={}),
        "records": records,
        "evidence_table": ev,
        "conflicts": _items(_first(payload, "conflicts", "conflict_analysis", default=[])),
        "gaps": _items(_first(payload, "research_gaps", "gaps", "gap_analysis", default=[])),
        "coverage": _first(payload, "coverage", "corpus_coverage", default={}),
        "temporal_trends": _first(payload, "temporal_trends", "trends", default=[]),
        "frontier_trends": _first(payload, "frontier_trends", "frontiers", default=[]),
        "prisma_flow": _first(payload, "prisma_flow", "prisma", default={}),
        "quality_appraisal": _items(_first(payload, "quality_appraisal", "quality", default=[])),
        "candidate_assessments": _items(_first(payload, "candidate_assessments", "novelty_assessment",
                                                  "assessments", default=[])),
        "search_strategy": _first(payload, "search_strategy", default={}),
        "screening_log": _first(payload, "screening_log", default={}),
        "synthesis": _first(payload, "synthesis", default={}),
        "topic_evolution": _first(payload, "topic_evolution", default=[]),
        "keyword_heat": _first(payload, "keyword_heat", default=[]),
        "representative_works": _items(_first(payload, "representative_works", default=[])),
        "snapshot_scope": _first(payload, "snapshot_scope", default={}),
        "problem_statement": _first(payload, "problem_statement", default={}),
        "prior_art_matrix": _items(_first(payload, "prior_art_matrix", default=[])),
        "novelty_assessment": _first(payload, "novelty_assessment", default={}),
        "feasibility_assessment": _first(payload, "feasibility_assessment", default={}),
        "value_assessment": _first(payload, "value_assessment", default={}),
        "risk_assessment": _first(payload, "risk_assessment", default={}),
        "four_dimension_assessment": _first(payload, "four_dimension_assessment", default={}),
        "status": payload.get("status", {}),
    }
    # 控制体积：引文最多 400 字，证据最多 80 条；超出时保留前 80 条并在 note 标记。
    for e in out["evidence_table"]:
        q = e.get("quote")
        if isinstance(q, str) and len(q) > 400:
            e["quote"] = q[:400] + "…"
    if len(out["evidence_table"]) > 80:
        out["evidence_table"] = out["evidence_table"][:80]
        out["note"] = "evidence_table 已截断到前 80 条；完整表见 result.json"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--case", default="")
    args = ap.parse_args(argv)
    payload = json.loads(Path(args.result).read_text(encoding="utf-8"))
    data = compact(payload)
    if not data.get("question") and args.case and Path(args.case).is_file():
        case = json.loads(Path(args.case).read_text(encoding="utf-8"))
        for k in ("research_question", "question", "task_instruction", "instruction", "objective", "topic"):
            v = case.get(k)
            if isinstance(v, str) and v.strip():
                data["question"] = v.strip()
                break
        if not data.get("question"):
            for k in ("case", "input", "data"):
                inner = case.get(k)
                if isinstance(inner, dict):
                    for kk in ("research_question", "question", "task_instruction", "objective"):
                        v = inner.get(kk)
                        if isinstance(v, str) and v.strip():
                            data["question"] = v.strip()
                            break
                if data.get("question"):
                    break
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": args.out, "records": len(data["records"]),
                      "evidence": len(data["evidence_table"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
