#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把不同组织方案例外形归一成管线认识的 sources[] / records[] / research_question。

只做“发现与改名”，不改写内容、不补造字段：
- 解包 `{"case": ...}`、`{"input": ...}`、`{"data": ...}` 一层；
- 问题从 research_question / question / task_instruction / objective / topic / task.md 取；
- 记录从 records / materials / corpus / documents / papers / literature / prior_art 等键取，
  也扫描任意“记录形”字典列表（有 title/abstract/doi/year/record_id 中至少两项）；
- 来源从 sources / source_files / files / attachments 等键取，也扫描任意“文档路径”字符串
  （.pdf/.txt/.md/.docx/.csv）或带 path/file 的字典；
- 只补缺失的规范键，已有键原样保留。
"""

from __future__ import annotations

import os
from pathlib import Path

RECORD_KEYS = ("records", "materials", "corpus", "documents", "papers", "literature",
               "prior_art", "candidates", "items", "bibliography", "references", "studies")
SOURCE_KEYS = ("sources", "source_files", "source_documents", "files", "attachments",
               "pdfs", "documents", "materials")
QUESTION_KEYS = ("research_question", "question", "task_instruction", "instruction",
                 "objective", "research_objective", "topic", "title")
CONTRACT_KEYS = ("output_contract", "outputContract", "expected_outputs", "deliverables")
DOC_SUFFIXES = {".pdf", ".txt", ".md", ".docx", ".doc", ".csv", ".json"}


def _unwrap(case):
    for _ in range(2):
        if not isinstance(case, dict):
            return case
        has_content = any(k in case for k in QUESTION_KEYS + RECORD_KEYS + SOURCE_KEYS)
        if has_content:
            return case
        for key in ("case", "input", "data", "payload", "task"):
            inner = case.get(key)
            if isinstance(inner, dict):
                case = inner
                break
        else:
            return case
    return case


def _walk_dicts(value, depth=0):
    if depth > 3:
        return
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk_dicts(v, depth + 1)
    elif isinstance(value, list):
        for v in value[:80]:
            yield from _walk_dicts(v, depth + 1)


def _walk_lists(value, depth=0):
    if depth > 3:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, list):
                yield k, v
            yield from _walk_lists(v, depth + 1)
    elif isinstance(value, list):
        for v in value[:20]:
            yield from _walk_lists(v, depth + 1)


def _is_record(obj):
    if not isinstance(obj, dict):
        return False
    fields = sum(1 for k in ("title", "abstract", "summary", "doi", "year", "record_id", "authors", "venue")
                 if obj.get(k) not in (None, "", [], {}))
    return fields >= 2 or ("title" in obj and ("abstract" in obj or "summary" in obj))


def _is_source(obj):
    if isinstance(obj, str):
        return Path(obj).suffix.lower() in DOC_SUFFIXES
    if isinstance(obj, dict):
        ref = obj.get("path") or obj.get("file") or obj.get("filepath") or obj.get("location")
        return isinstance(ref, str) and Path(ref).suffix.lower() in DOC_SUFFIXES
    return False


def normalize_case(case):
    if not isinstance(case, dict):
        return case
    case = dict(_unwrap(case))
    if not case.get("research_question"):
        for d in [case] + list(_walk_dicts(case)):
            for k in QUESTION_KEYS:
                val = d.get(k)
                if isinstance(val, str) and len(val.strip()) >= 8:
                    case["research_question"] = val.strip()
                    break
            if case.get("research_question"):
                break

    if not (isinstance(case.get("records"), list) and case["records"]):
        best = []
        for key, lst in _walk_lists(case):
            if key in CONTRACT_KEYS:
                continue
            recs = [x for x in lst if _is_record(x)]
            if len(recs) > len(best):
                best = recs
        if best:
            case["records"] = best

    if not (isinstance(case.get("sources"), list) and case["sources"]):
        best = []
        for key, lst in _walk_lists(case):
            if key in CONTRACT_KEYS:
                continue
            srcs = [x for x in lst if _is_source(x)]
            if len(srcs) > len(best):
                best = srcs
        if best:
            case["sources"] = best

    if not case.get("output_contract") and not case.get("outputContract"):
        for k in CONTRACT_KEYS[2:]:
            if isinstance(case.get(k), (dict, list)) and case[k]:
                case["output_contract"] = case[k]
                break
    return case


def discover_materials(case_dir, input_dir="/input", limit=50):
    """按目录扫描实际材料文件；供管线在 case 未列 sources 时兜底。"""
    out, seen = [], set()
    for base in (Path(case_dir or "."), Path(input_dir)):
        if not base.is_dir():
            continue
        try:
            files = sorted(base.rglob("*"))
        except OSError:
            continue
        for p in files:
            if len(out) >= limit:
                return out
            if not p.is_file() or p.name in {"case.json", "task.md", "TASK.md", "plan.json",
                                             "result.json", "report.md"}:
                continue
            if p.suffix.lower() not in DOC_SUFFIXES:
                continue
            if any(part in {"scripts", "rules", "examples", "__pycache__", ".git", "output", "out", "work"}
                   for part in p.parts):
                continue
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(str(p))
    return out
