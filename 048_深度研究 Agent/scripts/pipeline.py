#!/usr/bin/env python3
"""048 深度研究 Agent —— 主流程（纯标准库，离线、确定性、全流程留痕）。

输入：case.json（研究问题 + 来源/记录双结构 + 前瞻协议 + 约束 + 可选建议场景）+ task.md
输出：out/result.json + work/plan.json（计划与协议先于执行落盘）

六阶段（计划、协议、筛选、检索、证据、综合全部留痕，任何人可按记录复跑）：
  0. 摄入：sources[]（来源文件清单）与 records[]（记录）双结构建模；记录通过
     source_path/source_id 指到来源文件；正文按 内联 text → 内联 abstract → 侧车
     .txt/.ocr.txt → 自研 stdlib PDF 提取（scripts/pdf_min.py）→ DOCX zip+xml →
     文本文件直读 → 诚实 gap 取数，逐条记录 extraction_method + SHA-256 + 字符数。
  A. 拆解：case 显式声明的子问题优先（真实案例的领域问题 + 依赖）；否则要素词典模板
     （facets.json）；两者皆不命中时整题回退为一个通用子问题，绝不硬凑检索式。
  B. 前瞻协议：纳排标准/检索顺序/停止条件先写入 work/plan.json，再逐条筛选；
     协议缺失记 input_gap（R_PROTOCOL_MISSING）并声明回退口径，不冒充「已按协议筛选」。
  C. 筛选与执行：仅纳入记录进入检索；每条记录记 criterion_hits（命中/未命中哪条标准）；
     检索打分 record_score = 0.5·bm25 + 0.5·idf_coverage，retrieve/extract/verify 逐次留参数与返回。
  D. 证据表：evidence_id + record_id + source_file + char_span + 逐字 quote，
     管线内做精确子串校验，失败标 quote_mismatch 不静默。
  E. 综合：结论逐句挂 evidence_id（sentence_map 一一对应）→ 方向词典（direction_lexicon.json）
     同主题成对冲突双列（side_a/side_b 各带逐字引文、方向、强度词），不合并、不写「尚存争议」→
     研究空白只从零/弱证据子问题推导 → 研究建议逐条引用 constraints，不声称完成实验。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kit import (  # noqa: E402
    Clock,
    Result,
    as_list,
    bm25_score,
    clamp,
    first_present,
    load_case,
    load_json,
    load_task_text,
    norm,
    parse_args,
    read_text,
    rounded,
    sha256_text,
    stable_id,
    tokens,
    write_json,
)
import pdf_min  # noqa: E402

SKILL = "deep-research-agent"
RULES = Path(__file__).resolve().parent.parent / "rules"


def load_rules() -> dict:
    return {
        "facets": load_json(RULES / "facets.json", default={}) or {},
        "plan": load_json(RULES / "plan_rules.json", default={}) or {},
        "projection": load_json(RULES / "contract_projection.json", default={}) or {},
        "direction": load_json(RULES / "direction_lexicon.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 0. 来源/记录双结构：路径解析与正文获取
# --------------------------------------------------------------------------------------
def path_bases(work, case_dir: Path) -> list:
    bases, seen = [], set()
    for b in (case_dir, Path(work or "."), Path.cwd(), Path("/input")):
        text = str(b)
        if text not in seen:
            seen.add(text)
            bases.append(b)
    return bases


def resolve_ref(ref: str, bases: list) -> tuple:
    """相对路径依次按 case 目录、work 目录、cwd、/input 解析；返回 (首个候选, 全部候选)。"""
    raw = Path(str(ref))
    if raw.is_absolute():
        return raw, [raw]
    cands, seen = [], set()
    for b in bases:
        c = b / raw
        if str(c) not in seen:
            seen.add(str(c))
            cands.append(c)
    return cands[0], cands


def _body_info(text: str, method: str, source_file: str, error: str = "") -> dict:
    return {"text": text, "method": method, "source_file": source_file,
            "sha256": sha256_text(text) if text else "", "n_chars": len(text), "error": error}


def _extract_docx(path: Path) -> tuple:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:  # noqa: BLE001
        return "", f"docx_parse_failed: {exc}"
    paras = []
    for chunk in xml.split("</w:p>"):
        runs = re.findall(r"<w:t[^>]*>([^<]*)</w:t>", chunk)
        if runs:
            paras.append("".join(runs))
    return "\n".join(paras).strip(), ""


def _sidecar_read(path: Path, suffixes=(".txt", ".ocr.txt")) -> tuple:
    """二进制来源的侧车文本：path 同名 .txt / .ocr.txt。"""
    for suf in suffixes:
        cand = path.parent / (path.stem + suf)
        if cand.is_file():
            text = read_text(cand).strip()
            if text:
                return text, str(cand), ""
    return "", "", ""


def resolve_body(item: dict, bases: list) -> dict:
    """记录正文获取优先级：内联 text → 内联 abstract → 侧车 .txt/.ocr.txt → 自研 PDF 提取
    → DOCX zip+xml → 文本文件直读 → 诚实 missing。每级记录 extraction_method。"""
    body = first_present(item, "text", "content", "body", "fulltext", default="")
    if isinstance(body, list):
        body = "\n".join(str(x) for x in body)
    body = str(body or "").strip()
    if body:
        return _body_info(body, "inline_text", "inline")
    abstract = str(first_present(item, "abstract", "summary", default="") or "").strip()
    if abstract:
        return _body_info(abstract, "inline_abstract", "inline")
    ref = str(first_present(item, "source_path", "source_file", "path", "file", default="") or "")
    if not ref:
        return _body_info("", "missing", "", "record_has_no_text_and_no_source_path")
    resolved, cands = resolve_ref(ref, bases)
    if not resolved.is_file():
        resolved = next((c for c in cands if c.is_file()), resolved)
    if not resolved.is_file():
        return _body_info("", "missing", str(resolved),
                          "source_file_not_found: " + " | ".join(str(c) for c in cands[:4]))
    suffix = resolved.suffix.lower()
    if suffix in (".pdf", ".docx", ".doc"):
        text, side, err = _sidecar_read(resolved)
        if text:
            return _body_info(text, "sidecar_txt", side)
        text, side, err = _sidecar_read(resolved, (".ocr.txt",))
        if text:
            return _body_info(text, "sidecar_ocr_txt", side)
        if suffix == ".pdf":
            got = pdf_min.extract_pdf_bytes(resolved.read_bytes())
            if got.get("text"):
                return _body_info(got["text"], "pdf_extract", str(resolved), got.get("error", ""))
            return _body_info("", "missing", str(resolved),
                              f"pdf_extract_failed: {got.get('error', 'unknown')}")
        text, err = _extract_docx(resolved)
        if text:
            return _body_info(text, "docx_xml", str(resolved))
        return _body_info("", "missing", str(resolved), err or "docx_empty")
    text = read_text(resolved).strip()
    if text:
        return _body_info(text, "file_read", str(resolved))
    return _body_info("", "missing", str(resolved), "source_file_empty_or_unreadable")


def collect_sources(case: dict, bases: list) -> list:
    out = []
    for idx, item in enumerate(as_list(first_present(case, "sources", "source_files", "source_documents", default=[])), start=1):
        if isinstance(item, str):
            item = {"title": item, "path": item}
        if not isinstance(item, dict):
            continue
        ref = str(first_present(item, "path", "file", "filepath", default="") or "")
        sid = str(first_present(item, "source_id", "id", default=f"SRC{idx:02d}"))
        resolved = None
        if ref:
            resolved, _cands = resolve_ref(ref, bases)
        entry = {"source_id": sid,
                 "title": str(first_present(item, "title", "name", default="") or ""),
                 "path": ref, "type": str(first_present(item, "type", "source_type", default="") or ""),
                 "exists": bool(resolved is not None and resolved.is_file()),
                 "resolved_path": str(resolved) if resolved is not None else ""}
        if entry["exists"]:
            try:
                entry["sha256"] = sha256_text(resolved.read_bytes().decode("utf-8", errors="replace"))
            except OSError:
                entry["sha256"] = ""
        out.append(entry)
    if not out:
        # 兜底：case 未声明 sources 时，扫描案例目录与 /input 的实际材料文件，
        # 避免把“未列出”误当成“没有材料”。只收常见文本/文档类型，跳过管线自身产物。
        skip_names = {"case.json", "task.md", "TASK.md", "plan.json", "result.json", "report.md"}
        skip_parts = {"scripts", "rules", "examples", "__pycache__", ".git", "output", "out", "work"}
        seen_paths = set()
        scan_dirs = []
        for base in ([Path(bases[0])] if bases else []) + [Path("/input")]:
            if base.is_dir() and str(base) not in {str(x) for x in scan_dirs}:
                scan_dirs.append(base)
        for base in scan_dirs:
            try:
                candidates = sorted(base.rglob("*"))
            except OSError:
                continue
            for p in candidates:
                if len(out) >= 50:
                    break
                if not p.is_file() or p.name in skip_names:
                    continue
                if p.suffix.lower() not in {".pdf", ".docx", ".txt", ".md", ".csv", ".json"}:
                    continue
                if any(part in skip_parts for part in p.parts):
                    continue
                key = str(p.resolve())
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                out.append({"source_id": f"SRC{len(out) + 1:02d}", "title": p.stem, "path": str(p),
                            "type": p.suffix.lstrip("."), "exists": True, "resolved_path": str(p),
                            "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    return out


def collect_records(case: dict, bases: list, sources: list) -> list:
    by_sid = {s["source_id"]: s for s in sources}
    out = []
    raw = first_present(case, "records", "materials", "corpus", "documents", "papers", "literature", default=[])
    for idx, item in enumerate(as_list(raw), start=1):
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        title = str(first_present(item, "title", "name", default="") or "").strip()
        abstract = str(first_present(item, "abstract", "summary", default="") or "").strip()
        bodyinfo = resolve_body(item, bases)
        ref = str(first_present(item, "source_path", "source_file", "path", "file", default="") or "")
        sid = str(first_present(item, "source_id", default="") or "")
        src_entry = by_sid.get(sid) if sid else None
        if not title and src_entry:
            title = src_entry.get("title", "")
        rid = str(first_present(item, "record_id", "id", "doi", default="") or stable_id("rec", title, ref, idx, n=8))
        year = first_present(item, "year", "publication_year", default=None)
        try:
            year = int(str(year)[:4]) if year not in (None, "") else None
        except (TypeError, ValueError):
            year = None
        pages = first_present(item, "pages", "page_list")
        out.append({
            "record_id": rid, "title": title, "abstract": abstract,
            "text": bodyinfo["text"], "body": bodyinfo,
            "source_ref": ref, "source_id": sid,
            "source_file": bodyinfo["source_file"],
            "pages": pages if isinstance(pages, list) else [],
            "year": year,
            "venue": str(first_present(item, "venue", "journal", "container", default="") or ""),
            "doi": str(first_present(item, "doi", default="") or ""),
            "type": str(first_present(item, "type", "pub_type", default="") or ""),
            "source": str(first_present(item, "source", "database", default="") or ""),
            "version": str(first_present(item, "version", default="") or ""),
        })
    if not out and sources:
        # 兜底：case 只给来源文件清单、未给 records 时，把每个可读来源当作一条记录，
        # 正文仍走同一套优先级（侧车/PDF/DOCX/文本），并保留 source_id 回指。
        for src in sources:
            if not src.get("exists"):
                continue
            ref = str(src.get("path") or "")
            info = resolve_body({"source_path": ref, "title": src.get("title", "")}, bases)
            out.append({
                "record_id": f"REC{len(out) + 1:02d}",
                "title": str(src.get("title") or Path(ref).stem),
                "abstract": "", "text": info["text"], "body": info,
                "source_ref": ref, "source_id": str(src.get("source_id") or ""),
                "source_file": info["source_file"], "pages": [],
                "year": None, "venue": "", "doi": "",
                "type": str(src.get("type") or ""), "source": "", "version": "",
            })
    return out


def record_text(rec: dict) -> str:
    return "\n".join([rec["title"], rec["abstract"], rec["text"]]).strip()


# --------------------------------------------------------------------------------------
# A. 拆解
# --------------------------------------------------------------------------------------
def parse_question(case: dict, task_text: str) -> dict:
    q = first_present(case, "research_question", "question", "task_instruction", "instruction", default="")
    source = "case.research_question"
    if not str(q).strip():
        q = "\n".join(l for l in task_text.splitlines() if l.strip() and not l.startswith("#"))[:800]
        source = "task.md"
    return {"text": str(q).strip(), "source": source}


def detect_facets(question: str, facets: dict) -> dict:
    qn = norm(question)
    found, missing = {}, []
    for name, spec in (facets.get("lexicon") or {}).items():
        hits = [t for t in as_list(spec.get("terms", [])) if norm(t) and norm(t) in qn]
        if hits:
            found[name] = {"surface": sorted({norm(h) for h in hits}),
                           "canonical": spec.get("canonical", name),
                           "label": spec.get("label", name)}
        elif spec.get("required"):
            missing.append(name)
    return {"present": found, "missing_required": missing}


def expand_keys(terms: list, stop: list) -> list:
    """把词组展开成可匹配的词项：英文按词、中文按二元组；短语本身也保留（便于整串命中）。"""
    stop_set = {norm(x) for x in stop}
    out = []
    for term in terms:
        t = norm(term)
        if not t:
            continue
        if t not in stop_set and t not in out:
            out.append(t)
        for tok in tokens(term):
            if tok in stop_set or len(tok) < 2 or tok.isdigit() or tok in out:
                continue
            out.append(tok)
    return out


def build_subquestions_from_case(case: dict) -> list:
    """真实案例画像：领域研究问题已在 case 中拆好子问题并标注依赖 → 显式声明优先。"""
    out = []
    raw = first_present(case, "subquestions", "research_subquestions", "screening_subquestions", default=[])
    for i, item in enumerate(as_list(raw), start=1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = str(first_present(item, "text", "question", "title", default="") or "").strip()
        sid = str(first_present(item, "subquestion_id", "id", default=f"SQ{i}"))
        keys = []
        for k in as_list(first_present(item, "keys", "query_keys", "search_keys", "terms", default=[])):
            k = norm(k)
            if k and k not in keys:
                keys.append(k)
        if not keys and text:
            keys = expand_keys([text], [])[:8]
        deps = [str(d) for d in as_list(first_present(item, "depends_on", "depends", "requires", default=[])) if str(d)]
        out.append({"subquestion_id": sid, "template_id": "case_declared", "facets": [],
                    "text": text, "keys": keys, "depends_on": deps,
                    "evidence_requirement": str(first_present(item, "evidence_requirement", default="") or ""),
                    "source": "case_declared"})
    return out


def build_subquestions_templates(question: dict, facet_state: dict, facets: dict, cfg: dict) -> list:
    templates = facets.get("templates", [])
    present = facet_state["present"]
    subs, seen = [], set()
    for tpl in templates:
        need = [f for f in as_list(tpl.get("requires", [])) if f != "always"]
        if not all(f in present for f in need):
            continue
        slots = {}
        for f in need:
            slots[f] = present[f]["canonical"]
        text = tpl.get("template", "")
        try:
            text = text.format(**slots)
        except (KeyError, IndexError):
            continue
        key = norm(text)
        if key in seen:
            continue
        seen.add(key)
        surfaces = []
        for f in need:
            surfaces.extend(present[f]["surface"])
            surfaces.append(norm(present[f]["canonical"]))
            surfaces.extend(as_list((facets.get("lexicon") or {}).get(f, {}).get("terms", [])))
        keys = expand_keys(surfaces, cfg.get("key_stop_terms", []))
        subs.append({
            "subquestion_id": tpl.get("id", f"S{len(subs) + 1}"),
            "template_id": tpl.get("id", ""),
            "facets": need,
            "text": text,
            "keys": sorted({k for k in keys if k}),
            "depends_on": [d for d in as_list(tpl.get("depends_on", [])) if d],
            "evidence_requirement": tpl.get("evidence_requirement", ""),
            "source": "facet_templates",
        })
    # 仅当词典部分命中而必备要素缺失时才生成澄清子问题；显式子问题/通用回退路径不追加
    if present:
        for name in facet_state["missing_required"]:
            subs.append({
                "subquestion_id": f"S{len(subs) + 1}",
                "template_id": "missing_facet",
                "facets": [name],
                "text": f"问题的必备要素「{facets.get('lexicon', {}).get(name, {}).get('label', name)}」未在问题中出现，需要先澄清",
                "keys": [],
                "depends_on": [],
                "evidence_requirement": "需用户补齐该要素后才能制定检索表达式",
                "needs_clarification": True,
                "source": "facet_templates",
            })
    return subs


def generic_subquestion(question: dict, cfg: dict) -> list:
    """词典与显式子问题都不适用（非化学域的真实案例）→ 整题一个通用子问题，检索键取题面信息词。"""
    keys = expand_keys([question["text"]], cfg.get("key_stop_terms", []))
    keys = keys[: int(cfg.get("generic_max_keys", 8))]
    return [{"subquestion_id": "SQ1", "template_id": "generic_fallback", "facets": [],
             "text": question["text"][:200], "keys": keys, "depends_on": [],
             "evidence_requirement": "需要与题面对象/指标直接对应的原始记录",
             "source": "generic_fallback"}]


# --------------------------------------------------------------------------------------
# B. 规划
# --------------------------------------------------------------------------------------
def build_plan(subquestions: list, cfg: dict) -> list:
    order = {s["subquestion_id"]: i for i, s in enumerate(subquestions)}
    plan = []
    for s in subquestions:
        depth = 1
        cur = s
        guard = 0
        while cur["depends_on"] and guard < 10:
            depth += 1
            nxt = cur["depends_on"][0]
            cur = next((x for x in subquestions if x["subquestion_id"] == nxt), {"depends_on": []})
            guard += 1
        expr = " AND ".join(s["keys"][:4]) if s["keys"] else ""
        plan.append({
            "plan_id": f"P{order[s['subquestion_id']] + 1:02d}",
            "subquestion_id": s["subquestion_id"],
            "query_expression": expr,
            "query_keys": s["keys"],
            "sources": cfg.get("sources", ["local-corpus"]),
            "order": depth,
            "parallel_group": depth,
            "depends_on": s["depends_on"],
            "stop_condition": cfg.get("stop_condition", ""),
            "budget_units": int(cfg.get("budget_units_per_subquestion", 400)),
        })
    plan.sort(key=lambda x: (x["order"], x["plan_id"]))
    return plan


# --------------------------------------------------------------------------------------
# B2. 前瞻协议（先落盘，后逐条筛选）
# --------------------------------------------------------------------------------------
def normalize_criterion(raw, prefix: str, idx: int) -> dict:
    if not isinstance(raw, dict):
        raw = {"text": str(raw)}
    return {
        "criterion_id": str(first_present(raw, "criterion_id", "id", default=f"{prefix}{idx}")),
        "text": str(first_present(raw, "text", "description", "rule", default="") or ""),
        "all_keys": [norm(k) for k in as_list(first_present(raw, "all_keys", "all_terms", default=[])) if norm(k)],
        "any_keys": [norm(k) for k in as_list(first_present(raw, "any_keys", "any_terms", "keys", default=[])) if norm(k)],
        "type_in": [norm(t) for t in as_list(first_present(raw, "type_in", "types", default=[])) if norm(t)],
        "body_readable": bool(first_present(raw, "body_readable", default=False)),
        "min_chars": int(first_present(raw, "min_chars", default=0) or 0),
    }


def build_protocol_block(case: dict, cfg: dict) -> dict:
    raw = first_present(case, "protocol", "screening_protocol", "search_protocol", "review_protocol", default=None)
    if isinstance(raw, dict):
        incl, excl = [], []
        for c in as_list(raw.get("inclusion")):
            incl.append(normalize_criterion(c, "IN", len(incl) + 1))
        for c in as_list(raw.get("exclusion")):
            excl.append(normalize_criterion(c, "EX", len(excl) + 1))
        for c in as_list(raw.get("criteria")):
            role = norm(first_present(c, "role", "kind", default="inclusion"))
            if "exc" in role or "excl" in role or "out" in role:
                excl.append(normalize_criterion(c, "EX", len(excl) + 1))
            else:
                incl.append(normalize_criterion(c, "IN", len(incl) + 1))
        if incl or excl:
            return {"protocol_status": "registered_prospective",
                    "protocol_id": str(first_present(raw, "protocol_id", "id", default="PROTO")),
                    "prospective": bool(first_present(raw, "prospective", default=True)),
                    "inclusion": incl, "exclusion": excl,
                    "stop_conditions": [str(s) for s in as_list(raw.get("stop_conditions", raw.get("stop", [])))],
                    "search_order": [str(s) for s in as_list(raw.get("search_order", []))],
                    "source": "case.protocol", "input_gap": None, "fallback": None}
    fb = cfg.get("fallback_screening") or {}
    fb_incl = [normalize_criterion(c, "FB-IN", i + 1) for i, c in enumerate(as_list(fb.get("inclusion", [])))]
    fb_excl = [normalize_criterion(c, "FB-EX", i + 1) for i, c in enumerate(as_list(fb.get("exclusion", [])))]
    return {"protocol_status": "missing_fallback_declared",
            "protocol_id": "", "prospective": False,
            "inclusion": fb_incl, "exclusion": fb_excl,
            "stop_conditions": [str(s) for s in as_list(fb.get("stop_conditions", []))],
            "search_order": [], "source": "input_gap",
            "input_gap": {"code": "R_PROTOCOL_MISSING",
                          "detail": "case.json 未提供前瞻性筛选/检索协议（纳排标准、检索顺序、停止条件）",
                          "fallback_note": str(fb.get("note", "")),
                          "declaration": "以下筛选按声明回退口径执行并在每条记录标注 fallback=true，不冒充「已按注册协议筛选」"},
            "fallback": {"declared_in": "rules/plan_rules.json#fallback_screening", "note": str(fb.get("note", ""))}}


def criterion_eval(crit: dict, rec: dict) -> dict:
    body_all = norm(rec["title"] + " " + rec["abstract"] + " " + rec["text"])
    terms, ok = [], True
    if crit["body_readable"]:
        readable = rec["body"]["method"] != "missing" and rec["body"]["n_chars"] >= crit["min_chars"]
        ok = ok and readable
        if readable:
            terms.append(f"body_chars={rec['body']['n_chars']}")
    for k in crit["all_keys"]:
        if k in body_all:
            terms.append(k)
        else:
            ok = False
    if crit["any_keys"]:
        hits = [k for k in crit["any_keys"] if k in body_all]
        if hits:
            terms.extend(hits)
        else:
            ok = False
    if crit["type_in"]:
        if norm(rec["type"]) in crit["type_in"]:
            terms.append(f"type={rec['type']}")
        else:
            ok = False
    return {"criterion_id": crit["criterion_id"], "matched": bool(ok), "matched_terms": sorted(set(terms))}


def screen_records(records: list, protocol_block: dict, trace: list) -> tuple:
    """对每条记录逐条执行纳排：命中/未命中哪条标准记入 criterion_hits。"""
    rows, is_fallback = [], protocol_block["protocol_status"] == "missing_fallback_declared"
    for rec in records:
        if rec["body"]["method"] == "missing" or rec["body"]["n_chars"] == 0:
            outcome, incl_hits, excl_hits = "unscored_no_body", [], []
            reason = "记录正文不可读（内联/侧车/原生提取均失败），按口径不参与纳排与检索，诚实降级"
        else:
            incl_hits = [criterion_eval(c, rec) for c in protocol_block["inclusion"]]
            excl_hits = [criterion_eval(c, rec) for c in protocol_block["exclusion"]]
            hit_ex = [c for c in excl_hits if c["matched"]]
            missed_in = [c for c in incl_hits if not c["matched"]]
            if hit_ex:
                outcome = "excluded"
                reason = "命中排除标准 " + ",".join(c["criterion_id"] for c in hit_ex)
            elif incl_hits and missed_in:
                outcome = "excluded"
                reason = "未满足纳入标准 " + ",".join(c["criterion_id"] for c in missed_in)
            else:
                outcome = "included"
                reason = "满足全部纳入标准" if incl_hits else "回退口径：正文可读即纳入（案例未提供协议）"
        rows.append({"record_id": rec["record_id"], "title": rec["title"], "type": rec["type"],
                     "source_file": rec["source_file"],
                     "extraction_method": rec["body"]["method"],
                     "body_sha256": rec["body"]["sha256"], "body_chars": rec["body"]["n_chars"],
                     "outcome": outcome,
                     "inclusion_hits": incl_hits, "exclusion_hits": excl_hits,
                     "fallback": is_fallback, "reason": reason})
        trace.append({"seq": 0, "tool": "protocol_screen",
                      "params": {"record_id": rec["record_id"], "protocol_id": protocol_block["protocol_id"],
                                 "inclusion": [c["criterion_id"] for c in protocol_block["inclusion"]],
                                 "exclusion": [c["criterion_id"] for c in protocol_block["exclusion"]],
                                 "fallback": is_fallback},
                      "result": {"outcome": outcome, "reason": reason}})
    counts = {"screened": len(rows),
              "included": sum(1 for r in rows if r["outcome"] == "included"),
              "excluded": sum(1 for r in rows if r["outcome"] == "excluded"),
              "unscored_no_body": sum(1 for r in rows if r["outcome"] == "unscored_no_body")}
    return rows, counts


# --------------------------------------------------------------------------------------
# C. 检索执行
# --------------------------------------------------------------------------------------
def df_map(keys: list, records) -> dict:
    rows = list(records.values()) if isinstance(records, dict) else list(records)
    out = {}
    for k in keys:
        out[k] = sum(1 for r in rows if k in norm(record_text(r)))
    return out


def idf(df: int, n: int) -> float:
    import math

    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def _key_token_map(keys: list) -> dict:
    """键 → token 展开映射：中文多字键在 token 层是二元组集合，检索查询必须先展开再进 BM25。"""
    return {k: (tokens(k) or ([k] if k else [])) for k in keys}


def execute_plan(plan: list, records: list, cfg: dict, trace: list) -> dict:
    """按 plan 顺序检索本地语料；逐次记录参数/命中/新增证据/停止判定。

    键级 df 用 record_text 子串判定（对 CJK 稳定）；BM25 查询用键的 token 展开，
    否则「首次库仑效率」这类多字键在 token 层永远无法命中，检索退化。"""
    sc = cfg.get("retrieval", {})
    th = cfg.get("thresholds", {})
    top_k = int(th.get("top_records_per_subquestion", 3))
    min_score = float(th.get("min_record_score", 0.3))
    min_cover = float(th.get("min_evidence_key_coverage", 0.3))
    max_rounds = int(th.get("max_rounds", 2))
    evidence_by_sub, covered = {}, set()
    if not records:
        for p in plan:
            evidence_by_sub[p["subquestion_id"]] = []
            trace.append({"seq": 0, "tool": "local_retrieve", "params": {"plan_id": p["plan_id"], "keys": p["query_keys"]},
                          "result": {"hits": 0, "stopped_by": "empty_corpus", "note": "纳入记录为空，检索未执行"}})
        return evidence_by_sub
    doc_tokens = {r["record_id"]: tokens(record_text(r), unique=False) for r in records}
    corpus = list(doc_tokens.values())
    n = len(records) or 1
    for p in plan:
        keys = p["query_keys"]
        sub = p["subquestion_id"]
        if not keys:
            trace.append({"seq": 0, "tool": "local_retrieve", "params": {"plan_id": p["plan_id"], "keys": []},
                          "result": {"hits": 0, "stopped_by": "no_query_terms",
                                     "note": "子问题缺检索表达式（要素待澄清），未执行检索"}})
            evidence_by_sub[sub] = []
            continue
        df = df_map(keys, records)
        live = [k for k in keys if df.get(k, 0) > 0]
        key_toks = _key_token_map(live)
        query_tokens = [t for k in live for t in key_toks[k]]
        total_idf = sum(idf(df.get(k, 0), n) for k in live) or 1.0
        scored = []
        for r in records:
            dt = doc_tokens[r["record_id"]]
            b = bm25_score(query_tokens, dt, corpus,
                           k1=float(sc.get("bm25", {}).get("k1", 1.2)), b=float(sc.get("bm25", {}).get("b", 0.75)))
            hit = [k for k in live if any(t in dt for t in key_toks[k])]
            cover = sum(idf(df.get(k, 0), n) for k in hit) / total_idf if hit else 0.0
            score = clamp(float(sc.get("weights", {}).get("bm25", 0.5)) * clamp(b)
                          + float(sc.get("weights", {}).get("coverage", 0.5)) * cover)
            scored.append({"record_id": r["record_id"], "bm25": b, "coverage": rounded(cover),
                           "hit": hit, "score": rounded(score)})
        scored.sort(key=lambda x: (-x["score"], x["record_id"]))
        got, new, rounds = [], 0, 0
        stopped_by = "exhausted_records"
        for rnd in range(1, max_rounds + 1):
            rounds = rnd
            fresh = [s for s in scored[:top_k] if s["score"] >= min_score and s["coverage"] >= min_cover]
            fresh_new = [s for s in fresh if s["record_id"] not in covered]
            new += len(fresh_new)
            got = fresh
            for s in fresh:
                covered.add(s["record_id"])
            if not fresh_new:
                stopped_by = "no_new_evidence"
                break
        trace.append({"seq": 0, "tool": "local_retrieve",
                      "params": {"plan_id": p["plan_id"], "keys": keys,
                                 "keys_present_in_corpus": live,
                                 "absent_keys": [k for k in keys if k not in live],
                                 "expression": p["query_expression"], "top_k": top_k,
                                 "min_score": min_score, "min_coverage": min_cover,
                                 "scoring": cfg.get("retrieval", {}).get("formula", ""),
                                 "corpus_records": n, "sources": p["sources"]},
                      "result": {"hits": len(got), "new_evidence": new, "rounds": rounds, "stopped_by": stopped_by,
                                 "top": [{"record_id": s["record_id"], "score": s["score"],
                                          "coverage": s["coverage"], "bm25": rounded(s["bm25"])} for s in got]}})
        evidence_by_sub[sub] = got
    return evidence_by_sub


# --------------------------------------------------------------------------------------
# D. 证据表（精确来源：record_id + source_file + char_span + 逐字 quote + 校验）
# --------------------------------------------------------------------------------------
def split_sentences(text: str, terminators: list, split_ascii_period: bool = True) -> list:
    out, buf = [], ""
    n = len(text)
    for i, ch in enumerate(text):
        buf += ch
        if ch in terminators:
            if buf.strip():
                out.append(buf.strip())
            buf = ""
        elif ch == "." and split_ascii_period:
            nxt = text[i + 1] if i + 1 < n else " "
            prv = text[i - 1] if i > 0 else ""
            if prv.isdigit() or nxt.isdigit():
                continue  # 52.3 等数字小数点不切句
            if i == n - 1 or nxt in (" ", "\n", "\t"):
                if buf.strip():
                    out.append(buf.strip())
                buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _strength_terms(quote_norm: str, direction_rules: dict) -> list:
    return sorted({t for t in as_list(direction_rules.get("strength_terms", []))
                   if norm(t) and norm(t) in quote_norm})


def detect_direction(quote: str, direction_rules: dict) -> dict:
    """方向词典判定：零效应组优先；否定前置词使 ±1 失效；同句正负并存记 mixed。"""
    groups = as_list(direction_rules.get("direction_groups", []))
    q = norm(quote)
    negs = [norm(x) for x in as_list(direction_rules.get("negation_markers", ["不", "无", "未", "没", "not ", "no "])) if norm(x)]

    def hits_for(terms) -> list:
        found = []
        for t in sorted({norm(x) for x in as_list(terms) if norm(x)}, key=len, reverse=True):
            start = 0
            while True:
                idx = q.find(t, start)
                if idx < 0:
                    break
                found.append((idx, t))
                start = idx + len(t)
        return sorted(found)

    zero_g = next((g for g in groups if str(g.get("direction")) == "0"), None)
    pos_g = next((g for g in groups if str(g.get("direction")) == "+1"), None)
    neg_g = next((g for g in groups if str(g.get("direction")) == "-1"), None)

    def live(g) -> tuple:
        pos, neg = [], []
        for idx, t in hits_for((g or {}).get("terms", [])):
            pre = q[max(0, idx - 6):idx]
            (neg if any(m in pre for m in negs) else pos).append(t)
        return pos, neg

    if zero_g and hits_for(zero_g.get("terms", [])):
        return {"value": "0", "terms": sorted({t for _, t in hits_for(zero_g.get("terms", []))}),
                "strength_terms": _strength_terms(q, direction_rules), "negated": False}
    pos, pos_neg = live(pos_g)
    ng, ng_neg = live(neg_g)
    if pos and ng:
        return {"value": "mixed", "terms": sorted(set(pos + ng)),
                "strength_terms": _strength_terms(q, direction_rules), "negated": False}
    if pos:
        return {"value": "+1", "terms": sorted(set(pos)),
                "strength_terms": _strength_terms(q, direction_rules), "negated": False}
    if ng:
        return {"value": "-1", "terms": sorted(set(ng)),
                "strength_terms": _strength_terms(q, direction_rules), "negated": False}
    if pos_neg or ng_neg:
        return {"value": "0", "terms": sorted(set(pos_neg + ng_neg)),
                "strength_terms": _strength_terms(q, direction_rules), "negated": True}
    return {"value": None, "terms": [], "strength_terms": _strength_terms(q, direction_rules), "negated": False}


def build_evidence(plan: list, evidence_by_sub: dict, records: dict, cfg: dict, direction_rules: dict, trace: list) -> tuple:
    term = cfg.get("sentence_terminators", ["。", "！", "？", ";", "；"])
    split_period = bool(cfg.get("split_ascii_period", True))
    th = cfg.get("thresholds", {})
    min_cover = float(th.get("min_evidence_key_coverage", 0.3))
    direct = float(th.get("direct_coverage", 0.6))
    indirect = float(th.get("indirect_coverage", 0.35))
    rows, per_sub = [], {}
    for p in plan:
        sub = p["subquestion_id"]
        keys = p["query_keys"]
        if not keys:
            per_sub[sub] = []
            continue
        n = len(records) or 1
        df = df_map(keys, records)
        live = [k for k in keys if df.get(k, 0) > 0]
        total_idf = sum(idf(df.get(k, 0), n) for k in live) or 1.0
        picked = []
        for s in evidence_by_sub.get(sub, []):
            rec = records[s["record_id"]]
            best = None
            for fld in ("text", "abstract"):
                src_text = rec[fld]
                if not src_text:
                    continue
                for sent in split_sentences(src_text, term, split_period):
                    sn, tn = norm(sent), norm(rec["title"])
                    if tn and (sn == tn or sn in tn or tn in sn):
                        continue  # 题名不进引文：与记录标题互含的标题行跳过
                    hit = [k for k in live if k in norm(sent)]
                    if not hit:
                        continue
                    cov = sum(idf(df.get(k, 0), n) for k in hit) / total_idf
                    if best is None or cov > best[0]:
                        best = (cov, sent, hit, fld)
            if best is None or best[0] < min_cover:
                continue
            cov, sent, hit, fld = best
            field_text = rec[fld]
            start = field_text.find(sent)
            end = start + len(sent) if start >= 0 else -1
            exact = start >= 0 and field_text[start:end] == sent
            strength = "direct" if cov >= direct else ("indirect" if cov >= indirect else "background")
            eid = f"E{len(rows) + 1:03d}"
            page = None
            if rec["pages"]:
                page = first_present(rec["pages"][0], "page_label", "page", default=None) if isinstance(rec["pages"][0], dict) else None
            rows.append({
                "evidence_id": eid,
                "subquestion_id": sub,
                "record_id": rec["record_id"],
                "title": rec["title"],
                "year": rec["year"],
                "venue": rec["venue"],
                "doi": rec["doi"],
                "type": rec["type"],
                "source": rec["source"] or "local-corpus",
                "source_file": rec["source_file"],
                "extraction_method": rec["body"]["method"],
                "quote_field": fld,
                "quote": sent,
                "char_span": {"start": start, "end": end},
                "quote_verified": bool(exact),
                "body_sha256": rec["body"]["sha256"],
                "location": {"field": fld, "page": page, "record_pointer": f"{rec['record_id']}#{fld}",
                             "char_span": {"start": start, "end": end}},
                "key_coverage": rounded(cov),
                "matched_keys": sorted(hit),
                "strength": strength,
                "direction": detect_direction(sent, direction_rules),
                "sha256_quote": sha256_text(norm(sent)),
            })
            if not exact:
                rows[-1]["quote_mismatch"] = "quote 不是该字段原文的精确子串（span 定位失败），已显式标记，不静默"
            trace.append({"seq": 0, "tool": "evidence_extract",
                          "params": {"record_id": rec["record_id"], "field": fld,
                                     "keys": sorted(hit), "min_coverage": min_cover},
                          "result": {"evidence_id": eid, "char_span": {"start": start, "end": end},
                                     "quote_chars": len(sent), "source_file": rec["source_file"]}})
            trace.append({"seq": 0, "tool": "quote_verify",
                          "params": {"evidence_id": eid, "record_id": rec["record_id"],
                                     "source_file": rec["source_file"], "field": fld},
                          "result": {"exact_substring": bool(exact), "sha256": rec["body"]["sha256"],
                                     "verdict": "verified" if exact else "mismatch_marked"}})
            picked.append(eid)
        per_sub[sub] = picked
    return rows, per_sub


# --------------------------------------------------------------------------------------
# E. 冲突（方向词典成对双列） + 综合 + 空白 + 建议
# --------------------------------------------------------------------------------------
def _conflict_side(row: dict) -> dict:
    d = row.get("direction") or {}
    return {"evidence_id": row["evidence_id"], "record_id": row["record_id"],
            "source_file": row["source_file"], "quote": row["quote"],
            "quote_verified": row["quote_verified"],
            "direction": d.get("value"), "direction_terms": d.get("terms", []),
            "strength_terms": d.get("strength_terms", [])}


def detect_conflicts(rows: list, cfg: dict) -> list:
    """同子问题 + 共享检索键（同主题）才比对：方向词典成对判定；数值冲突保留原口径。"""
    vocab = cfg.get("context_vocabulary", {})
    num = re.compile(r"(\d+(?:\.\d+)?)\s*(%|个百分点|ms|mM|mL|µm|倍|pp|kN/m|MPa|mg/dm2)")
    raws = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if a["subquestion_id"] != b["subquestion_id"]:
                continue
            shared = sorted(set(a["matched_keys"]) & set(b["matched_keys"]))
            if not shared:
                continue
            va = (a.get("direction") or {}).get("value")
            vb = (b.get("direction") or {}).get("value")
            polar = {"+1", "-1"}
            if va in polar and vb in polar and va != vb:
                raws.append({"type": "direction_conflict", "subquestion_id": a["subquestion_id"],
                             "shared_keys": shared, "a": a, "b": b,
                             "detail": f"同主题证据方向相反：{'+1' if va == '+1' else '-1'} 与 {vb}（共享键 {shared}）",
                             "resolution": "并列双列呈现（side_a/side_b 各带逐字引文、方向与强度词），不择一、不合并、不以「尚存争议」替代双列"})
                continue
            if {va, vb} in ({"0", "+1"}, {"0", "-1"}):
                raws.append({"type": "zero_vs_direction", "subquestion_id": a["subquestion_id"],
                             "shared_keys": shared, "a": a, "b": b,
                             "detail": f"同一主题下一条为零效应（0）、另一条为方向效应（{vb if va == '0' else va}），口径差异待核",
                             "resolution": "零效应与方向效应并列双列，各自保留原文引文；差异写进空白/限制说明，不择一"})
                continue
            if va == "mixed" or vb == "mixed":
                continue  # 一句话内正负并存，已按 mixed 标记，不参与成对判定（另记 uncertainty）
            qa = [(m.group(1), m.group(2)) for m in num.finditer(norm(a["quote"]))]
            qb = [(m.group(1), m.group(2)) for m in num.finditer(norm(b["quote"]))]
            ca, cb = {}, {}
            for group, terms in (vocab or {}).items():
                ha = sorted({norm(t) for t in as_list(terms) if norm(t) in norm(a["quote"])})
                hb = sorted({norm(t) for t in as_list(terms) if norm(t) in norm(b["quote"])})
                if ha:
                    ca[group] = ha
                if hb:
                    cb[group] = hb
            same_context = bool(vocab) and all(set(ca.get(g) or []) & set(cb.get(g) or []) for g in vocab)
            hit = next(((x, y) for x in qa for y in qb if x[1] == y[1] and x[0] != y[0]), None)
            if hit and same_context:
                raws.append({"type": "numeric_conflict", "subquestion_id": a["subquestion_id"],
                             "shared_keys": shared, "a": a, "b": b,
                             "detail": f"同一数据集与同一方法口径下指标不一致：{hit[0][0]}{hit[0][1]} vs {hit[1][0]}{hit[1][1]}",
                             "resolution": "并列呈现，不择一写入结论；差异写进研究空白或限制说明"})
    conflicts = []
    for c in raws:
        conflicts.append({"conflict_id": f"C{len(conflicts) + 1:02d}", "type": c["type"],
                          "subquestion_id": c["subquestion_id"], "shared_keys": c["shared_keys"],
                          "side_a": _conflict_side(c["a"]), "side_b": _conflict_side(c["b"]),
                          "detail": c["detail"], "resolution": c["resolution"]})
    return conflicts


def synthesize(subquestions: list, plan: list, rows: list, conflicts: list, cfg: dict) -> tuple:
    conclusions, gaps = [], []
    by_sub = {}
    for r in rows:
        by_sub.setdefault(r["subquestion_id"], []).append(r)
    th = cfg.get("thresholds", {})
    for s in subquestions:
        sub = s["subquestion_id"]
        ev = by_sub.get(sub, [])
        direct = [e for e in ev if e["strength"] == "direct"]
        ind = [e for e in ev if e["strength"] == "indirect"]
        cflt = [c for c in conflicts if c["subquestion_id"] == sub]
        if ev:
            level = "strong" if len(direct) >= int(th.get("strong_min_direct_evidence", 2)) else (
                "moderate" if (direct or len(ind) >= 2) else "weak")
            if cflt:
                level = "conflicting"
            picks = ev[:3]
            stated = " ".join(f"{e['quote']} [{e['evidence_id']}]" for e in picks)
            concl = {
                "conclusion_id": f"K{len(conclusions) + 1:02d}",
                "subquestion_id": sub,
                "statement": stated,
                "statement_rule": "结论 = 原文整句 + 证据标记的拼接（最多 3 条，逐句对应 sentence_map），不改写、不新增未引用的判断",
                "sentence_map": [{"evidence_id": e["evidence_id"], "record_id": e["record_id"],
                                  "source_file": e["source_file"], "quote": e["quote"]} for e in picks],
                "evidence_ids": [e["evidence_id"] for e in ev],
                "support_level": level,
                "conflict_ids": [c["conflict_id"] for c in cflt],
                "dual_column": ([{"conflict_id": c["conflict_id"], "type": c["type"],
                                  "side_a": c["side_a"], "side_b": c["side_b"]} for c in cflt] if cflt else []),
                "boundary": "结论强度按 evidence 的 key_coverage 与数量给定，不外推到未检索材料",
            }
            conclusions.append(concl)
            if level == "weak":
                gaps.append({"subquestion_id": sub, "code": "R_WEAK_EVIDENCE", "basis": "weak_evidence_only",
                             "gap": f"子问题「{s['text']}」在给定语料内仅有弱证据（无直接证据或间接证据不足两条）",
                             "evidence_ids": [e["evidence_id"] for e in ev],
                             "missing_evidence": s.get("evidence_requirement") or "需要与该子问题直接对应的原始定量研究",
                             "plan_id": next((p["plan_id"] for p in plan if p["subquestion_id"] == sub), ""),
                             "next_action": "按 plan_id 换来源重查或补充该类型材料；空白只声明到检索缺口，不写成领域空白"})
        else:
            conclusions.append({
                "conclusion_id": f"K{len(conclusions) + 1:02d}",
                "subquestion_id": sub,
                "statement": "",
                "sentence_map": [],
                "evidence_ids": [],
                "support_level": "no_evidence",
                "conflict_ids": [],
                "dual_column": [],
                "boundary": "该子问题在现有语料内没有达阈值证据，不写结论",
            })
            gaps.append({"subquestion_id": sub, "code": "R_NO_EVIDENCE", "basis": "zero_evidence",
                         "gap": f"子问题「{s['text']}」在给定语料内无达阈值证据",
                         "evidence_ids": [],
                         "missing_evidence": s.get("evidence_requirement") or "需要能报告该指标/对照的原始研究或数据表",
                         "plan_id": next((p["plan_id"] for p in plan if p["subquestion_id"] == sub), ""),
                         "next_action": "按 plan_id 换来源重查，或补充该类型材料；空白只声明到检索缺口，不写成领域空白"})
    return conclusions, gaps


def build_proposals(case: dict, gaps: list, conflicts: list, rows: list, subquestions: list) -> tuple:
    """边界明确的研究建议：逐条引用 evidence_id/conflict/gap 与 constraints，不声称完成实验。"""
    req = first_present(case, "proposal_request", "proposal", "bounded_proposal", default=None)
    if not isinstance(req, dict):
        return []  # 案例未请求边界化研究建议：保持为空，不虚构建议
    constraints = case.get("constraints") if isinstance(case.get("constraints"), dict) else {}
    refs = [{"ref": k, "value": constraints[k]} for k in sorted(constraints)] or \
        [{"ref": "unspecified", "value": "case.json 未提供 constraints，建议不得假定任何额外预算/范围"}]
    defaults = ["不声称已完成实验", "不声称覆盖全局完备性", "不越过给定来源与约束范围"]
    boundaries = defaults + [str(b) for b in as_list((req or {}).get("boundaries", []))]
    sub_text = {s["subquestion_id"]: s["text"] for s in subquestions}
    items = []
    for g in gaps:
        kind = "fill_zero_gap" if g["code"] == "R_NO_EVIDENCE" else "strengthen_weak"
        items.append({"proposal_id": f"PR{len(items) + 1:02d}", "kind": kind,
                      "statement": (f"检索缺口 {g['subquestion_id']}（{g['code']}）在给定语料内"
                                    f"{'无达阈值证据' if g['code'] == 'R_NO_EVIDENCE' else '仅有弱证据'}；"
                                    f"建议在约束范围内优先按 {g['plan_id']} 的检索式补充{g['missing_evidence']}，"
                                    f"取得证据后再评估是否扩展来源范围。"),
                      "basis": {"gap_subquestion_id": g["subquestion_id"],
                                "gap_code": g["code"],
                                "evidence_ids": g.get("evidence_ids", []), "conflict_ids": []},
                      "constraint_refs": refs, "not_claimed": boundaries})
    for c in conflicts[:3]:
        items.append({"proposal_id": f"PR{len(items) + 1:02d}", "kind": "recheck_conflict",
                      "statement": (f"证据 {c['side_a']['evidence_id']} 与 {c['side_b']['evidence_id']} 在 "
                                    f"{sub_text.get(c['subquestion_id'], c['subquestion_id'])} 上方向不一致"
                                    f"（{c['conflict_id']}，{c['type']}）；建议在约束范围内复核双方测试口径"
                                    f"（材料/剂量/终点）后再作取舍，本轮已双列呈现、不择一。"),
                      "basis": {"gap_subquestion_id": "", "gap_code": "",
                                "evidence_ids": [c["side_a"]["evidence_id"], c["side_b"]["evidence_id"]],
                                "conflict_ids": [c["conflict_id"]]},
                      "constraint_refs": refs, "not_claimed": boundaries})
    if not items and rows:
        top = sorted(rows, key=lambda r: (-r["key_coverage"], r["evidence_id"]))[:3]
        ids = [r["evidence_id"] for r in top]
        subs = sorted({r["subquestion_id"] for r in top})
        items.append({"proposal_id": "PR01", "kind": "next_step",
                      "statement": (f"现有最强证据 {ids} 支撑子问题 {subs} 的结论；建议在约束范围内"
                                    f"以相同口径复现验证，再决定是否扩展到未覆盖来源。"),
                      "basis": {"gap_subquestion_id": "", "gap_code": "",
                                "evidence_ids": ids, "conflict_ids": []},
                      "constraint_refs": refs, "not_claimed": boundaries})
    return items[:5]


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    facets, cfg = rules["facets"], rules["plan"]
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res._contract_orig.keys()))

    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).parent
    bases = path_bases(work, case_dir)
    sources = collect_sources(case, bases)
    records = collect_records(case, bases, sources)
    question = parse_question(case, task_text)
    res.log("intake", sources=len(sources), records=len(records), question_chars=len(question["text"]))

    if not question["text"].strip() and not build_subquestions_from_case(case):
        res.set("research_plan", {"subquestions": [], "plan": []})
        res.set("evidence_table", [])
        res.set("conclusions", [])
        res.status["overall"] = "empty"
        res.unmet("研究问题", "case.json 与 task.md 均未给出研究问题，无法拆解与规划")
        res.done("盘点")
        return res

    # ---- A. 拆解：显式子问题 > 要素模板 > 通用回退 ----
    explicit = build_subquestions_from_case(case)
    facet_state = detect_facets(question["text"], facets)
    if explicit:
        subquestions = explicit
        decomp_source = "case_declared"
        facet_state["missing_required"] = []  # 显式子问题优先，不追加要素澄清假子问题
    else:
        subquestions = build_subquestions_templates(question, facet_state, facets, cfg)
        if subquestions:
            decomp_source = "facet_templates"
        else:
            subquestions = generic_subquestion(question, cfg)
            decomp_source = "generic_fallback"
    plan = build_plan(subquestions, cfg)
    res.log("decompose", source=decomp_source, facets=sorted(facet_state["present"].keys()),
            subquestions=len(subquestions), plan=len(plan))

    # ---- B. 前瞻协议：先落盘计划文件，再逐条筛选 ----
    protocol_block = build_protocol_block(case, cfg)
    constraints = case.get("constraints") if isinstance(case.get("constraints"), dict) else {}
    plan_doc = {"case_id": res.case_id, "skill": SKILL,
                "question": question["text"], "question_source": question["source"],
                "subquestions": subquestions, "plan": plan,
                "protocol": protocol_block, "constraints": constraints,
                "screening_order": [r["record_id"] for r in records],
                "prospective_note": "本文件先于筛选、检索与打分落盘（prospective）；执行明细见 result.json 的 evidence.execution"}
    artifacts = []
    for _label, base in (("work", work), ("out", Path(args.out))):
        try:
            write_json(base / "plan.json", plan_doc)
            artifacts = ["plan.json"]
            break
        except OSError:
            continue  # work 不可写时静默回退到 out；位置差异不写入结果，保证跨环境字节一致
    if not artifacts:
        res.note("plan.json 落盘失败，计划仍完整保存在 result.json 的 research_plan 中")
    res.log("plan_written", artifacts=artifacts)

    # ---- C. 筛选 + 检索 ----
    trace = []
    for rec in records:
        trace.append({"seq": 0, "tool": "source_extract",
                      "params": {"record_id": rec["record_id"], "source_ref": rec["source_ref"],
                                 "priority": ["inline_text", "inline_abstract", "sidecar_txt", "sidecar_ocr_txt",
                                              "pdf_extract", "docx_xml", "file_read", "missing"]},
                      "result": {"method": rec["body"]["method"], "n_chars": rec["body"]["n_chars"],
                                 "sha256": rec["body"]["sha256"], "error": rec["body"]["error"]}})
    screening_rows, screen_counts = screen_records(records, protocol_block, trace)
    included_ids = {r["record_id"] for r in screening_rows if r["outcome"] == "included"}
    search_records = [r for r in records if r["record_id"] in included_ids]
    res.log("screen", protocol=protocol_block["protocol_status"], **screen_counts)

    evidence_by_sub = execute_plan(plan, search_records, cfg, trace)
    rec_by_id = {r["record_id"]: r for r in search_records}
    rows, per_sub = build_evidence(plan, evidence_by_sub, rec_by_id, cfg, rules["direction"], trace)
    conflicts = detect_conflicts(rows, cfg)
    conclusions, gaps = synthesize(subquestions, plan, rows, conflicts, cfg)
    proposals = build_proposals(case, gaps, conflicts, rows, subquestions)
    seq = 0
    for call in trace:
        seq += 1
        call["seq"] = seq
    res.log("execute", tool_calls=len(trace), evidence=len(rows), conflicts=len(conflicts),
            gaps=len(gaps), proposals=len(proposals))

    # ---- F. 状态与不确定性 ----
    unscored = [r for r in screening_rows if r["outcome"] == "unscored_no_body"]
    mismatched = [r for r in rows if not r["quote_verified"]]
    if protocol_block["input_gap"]:
        res.unmet("筛选协议", f"{protocol_block['input_gap']['code']} — {protocol_block['input_gap']['detail']}；"
                             f"已按声明回退口径筛选（fallback_screening），不冒充已按协议筛选")
    if unscored:
        res.unmet("记录正文", f"{len(unscored)} 条记录正文不可读（{','.join(r['record_id'] for r in unscored)}），"
                             f"已按 unscored_no_body 诚实降级，不参与纳排与检索，未编造其内容")
    if mismatched:
        res.unmet("证据引文校验", f"{len(mismatched)} 条证据 quote 未通过精确子串校验，已在行内标 quote_mismatch，不静默")
    no_evidence = [c for c in conclusions if c["support_level"] == "no_evidence"]
    if not question["text"].strip() or not records:
        res.status["overall"] = "empty"
    elif not conclusions or len(no_evidence) == len(conclusions):
        res.status["overall"] = "empty"
        res.unmet("综合结论", "全部子问题在给定语料内均无达阈值证据，只保留计划与研究空白")
    else:
        res.status["overall"] = "complete"
    for g in gaps:
        res.unmet(f"子问题缺口:{g['subquestion_id']}", f"{g['code']} — {g['gap']}")
    for c in conflicts:
        res.note(f"{c['subquestion_id']} 存在{c['type']}，已双列呈现（{c['side_a']['evidence_id']} × "
                 f"{c['side_b']['evidence_id']}，conflict_id={c['conflict_id']}）")
        res.unsure(f"冲突:{c['conflict_id']}", f"{c['detail']}；双方口径差异未仲裁，以双列并列交付")
    for r in rows:
        if (r.get("direction") or {}).get("value") == "mixed":
            res.unsure(f"方向:mixed:{r['evidence_id']}", "同一句内同时含正负方向表述，已标 mixed，不参与成对冲突判定")
    if facet_state["missing_required"]:
        res.unsure("问题要素", f"问题缺必备要素 {facet_state['missing_required']}，已生成澄清子问题")
    if search_records and not rows:
        res.unsure("检索覆盖", "纳入记录非空但无证据达阈值：可能是语料与本问题不在同一主题，需扩大来源")

    # ---- 结果装配 ----
    proposals_req = bool(first_present(case, "proposal_request", "proposal", "bounded_proposal", default=None))
    payload = {
        "research_plan": {"question": question["text"], "question_source": question["source"],
                          "decomposition_source": decomp_source, "facets": facet_state,
                          "subquestions": subquestions, "plan": plan,
                          "protocol": protocol_block, "constraints": constraints},
        "evidence_table": rows,
        "screening": {"protocol_status": protocol_block["protocol_status"],
                      "protocol_id": protocol_block["protocol_id"],
                      "order_note": "先落盘协议与计划（plan.json），后对每条记录逐条筛选并记 criterion_hits",
                      "counts": screen_counts, "records": screening_rows},
        "conclusions": conclusions,
        "conflicts": {"count": len(conflicts), "items": conflicts,
                      "resolution": rules["direction"].get("pair_rules", {}).get("resolution",
                                 cfg.get("conflict_rule", "")),
                      "note": "count=0 表示本轮同主题证据无方向/数值冲突，非未检查"},
        "research_gaps": {"count": len(gaps), "items": gaps, "rule": cfg.get("gap_rule", ""),
                          "note": "count=0 表示全部子问题均有达阈值证据，非未检查"},
        "proposals": {"requested": proposals_req, "count": len(proposals), "items": proposals,
                      "rule": cfg.get("proposal_rule", ""),
                      "note": ("案例未请求边界化研究建议，items 保持为空，不虚构建议" if not proposals_req
                               else "按案例 proposal_request 生成的边界明确建议")},
    }
    for key, value in payload.items():
        res.set(key, value)

    # ---- 证据记录（三段式） ----
    resolved_all = {r["evidence_id"] for r in rows}
    claimed = [c for c in conclusions if c["support_level"] != "no_evidence"]
    back_ids = sorted({eid for c in claimed for eid in c["evidence_ids"]})
    unresolved = [eid for eid in back_ids if eid not in resolved_all]
    res.evidence["sources_manifest"] = {"sources": sources, "count": len(sources),
                                        "note": "来源文件清单与解析结果；记录通过 source_path/source_id 指到来源文件"}
    res.evidence["decomposition"] = {
        "lexicon_version": facets.get("version"),
        "templates": [t.get("id") for t in facets.get("templates", [])],
        "decomposition_source": decomp_source,
        "present_facets": facet_state["present"],
        "missing_required": facet_state["missing_required"],
        "dependency_rule": facets.get("dependency_rule", cfg.get("dependency_rule", "")),
    }
    res.evidence["protocol"] = protocol_block
    res.evidence["screening"] = {"counts": screen_counts,
                                 "rule": cfg.get("screening_rule", ""),
                                 "fallback": protocol_block["fallback"],
                                 "input_gap": protocol_block["input_gap"]}
    res.evidence["execution"] = {
        "tool_calls": trace,
        "corpus": [{"record_id": r["record_id"], "title": r["title"], "year": r["year"], "type": r["type"],
                    "source": r["source"] or "local-corpus", "extraction_method": r["body"]["method"],
                    "included": r["record_id"] in included_ids} for r in records],
        "stop_condition": cfg.get("stop_condition", ""),
        "recompute": "record_score = 0.5·min(bm25,1) + 0.5·idf_coverage(keys)；每条 tool_call 的 params 可原样重放（retrieve/score/extract/verify/screen）",
    }
    res.evidence["evidence_table_map"] = {
        "rows": len(rows),
        "per_subquestion": {k: v for k, v in sorted(per_sub.items())},
        "strength_rule": f"direct ≥{cfg.get('thresholds', {}).get('direct_coverage')}，indirect ≥{cfg.get('thresholds', {}).get('indirect_coverage')}，背景信息不单列",
        "quote_rule": "证据句必须是记录正文的逐字整句；source_file + char_span（按 quote_field 字段内字符区间）+ 精确子串校验；binary 来源（PDF/DOCX）以 body_sha256 锚定提取文本",
    }
    res.evidence["backlink"] = {
        "rule": "回链覆盖率 = 引用了 evidence_id 的结论数 / 非 no_evidence 结论数；引用 id 必须能在 evidence_table 解析且 quote 校验通过",
        "conclusions_total": len(claimed),
        "conclusions_with_evidence": len([c for c in claimed if c["evidence_ids"]]),
        "coverage": rounded(len([c for c in claimed if c["evidence_ids"]]) / len(claimed)) if claimed else 1.0,
        "referenced_ids": back_ids,
        "unresolved_ids": unresolved,
        "quote_verified_rows": len([r for r in rows if r["quote_verified"]]),
        "quote_rows_total": len(rows),
    }
    res.evidence["conflicts"] = {"rule": rules["direction"].get("pair_rules", {}).get("resolution",
                                 cfg.get("conflict_rule", "")), "items": conflicts}
    res.evidence["proposals"] = {"requested": bool(first_present(case, "proposal_request", "proposal", "bounded_proposal",
                                                 default=None)),
                                 "rule": cfg.get("proposal_rule", ""), "items": proposals}
    res.evidence["artifacts"] = artifacts
    res.evidence["plan_file"] = {"file": "plan.json", "prospective": True,
                                 "note": "计划/协议先于筛选与打分落盘（优先 work 目录，不可写时回退 out 目录）"}
    res.evidence["scope"] = {"sources": cfg.get("sources", ["local-corpus"]),
                             "note": "本轮只在给定材料池内检索；不联网、不虚构工具调用" +
                                     (f"；{case['synthetic_notice']}" if case.get("synthetic_notice") else "")}

    res.bind(payload, aliases={"计划": "research_plan", "拆解": "research_plan", "主结果": "research_plan",
                               "证据表": "evidence_table", "结论": "conclusions",
                               "证据记录": "evidence", "状态说明": "status"})
    bind_contract(res, payload, rules["projection"])
    res.done("问题拆解", "前瞻协议落盘", "逐条筛选留痕", "检索计划", "多步执行与记录", "证据表构建",
             "结论回链", "冲突与空白分析")
    if proposals:
        res.done("边界化研究建议")
    return res


def bind_contract(res: Result, payload: dict, projection: dict):
    rules = projection.get("rules", [])
    virtual = {"evidence": res.evidence_view, "status": lambda: res.status, "uncertainty": lambda: res.uncertainty}
    for key in list(res.contract.keys()):
        if key in res.fields:
            continue
        kn = re.sub(r"[_\s]", "", norm(key))
        target = None
        for rule in rules:
            if any(re.sub(r"[_\s]", "", norm(kw)) in kn for kw in rule.get("keywords", [])):
                target = rule.get("projection")
                break
        if target in payload:
            res.set(key, {"projection_of": target,
                          "note": "该契约字段未单独定义，按关键词投影到同名产物分区（内容为真实计算结果）",
                          "data": payload[target]})
            res.note(f"契约字段 {key} 投影自 {target}")
        elif target in virtual:
            res.set(key, virtual[target]())
            res.note(f"契约字段 {key} 投影自三段式 {target}")
        else:
            res.unmet(key, "契约字段无对应产物分区，按规则显式声明缺口，未以推测值填充")


def _write_input_error(args, exc: Exception) -> int:
    payload = {
        "case_id": "UNKNOWN-CASE",
        "skill": {"name": SKILL, "version": "0.1.0"},
        "research_plan": {"subquestions": [], "plan": []},
        "evidence_table": [],
        "conclusions": [{"code": "R_INPUT_ERROR", "reason": str(exc)}],
        "evidence": {"error": f"{type(exc).__name__}: {exc}"},
        "status": {"overall": "input_error", "completed": [],
                   "unmet": [{"item": "case.json", "reason": str(exc)}],
                   "notes": ["输入不可读即失败并保留错误原文，不产出伪造结果"]},
        "uncertainty": [],
    }
    out = Path(args.out) / "result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": False, "result": str(out), "error": str(exc)}, ensure_ascii=False))
    return 2


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.self_check:
        from kit import self_check

        return self_check()
    try:
        case = load_case(args.case)
    except Exception as exc:  # noqa: BLE001
        return _write_input_error(args, exc)
    task_text = load_task_text(args, case) or read_text(Path(args.work or ".") / "task.md")
    res = run(case, task_text, args)
    payload = res.build()
    if args.dry_run:
        print(json.dumps({"case_id": payload["case_id"], "status": payload["status"]["overall"],
                          "subquestions": len(payload.get("research_plan", {}).get("subquestions", []))}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    try:
        from render_report import render_file
        render_file(path, Path(args.out) / "report.md")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"report_warning": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "subquestions": len(payload.get("research_plan", {}).get("subquestions", [])),
                      "screened": payload.get("screening", {}).get("counts", {}).get("screened", 0),
                      "evidence": len(payload.get("evidence_table", [])),
                      "conflicts": payload.get("conflicts", {}).get("count", 0),
                      "gaps": payload.get("research_gaps", {}).get("count", 0)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
