#!/usr/bin/env python3
"""050 系统性文献综述 —— 主流程（纯标准库 + office_min 生成 PRISMA 数据表）。

输入：case.json（PICO 研究问题 + sources/records 双结构题录 + 前瞻协议 + 约束）+ task.md
输出：out/result.json + out/prisma_flow.xlsx（四阶段计数 + 排除原因 + 纳入清单 + 质量量表）
      以及 protocol.json（前瞻协议先于筛选落盘；work 不可写时回退 out）

七环节（每条都能被别人沿路径复跑）：
  0. 摄入：sources[]（来源文件清单）与 records[]（记录）双结构建模；正文按 内联 text →
     内联 abstract → 侧车 .txt/.ocr.txt → 自研 stdlib PDF 提取（scripts/pdf_min.py）→
     DOCX zip+xml → 文本文件直读 → 诚实 missing 取数，逐条记录 extraction_method + SHA-256 + 字符数。
  1. 前瞻协议：case 提供的纳排条款/主因顺序/检索要素块先写入 protocol.json，再逐条筛选；
     协议缺失记 input_gap（R_PROTOCOL_MISSING）并声明回退口径（仅结构条款），筛选行 fallback=true。
  2. 检索式：PICO 要素块 → 每块 (A OR B …) → 合成 AND 表达式 + 数据库/字段/时间窗/语言限制。
  3. 筛选与计数链：题录级 → 全文级；每条记录评估该阶段全部条款并记 criterion_hits；
     多因命中按 rules 主因顺序取唯一主因（其余记 secondary_hits，不重复计数）；
     元数据缺失的条款判 undecidable → 人工复核队列，不自动排除；
     四阶段计数落档，flow_counts 自校验闭合（和差恒等式 + 每条排除恰落一桶 + 每库计数加总），
     任何断链如实 partial。
  4. 质量评价：rules 字段级量表（field/cue 两类）；缺字段或缺依据词 → not_reported：
     不计入总分与分母、不判低质量；全部条目未报告 → not_assessable。
  5. 证据矩阵：主题 × 纳入研究 → 逐字整句 + char_span 精确子串校验 + source_file/extraction_method
     + sha256_quote；同主题同上下文口径数值不一致 → 冲突双列（side_a/side_b 各带逐字引文）。
  6. 综合：结论逐句挂 evidence_id（sentence_map 一一对应）；确定性随 not_reported /
     not_assessable 如实降级；空白只从无证据主题推导；协议缺失时不虚构主题（R_PROTOCOL_NO_THEMES）。
"""

from __future__ import annotations

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
    first_present,
    load_case,
    load_json,
    load_task_text,
    norm,
    norm_title,
    parse_args,
    read_text,
    rounded,
    sha256_text,
    stable_id,
    write_json,
)
from office_min import write_xlsx  # noqa: E402
import pdf_min  # noqa: E402

SKILL = "systematic-review-prisma"
RULES = Path(__file__).resolve().parent.parent / "rules"
FULLTEXT_METHODS = {"inline_text", "sidecar_txt", "sidecar_ocr_txt", "pdf_extract", "docx_xml", "file_read"}


def load_rules() -> dict:
    return {
        "protocol": load_json(RULES / "prisma_protocol.json", default={}) or {},
        "projection": load_json(RULES / "contract_projection.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 0. 来源/记录双结构：路径解析与正文获取（复用 048 双结构摄入）
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
    """记录正文获取优先级：内联 text → 侧车 .txt/.ocr.txt → 自研 PDF 提取 → DOCX zip+xml
    → 文本文件直读 → 内联 abstract 兜底（仅当无来源路径或来源取不到正文）→ 诚实 missing。
    有来源路径的记录优先取文件全文；内联 abstract 不是全文（require_fulltext 不据此放行）。"""
    body = first_present(item, "text", "content", "body", "fulltext", default="")
    if isinstance(body, list):
        body = "\n".join(str(x) for x in body)
    body = str(body or "").strip()
    if body:
        return _body_info(body, "inline_text", "inline")
    abstract = str(first_present(item, "abstract", "summary", default="") or "").strip()
    ref = str(first_present(item, "source_path", "source_file", "path", "file", default="") or "")
    if not ref:
        if abstract:
            return _body_info(abstract, "inline_abstract", "inline")
        return _body_info("", "missing", "", "record_has_no_text_and_no_source_path")
    resolved, cands = resolve_ref(ref, bases)
    if not resolved.is_file():
        if abstract:
            return _body_info(abstract, "inline_abstract", "inline",
                              f"source_file_not_found: " + " | ".join(str(c) for c in cands[:4]))
        return _body_info("", "missing", str(resolved),
                          "source_file_not_found: " + " | ".join(str(c) for c in cands[:4]))
    suffix = resolved.suffix.lower()
    if suffix in (".pdf", ".docx", ".doc"):
        text, side, _err = _sidecar_read(resolved)
        if text:
            return _body_info(text, "sidecar_txt", side)
        if suffix == ".pdf":
            got = pdf_min.extract_pdf_bytes(resolved.read_bytes())
            if got.get("text"):
                return _body_info(got["text"], "pdf_extract", str(resolved), got.get("error", ""))
            if abstract:
                return _body_info(abstract, "inline_abstract", str(resolved),
                                  f"pdf_extract_failed: {got.get('error', 'unknown')}")
            return _body_info("", "missing", str(resolved),
                              f"pdf_extract_failed: {got.get('error', 'unknown')}")
        text, err = _extract_docx(resolved)
        if text:
            return _body_info(text, "docx_xml", str(resolved))
        if abstract:
            return _body_info(abstract, "inline_abstract", str(resolved), err or "docx_empty")
        return _body_info("", "missing", str(resolved), err or "docx_empty")
    text = read_text(resolved).strip()
    if text:
        return _body_info(text, "file_read", str(resolved))
    if abstract:
        return _body_info(abstract, "inline_abstract", str(resolved), "source_file_empty_or_unreadable")
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
                entry["n_bytes"] = resolved.stat().st_size
            except OSError:
                entry["sha256"] = ""
        out.append(entry)
    return out


def collect_records(case: dict, bases: list, sources: list) -> list:
    by_sid = {s["source_id"]: s for s in sources}
    out = []
    raw = first_present(case, "records", "literature", "papers", "candidates", "materials", "corpus", default=[])
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
        keywords = [str(k) for k in as_list(first_present(item, "keywords", default=[]))]
        out.append({
            "record_id": rid, "title": title, "abstract": abstract,
            "text": bodyinfo["text"], "body": bodyinfo,
            "keywords": keywords,
            "source_ref": ref, "source_id": sid, "source_file": bodyinfo["source_file"],
            "year": year,
            "authors": [str(a) for a in as_list(first_present(item, "authors", "author", default=[]))],
            "venue": str(first_present(item, "venue", "journal", "container", default="") or ""),
            "doi": str(first_present(item, "doi", default="") or ""),
            "type": str(first_present(item, "type", "pub_type", default="") or ""),
            "language": str(first_present(item, "language", "lang", default="") or ""),
            "source": str(first_present(item, "source", "database", default="") or "local-corpus"),
            "sample_size": str(first_present(item, "sample_size", "sample", default="") or "").strip(),
            "methodology": str(first_present(item, "methodology", "methods_note", "design", default="") or "").strip(),
            "has_fulltext": bodyinfo["method"] in FULLTEXT_METHODS,
        })
    return out


def text_of(rec: dict, with_body: bool = False) -> str:
    parts = [rec["title"], rec["abstract"], " ".join(rec["keywords"])]
    if with_body:
        parts.append(rec["text"])
    return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------------------
# 1. 前瞻协议（先落盘，后逐条筛选）
# --------------------------------------------------------------------------------------
def normalize_clause(raw, idx: int) -> dict:
    if not isinstance(raw, dict):
        raw = {"id": f"C{idx}", "type": "keyword_any", "value": [str(raw)], "label": str(raw)}
    return {
        "id": str(first_present(raw, "id", "criterion_id", "clause_id", default=f"C{idx}")),
        "stage": str(first_present(raw, "stage", default="title_abstract")),
        "type": str(first_present(raw, "type", "kind", default="")),
        "value": raw.get("value", raw.get("values", raw.get("terms", ""))),
        "label": str(first_present(raw, "label", "text", "description", default="") or ""),
    }


def build_protocol_block(case: dict, rules: dict) -> dict:
    """case.protocol（前瞻注册）> rules 默认条款的回退声明；返回统一协议块。"""
    proto_rules = rules["protocol"]
    raw = first_present(case, "protocol", "review_protocol", "screening_protocol", "search_protocol", default=None)
    base = {"version": proto_rules.get("version"), "flow_stages": proto_rules.get("flow_stages", []),
            "manual_review_policy": proto_rules.get("manual_review_policy", ""),
            "screening_policy": proto_rules.get("screening_policy", ""),
            "sentence_terminators": proto_rules.get("sentence_terminators", ["。", "！", "？", "!", "?"]),
            "split_ascii_period": bool(proto_rules.get("split_ascii_period", True))}
    if isinstance(raw, dict) and as_list(first_present(raw, "criteria", "clauses", default=[])):
        criteria = [normalize_clause(c, i + 1) for i, c in enumerate(as_list(raw["criteria"] if "criteria" in raw else raw["clauses"]))]
        prospective = bool(first_present(raw, "prospective", default=True))
        reason_codes = dict(proto_rules.get("reason_codes", {}))
        reason_codes.update({str(k): str(v) for k, v in (raw.get("reason_codes") or {}).items()})
        order = [str(x) for x in as_list(first_present(raw, "primary_reason_order", "primary_order", default=[]))]
        if not order:
            order = list(proto_rules.get("primary_reason_rule", {}).get("order", []))
        themes = raw.get("themes") if isinstance(raw.get("themes"), list) and raw.get("themes") else proto_rules.get("themes", [])
        # 显式给出 context_vocabulary（即使为空，表示案例协议未声明可比口径 → 不判数值冲突）优先于 rules 默认
        vocab = raw.get("context_vocabulary") if isinstance(raw.get("context_vocabulary"), dict) \
            else proto_rules.get("context_vocabulary", {})
        blocks = raw.get("search_blocks") if isinstance(raw.get("search_blocks"), list) and raw.get("search_blocks") \
            else proto_rules.get("search_blocks", [])
        limits = raw.get("limits") if isinstance(raw.get("limits"), dict) and raw.get("limits") else proto_rules.get("limits", {})
        return {**base,
                "protocol_status": "registered_prospective" if prospective else "registered_not_prospective",
                "protocol_id": str(first_present(raw, "protocol_id", "id", default="PROTO")),
                "title": str(first_present(raw, "title", default="") or ""),
                "registered_at": str(first_present(raw, "registered_at", default="") or ""),
                "prospective": prospective, "criteria": criteria, "reason_codes": reason_codes,
                "primary_reason_order": order,
                "primary_reason_note": str(first_present(raw, "primary_reason_note", default="")
                                           or proto_rules.get("primary_reason_rule", {}).get("note", "")),
                "themes": themes, "context_vocabulary": vocab, "search_blocks": blocks, "limits": limits,
                "strong_min_high": int(first_present(raw, "strong_min_high", default=proto_rules.get("strong_min_high", 2))),
                "source": "case.protocol", "input_gap": None,
                "fallback_note": "", "fallback": False}
    fb = proto_rules.get("fallback_screening") or {}
    criteria = [normalize_clause(c, i + 1) for i, c in enumerate(as_list(fb.get("criteria", [])))]
    reason_codes = dict(proto_rules.get("reason_codes", {}))
    reason_codes.update({str(k): str(v) for k, v in (fb.get("reason_codes") or {}).items()})
    return {**base,
            "protocol_status": "missing_fallback_declared",
            "protocol_id": "", "title": "", "registered_at": "", "prospective": False,
            "criteria": criteria, "reason_codes": reason_codes,
            "primary_reason_order": [str(x) for x in as_list(fb.get("primary_order", []))],
            "primary_reason_note": "回退口径主因顺序见 fallback_screening.primary_order。",
            "themes": [], "context_vocabulary": {},
            "search_blocks": proto_rules.get("search_blocks", []), "limits": proto_rules.get("limits", {}),
            "strong_min_high": int(proto_rules.get("strong_min_high", 2)),
            "source": "input_gap",
            "input_gap": {"code": "R_PROTOCOL_MISSING",
                          "detail": "case.json 未提供前瞻性纳排协议（纳排条款、主因顺序），无法证明『先注册后筛选』",
                          "fallback_note": str(fb.get("note", "")),
                          "declaration": "以下筛选按声明的回退口径执行（仅结构可行性条款），每条记录 fallback=true，"
                                         "并整体进入人工复核队列；不冒充『已按注册协议筛选』"},
            "fallback_note": str(fb.get("note", "")), "fallback": True}


# --------------------------------------------------------------------------------------
# 2. 检索式
# --------------------------------------------------------------------------------------
def build_search_strategy(question: str, pblock: dict, records: list) -> dict:
    qn = norm(question)
    blocks, present = [], []
    for block in pblock.get("search_blocks", []):
        terms = [str(t) for t in as_list(block.get("terms", []))]
        hits = [t for t in terms if norm(t) and norm(t) in qn]
        if hits or block.get("required"):
            present.append(block.get("id", ""))
            blocks.append({
                "block_id": block.get("id", ""),
                "label": block.get("label", ""),
                "matched_in_question": sorted({norm(h) for h in hits}),
                "terms": terms,
                "boolean": "(" + " OR ".join(terms[:6]) + ")",
                "field_scope": block.get("fields", ["title", "abstract", "keywords"]),
            })
    limits = pblock.get("limits", {})
    expression = " AND ".join(b["boolean"] for b in blocks) if blocks else ""
    per_source = {}
    for rec in records:
        key = rec["source"] or "local-corpus"
        per_source[key] = per_source.get(key, 0) + 1
    return {
        "question": question,
        "elements": {b["block_id"]: b["matched_in_question"] for b in blocks},
        "blocks": blocks,
        "expression": expression,
        "limits": limits,
        "databases": sorted(per_source),
        "records_per_source": {k: per_source[k] for k in sorted(per_source)},
        "protocol_status": pblock["protocol_status"],
        "prospective": pblock["prospective"],
        "executed_at": pblock.get("limits", {}).get("search_executed_at", "") or "",
        "note": "检索式只在本轮给定的题录集上执行；未联网、未声称覆盖数据库全量",
    }


# --------------------------------------------------------------------------------------
# 3. 去重 + 两轮筛选 + 计数链机器
# --------------------------------------------------------------------------------------
def dedupe(records: list) -> tuple:
    seen_doi, seen_title, kept, dropped = {}, {}, [], []
    for rec in records:
        doi_key = norm(rec["doi"]).replace("https://doi.org/", "")
        title_key = norm_title(rec["title"]) + "|" + str(rec["year"] or "")
        code = ""
        if doi_key and doi_key in seen_doi:
            code = "R_DUP_DOI"
        elif title_key and title_key in seen_title:
            code = "R_DUP_TITLE"
        if code:
            dropped.append({"record_id": rec["record_id"], "title": rec["title"], "code": code,
                            "duplicate_of": seen_doi.get(doi_key) or seen_title.get(title_key)})
            continue
        if doi_key:
            seen_doi[doi_key] = rec["record_id"]
        seen_title[title_key] = rec["record_id"]
        kept.append(rec)
    return kept, dropped


def evaluate_clause(cl: dict, rec: dict, stage: str) -> tuple:
    """返回 (state, detail)：state ∈ pass / fail / undecidable。元数据缺失不判 fail。"""
    ctype = cl.get("type", "")
    val = cl.get("value")
    if ctype == "year_min" or ctype == "year_max":
        try:
            lim = int(val)
        except (TypeError, ValueError):
            return "undecidable", f"条款 {cl['id']} 的 value 非整数（配置错误），不猜测"
        if rec["year"] is None:
            return "undecidable", "发表年份字段缺失，条款不可判定（转人工复核）"
        if ctype == "year_min":
            return ("pass", f"年份 {rec['year']} ≥ {lim}") if rec["year"] >= lim else ("fail", f"年份 {rec['year']} 早于 {lim}")
        return ("pass", f"年份 {rec['year']} ≤ {lim}") if rec["year"] <= lim else ("fail", f"年份 {rec['year']} 晚于 {lim}")
    if ctype == "type_in" or ctype == "language_in":
        allowed = [norm(x) for x in as_list(val)]
        got = norm(rec["type"] if ctype == "type_in" else rec["language"])
        if not got:
            return "undecidable", f"{'文献类型' if ctype == 'type_in' else '语言'}字段缺失，条款不可判定（转人工复核）"
        if got not in allowed:
            name = "文献类型" if ctype == "type_in" else "语言"
            return "fail", f"{name}「{got}」不在允许集合"
        return "pass", f"{('文献类型' if ctype == 'type_in' else '语言')}在允许集合内"
    if ctype == "require_abstract":
        if rec["abstract"]:
            return "pass", f"摘要 {len(rec['abstract'])} 字符"
        if rec["text"]:
            return "pass", f"无独立摘要字段，但正文可读（{len(rec['text'])} 字符）"
        return "fail", "无摘要且正文不可得，题录级不可判定（不可判定≠不相关）"
    if ctype == "require_fulltext":
        if rec["has_fulltext"]:
            return "pass", f"正文 {len(rec['text'])} 字符（{rec['body']['method']}）"
        return "fail", "全文不可得（内联/侧车/PDF/DOCX/直读均未取得正文）"
    if ctype in ("keyword_any", "keyword_all", "outcome_reported"):
        scope = text_of(rec, with_body=(stage == "full_text" or ctype == "outcome_reported"))
        kws = [norm(x) for x in as_list(val)]
        if not norm(scope):
            return "undecidable", "题录与正文均无文本，条款不可判定（转人工复核）"
        hits = [k for k in kws if k and k in norm(scope)]
        if ctype == "keyword_all":
            missing = [k for k in kws if k not in hits]
            return ("pass", f"全部要素命中") if not missing else ("fail", "要素未全部命中")
        if hits:
            return "pass", "命中 " + ",".join(hits[:4])
        why = "未报告可比结局指标" if ctype == "outcome_reported" else "对象/方法/结局要素均未命中"
        return "fail", why
    if ctype == "field_present":
        field = str(val or "")
        got = str(first_present(rec, field, default="") or "").strip()
        if got:
            return "pass", f"字段 {field} 已报告"
        return "undecidable", f"字段 {field} 未报告（不据此自动排除，转人工复核）"
    return "undecidable", f"未知条款类型 {ctype or '∅'}（不猜测，转人工复核）"


def screen_records(records: list, pblock: dict) -> tuple:
    """逐条评估该阶段全部条款：命中全记，主因唯一（规则顺序），undecidable 转人工复核。"""
    criteria = pblock["criteria"]
    codes = pblock["reason_codes"]
    order = pblock["primary_reason_order"]
    seq = {c["id"]: i for i, c in enumerate(criteria)}

    def rank(cid: str) -> tuple:
        return (order.index(cid) if cid in order else len(order), seq.get(cid, 10 ** 6))

    def reason_text(cid: str, detail: str) -> str:
        base = codes.get(cid) or next((c.get("label") for c in criteria if c["id"] == cid), "") or cid
        if detail and base and base not in detail:
            return f"{detail}（{base}）"
        return detail or base

    rows, queue = [], []
    is_fallback = bool(pblock["fallback"])
    for rec in records:
        row = {"record_id": rec["record_id"], "title": rec["title"], "year": rec["year"],
               "type": rec["type"], "language": rec["language"], "source": rec["source"],
               "source_file": rec["source_file"], "extraction_method": rec["body"]["method"],
               "body_chars": rec["body"]["n_chars"], "has_fulltext": rec["has_fulltext"],
               "stage_decisions": [], "outcome": "included", "excluded_at_stage": "",
               "primary_criterion_id": "", "secondary_hits": [],
               "needs_manual_review": False, "manual_review_reasons": [],
               "fallback": is_fallback}
        if is_fallback:
            row["needs_manual_review"] = True
            row["manual_review_reasons"].append("回退口径仅判结构可行性，主题相关性需人工复核")
            queue.append({"record_id": rec["record_id"], "stage": "protocol_level",
                          "criterion_ids": [], "reasons": ["协议缺失：回退口径不做主题相关性判定"]})
        for stage in ("title_abstract", "full_text"):
            stage_cls = [c for c in criteria if c.get("stage") == stage]
            hits, undec, evaluated = [], [], []
            for cl in stage_cls:
                state, detail = evaluate_clause(cl, rec, stage)
                evaluated.append({"criterion_id": cl["id"], "state": state, "detail": detail})
                if state == "fail":
                    hits.append((cl["id"], detail))
                elif state == "undecidable":
                    undec.append({"criterion_id": cl["id"], "reason": detail})
            decision = "exclude" if hits else "include"
            primary, secondary = "", []
            if hits:
                ids = [cid for cid, _ in hits]
                primary = min(ids, key=rank)
                secondary = [cid for cid in ids if cid != primary]
            entry = {"stage": stage, "decision": decision,
                     "evaluated_criteria": [e["criterion_id"] for e in evaluated],
                     "criterion_hits": [{"criterion_id": cid, "reason": reason_text(cid, d)} for cid, d in hits],
                     "primary_criterion_id": primary, "secondary_hits": secondary,
                     "undecidable": undec,
                     "reason": (reason_text(primary, next(d for cid, d in hits if cid == primary)) if hits
                                else "通过该阶段全部条款" + ("；部分条款不可判定，转人工复核" if undec else ""))}
            row["stage_decisions"].append(entry)
            if undec:
                row["needs_manual_review"] = True
                row["manual_review_reasons"].append(
                    f"{stage} 阶段条款不可判定：" + ",".join(u["criterion_id"] for u in undec))
                queue.append({"record_id": rec["record_id"], "stage": stage,
                              "criterion_ids": [u["criterion_id"] for u in undec],
                              "reasons": [u["reason"] for u in undec]})
            if hits:
                row["outcome"] = "excluded"
                row["excluded_at_stage"] = stage
                row["primary_criterion_id"] = primary
                row["secondary_hits"] = secondary
                break
        rows.append(row)
    return rows, queue


def build_flow(records: list, dropped: list, rows: list, included: list, pblock: dict) -> dict:
    """PRISMA 四阶段计数 + flow_counts 自校验闭合（和差恒等式 + 一桶一条 + 每库加总）。"""
    codes = pblock["reason_codes"]

    def reason_of(cid: str) -> str:
        return codes.get(cid) or cid

    def buckets(stage: str) -> list:
        agg, seen_in_bucket = {}, set()
        for r in rows:
            if r["excluded_at_stage"] != stage:
                continue
            cid = r["primary_criterion_id"]
            bucket = agg.setdefault(cid, [])
            if r["record_id"] not in seen_in_bucket:
                bucket.append(r["record_id"])
                seen_in_bucket.add(r["record_id"])
        return [{"criterion_id": k, "reason": reason_of(k), "count": len(v), "record_ids": sorted(v)}
                for k, v in sorted(agg.items())]

    per_source = {}
    for rec in records:
        key = rec["source"] or "local-corpus"
        per_source[key] = per_source.get(key, 0) + 1
    identified, ndup = len(records), len(dropped)
    after = identified - ndup
    ta_rows = [r for r in rows if r["excluded_at_stage"] == "title_abstract"]
    ft_rows = [r for r in rows if r["excluded_at_stage"] == "full_text"]
    ta_buckets, ft_buckets = buckets("title_abstract"), buckets("full_text")
    assessed = after - len(ta_rows)
    flow = {
        "stages": pblock.get("flow_stages", []),
        "identification": {"records_identified": identified,
                           "per_source": {k: per_source[k] for k in sorted(per_source)},
                           "duplicates_removed": ndup, "duplicates": dropped,
                           "records_after_dedup": after},
        "screening": {"records_screened": after, "records_excluded": len(ta_rows),
                      "exclusion_reasons": ta_buckets},
        "eligibility": {"records_assessed": assessed, "records_excluded": len(ft_rows),
                        "exclusion_reasons": ft_buckets},
        "included": {"records_included": len(included),
                     "record_ids": sorted(r["record_id"] for r in included)},
    }
    checks = []

    def add(cid, identity, ok, detail):
        checks.append({"id": cid, "identity": identity, "ok": bool(ok), "detail": detail})

    add("I1", "identified = duplicates_removed + after_dedup",
        identified == ndup + after, f"{identified} = {ndup} + {after}")
    add("I2", "after_dedup = excluded_title_abstract + assessed_full_text",
        after == len(ta_rows) + assessed, f"{after} = {len(ta_rows)} + {assessed}")
    add("I3", "assessed_full_text = excluded_full_text + included",
        assessed == len(ft_rows) + len(included), f"{assessed} = {len(ft_rows)} + {len(included)}")
    ta_sum, ft_sum = sum(b["count"] for b in ta_buckets), sum(b["count"] for b in ft_buckets)
    add("I4", "sum(screening buckets) = screening.records_excluded",
        ta_sum == len(ta_rows), f"{ta_sum} = {len(ta_rows)}")
    add("I5", "sum(eligibility buckets) = eligibility.records_excluded",
        ft_sum == len(ft_rows), f"{ft_sum} = {len(ft_rows)}")
    dup_in_bucket = []
    for stage, bk in (("title_abstract", ta_buckets), ("full_text", ft_buckets)):
        seen = set()
        for b in bk:
            for rid in b["record_ids"]:
                if rid in seen:
                    dup_in_bucket.append(f"{stage}:{rid}")
                seen.add(rid)
    add("I6", "每条排除记录只落入其阶段的一个原因桶（主因唯一）",
        not dup_in_bucket, "无重复落桶" if not dup_in_bucket else "重复：" + ",".join(dup_in_bucket[:6]))
    add("I7", "sum(per_source) = identified",
        sum(per_source.values()) == identified, f"{sum(per_source.values())} = {identified}")
    flow["flow_counts"] = {"identified": identified, "duplicates_removed": ndup, "after_dedup": after,
                           "screened": after, "excluded_title_abstract": len(ta_rows),
                           "assessed_full_text": assessed, "excluded_full_text": len(ft_rows),
                           "included": len(included)}
    flow["consistency"] = {"checks": checks, "all_closed": all(c["ok"] for c in checks),
                           "included_check": checks[2]["ok"]}
    return flow


# --------------------------------------------------------------------------------------
# 4. 质量评价（字段级：缺字段 / 缺依据词 → not_reported，不判低质量）
# --------------------------------------------------------------------------------------
def appraise(records: list, scale: dict) -> list:
    items = scale.get("items", [])
    weights = scale.get("judgment_weight", {"yes": 2, "partial": 1, "no": 0, "unclear": 0, "not_reported": 0})
    levels = scale.get("levels", [])
    rows = []
    for rec in records:
        judged = []
        for it in items:
            kind = it.get("kind", "cue")
            base = {"item_id": it.get("id", ""), "label": it.get("label", "")}
            if kind == "field":
                field = str(it.get("field", ""))
                got = str(first_present(rec, field, default="") or "").strip()
                if got:
                    judged.append({**base, "judgment": "yes", "weight": int(weights.get("yes", 2)),
                                   "basis_terms": [got[:80]], "basis_location": f"{rec['record_id']}#field:{field}"})
                else:
                    judged.append({**base, "judgment": "not_reported", "weight": 0,
                                   "basis_terms": [], "basis_location": "字段缺失",
                                   "not_reported_reason": it.get("label_missing") or f"结构化字段 {field} 未报告"})
                continue
            cues = [norm(x) for x in as_list(it.get("cues", []))]
            scope = norm(text_of(rec, with_body=True))
            hits = sorted({k for k in cues if k and k in scope})
            min_hits = int(it.get("min_hits", 1))
            partial_min = int(it.get("partial_min_hits", 0))
            if len(hits) >= min_hits:
                judged.append({**base, "judgment": "yes", "weight": int(weights.get("yes", 2)),
                               "basis_terms": hits[:5], "basis_location": f"{rec['record_id']}#abstract/text"})
            elif partial_min and len(hits) >= partial_min:
                judged.append({**base, "judgment": "partial", "weight": int(weights.get("partial", 1)),
                               "basis_terms": hits[:5], "basis_location": f"{rec['record_id']}#abstract/text"})
            else:
                judged.append({**base, "judgment": "not_reported", "weight": 0,
                               "basis_terms": [], "basis_location": "依据词未命中",
                               "not_reported_reason": "依据词未命中：按未报告处理，不判 no/低质量"})
        reported = [j for j in judged if j["judgment"] != "not_reported"]
        score = sum(int(j["weight"]) for j in judged)
        denom = sum(int(weights.get("yes", 2)) for j in reported)
        ratio = (score / denom) if denom else None
        level = "not_assessable"
        if denom:
            for lv in levels:
                if ratio >= float(lv.get("min", 0)):
                    level = lv.get("level", "low")
                    break
        not_reported_items = [{"item_id": j["item_id"], "label": j["label"], "reason": j["not_reported_reason"]}
                              for j in judged if j["judgment"] == "not_reported"]
        rows.append({"record_id": rec["record_id"], "title": rec["title"],
                     "score": score, "max_score": denom,
                     "ratio": rounded(ratio) if ratio is not None else None,
                     "level": level, "items": judged,
                     "not_reported_items": not_reported_items,
                     "effect_on_conclusion": (scale.get("level_effect", {}).get(level)
                                              or scale.get("not_assessable_effect", ""))})
    return rows


# --------------------------------------------------------------------------------------
# 5. 证据矩阵（逐字整句 + char_span 精确校验） + 冲突双列
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


def contexts_of(text: str, vocab: dict) -> dict:
    """抽取「数据集/干预/疗程」等上下文口径的规范名（同族归一）。"""
    tn = norm(text)
    out = {}
    for group, spec in (vocab or {}).items():
        if isinstance(spec, dict):
            hits = sorted({norm(canon) for canon, terms in spec.items()
                           if any(norm(t) and norm(t) in tn for t in as_list(terms))})
        else:
            hits = sorted({norm(t) for t in as_list(spec) if norm(t) and norm(t) in tn})
            hits = [h for h in hits if not any(h != o and h in o for o in hits)]
        if hits:
            out[group] = hits
    return out


def build_evidence_matrix(records: list, themes: list, pblock: dict, appraisals: list) -> tuple:
    term = pblock.get("sentence_terminators", ["。", "！", "？", "!", "?"])
    split_period = bool(pblock.get("split_ascii_period", True))
    vocab = pblock.get("context_vocabulary", {})
    q_by_rec = {a["record_id"]: a for a in appraisals}
    rows = []
    for theme in themes:
        terms = [norm(x) for x in as_list(theme.get("terms", []))]
        for rec in records:
            best = None
            for fld in ("text", "abstract"):
                for sent in split_sentences(rec[fld], term, split_period):
                    sn = norm(sent)
                    tn = norm(rec["title"])
                    if tn and (sn == tn or sn in tn or tn in sn):
                        continue  # 题名不进引文
                    hits = [x for x in terms if x and x in sn]
                    if not hits:
                        continue
                    cov = len(hits) / max(1, len(terms))
                    if best is None or cov > best[0]:
                        best = (cov, sent, hits, fld)
            if best is None:
                continue
            cov, sent, hits, fld = best
            field_text = rec[fld]
            start = field_text.find(sent)
            end = start + len(sent) if start >= 0 else -1
            exact = start >= 0 and field_text[start:end] == sent
            # 冲突口径的上下文按「记录级」全文算（菌株/疗程等属性常在题录与全文而非单句内）
            ctx = contexts_of(text_of(rec, with_body=True), vocab)
            eid = f"M{len(rows) + 1:03d}"
            qrow = q_by_rec.get(rec["record_id"]) or {}
            rows.append({"evidence_id": eid, "theme_id": theme.get("id", ""), "theme": theme.get("label", ""),
                         "record_id": rec["record_id"], "title": rec["title"], "year": rec["year"],
                         "source_file": rec["source_file"], "extraction_method": rec["body"]["method"],
                         "quote_field": fld, "quote": sent,
                         "char_span": {"start": start, "end": end},
                         "quote_verified": bool(exact),
                         "sha256_quote": sha256_text(norm(sent)),
                         "matched_terms": sorted(hits), "coverage": rounded(cov),
                         "context": ctx,
                         "quality_level": qrow.get("level", ""),
                         "claim": theme.get("claim_template", "").format(theme=theme.get("label", ""))})
            if not exact:
                rows[-1]["quote_mismatch"] = "quote 不是该字段原文的精确子串（span 定位失败），已显式标记，不静默"
    return rows


_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(%|个百分点|pp|倍|分)")


def _conflict_side(row: dict, value: str, unit: str) -> dict:
    return {"evidence_id": row["evidence_id"], "record_id": row["record_id"],
            "source_file": row["source_file"], "quote": row["quote"],
            "quote_verified": row["quote_verified"],
            "value": value, "unit": unit, "context": row.get("context", {})}


def detect_conflicts(rows: list, vocab: dict) -> list:
    """同主题 + 记录级上下文口径家族可比（双方共有的组必须相交，且至少一组共享）
    → 同单位不同数值 = numeric_conflict，双列呈现。单方独有的组不参与判定。"""
    out, by_pair = [], {}
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if a["theme_id"] != b["theme_id"]:
                continue
            qa = [(m.group(1), m.group(2)) for m in _NUM.finditer(norm(a["quote"]))]
            qb = [(m.group(1), m.group(2)) for m in _NUM.finditer(norm(b["quote"]))]
            ca, cb = a.get("context", {}), b.get("context", {})
            shared = {}
            comparable = False
            mismatched_group = False
            for g in (vocab or {}):
                ga, gb = set(ca.get(g) or []), set(cb.get(g) or [])
                if ga and gb:
                    inter = sorted(ga & gb)
                    if inter:
                        shared[g] = inter
                        comparable = True
                    else:
                        mismatched_group = True
            hit = next(((x, y) for x in qa for y in qb if x[1] == y[1] and x[0] != y[0]), None)
            if not hit or not comparable or mismatched_group:
                continue
            key = (a["record_id"], b["record_id"], a["sha256_quote"], b["sha256_quote"])
            if key in by_pair:
                continue
            by_pair[key] = True
            out.append({"conflict_id": f"C{len(out) + 1:02d}", "type": "numeric_conflict",
                        "theme_id": a["theme_id"], "shared_context": shared,
                        "side_a": _conflict_side(a, hit[0][0], hit[0][1]),
                        "side_b": _conflict_side(b, hit[1][0], hit[1][1]),
                        "detail": f"同主题同上下文口径下数值不一致：{hit[0][0]}{hit[0][1]} vs {hit[1][0]}{hit[1][1]}",
                        "resolution": "双列并列呈现（side_a/side_b 各带逐字引文与数值），不择一写入结论；"
                                      "差异写进空白/限制说明，并以 uncertainty 标注未仲裁"})
    return out


# --------------------------------------------------------------------------------------
# 6. 综合（sentence_map 一一对应 + 确定性随证据受限降级） + 空白
# --------------------------------------------------------------------------------------
def synthesize(themes: list, matrix: list, appraisals: list, conflicts: list, pblock: dict) -> tuple:
    q_by_rec = {a["record_id"]: a for a in appraisals}
    strong_min = int(pblock.get("strong_min_high", 2))
    conclusions, gaps = [], []
    for theme in themes:
        ev = [r for r in matrix if r["theme_id"] == theme.get("id", "")]
        if not ev:
            tid = theme.get("id", "")
            gaps.append({"theme_id": tid, "code": "R_THEME_NO_EVIDENCE",
                         "gap": f"主题「{theme.get('label', '')}」在纳入研究中没有可引用的证据句",
                         "missing_evidence": theme.get("missing_evidence", "需要能报告该主题结局的原始研究")})
            conclusions.append({"conclusion_id": f"K{len(conclusions) + 1:02d}", "theme_id": tid,
                                "statement": "", "sentence_map": [], "evidence_ids": [],
                                "support_level": "no_evidence", "certainty": {"note": "无证据不写结论"},
                                "conflict_ids": [], "dual_column": [], "quality_distribution": {},
                                "boundary": "无证据不写结论"})
            continue
        picks = ev[:3]
        levels = [q_by_rec[r["record_id"]]["level"] for r in ev if r["record_id"] in q_by_rec]
        missing_map = {}
        for r in ev:
            qrow = q_by_rec.get(r["record_id"])
            if qrow and qrow["not_reported_items"]:
                missing_map[r["record_id"]] = [i["item_id"] for i in qrow["not_reported_items"]]
        not_assess = any(x == "not_assessable" for x in levels)
        cflt = [c for c in conflicts if c["theme_id"] == theme.get("id", "")]
        high_n = levels.count("high")
        if cflt:
            level = "conflicting"
        elif high_n >= strong_min and not not_assess:
            level = "strong"
        elif any(x in ("high", "moderate") for x in levels):
            level = "moderate"
        else:
            level = "weak"
        cap_note = ""
        if not cflt and not_assess and level in ("strong", "moderate"):
            level = "weak"
            cap_note = "存在 not_assessable 纳入记录（条目均未报告，无法分级），确定性降级"
        elif not cflt and missing_map and level == "strong":
            level = "moderate"
            cap_note = "纳入研究存在未报告条目（not_reported），确定性降级"
        certainty = {
            "backing_records": len({r["record_id"] for r in ev}),
            "quality_levels": {"high": levels.count("high"), "moderate": levels.count("moderate"),
                               "low": levels.count("low"), "not_assessable": levels.count("not_assessable")},
            "records_with_not_reported": missing_map,
            "not_assessable_records": [r["record_id"] for r in ev
                                       if q_by_rec.get(r["record_id"], {}).get("level") == "not_assessable"],
            "note": (cap_note or "确定性按已报告条目计算；未报告条目不计入低质量，但在本说明中列明")
                    + (f"；未报告条目：{json.dumps(missing_map, ensure_ascii=False)}" if missing_map else ""),
        }
        conclusions.append({
            "conclusion_id": f"K{len(conclusions) + 1:02d}",
            "theme_id": theme.get("id", ""),
            "statement": " ".join(f"{e['quote']} [{e['evidence_id']}]" for e in picks),
            "statement_rule": "结论 = 纳入研究原文整句 + 证据标记（≤3 条，逐句对应 sentence_map），不改写、不外推",
            "sentence_map": [{"evidence_id": e["evidence_id"], "record_id": e["record_id"],
                              "source_file": e["source_file"], "quote": e["quote"],
                              "char_span": e["char_span"]} for e in picks],
            "evidence_ids": [e["evidence_id"] for e in ev],
            "support_level": level,
            "certainty": certainty,
            "conflict_ids": [c["conflict_id"] for c in cflt],
            "dual_column": ([{"conflict_id": c["conflict_id"], "type": c["type"],
                              "side_a": c["side_a"], "side_b": c["side_b"]} for c in cflt] if cflt else []),
            "quality_distribution": certainty["quality_levels"],
            "boundary": "结论仅适用于本轮纳入研究覆盖的对象与结局；未纳入的来源不在本结论范围内",
        })
    if appraisals and all(a["level"] in ("low", "not_assessable") for a in appraisals):
        gaps.append({"theme_id": "", "code": "R_ALL_LOW_QUALITY",
                     "gap": "全部纳入研究在已报告条目上的质量等级为 low（或无法分级）",
                     "missing_evidence": "需要更高质量（含样本量、方法学与局限说明）的研究支撑结论"})
    return conclusions, gaps


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res._contract_orig.keys()))

    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).parent
    bases = path_bases(work, case_dir)
    sources = collect_sources(case, bases)
    records = collect_records(case, bases, sources)
    question = str(first_present(case, "research_question", "question", default="") or "").strip()
    if not question:
        question = next((l.strip() for l in task_text.splitlines() if l.strip() and not l.startswith("#")), "")[:300]
    res.log("intake", sources=len(sources), records=len(records), question_chars=len(question))

    # ---- 1. 前瞻协议：先落盘，再筛选 ----
    pblock = build_protocol_block(case, rules)
    protocol_doc = {"case_id": res.case_id, "skill": SKILL, "question": question,
                    "protocol": {k: v for k, v in pblock.items() if k != "flow_stages"},
                    "record_order": [r["record_id"] for r in records],
                    "prospective_note": "本文件先于筛选、质量评价与综合落盘（prospective）；"
                                        "执行明细见 result.json 的 screening_log 与 prisma_flow"}
    artifacts = []
    for _label, base_dir in (("work", work), ("out", Path(args.out))):
        try:
            write_json(base_dir / "protocol.json", protocol_doc)
            artifacts = ["protocol.json"]
            break
        except OSError:
            continue  # work 不可写时静默回退到 out；位置差异不写入结果，保证跨环境字节一致
    if not artifacts:
        res.note("protocol.json 落盘失败，协议仍完整保存在 result.json 的 screening_log.criteria 中")
    res.log("protocol_written", status=pblock["protocol_status"], criteria=len(pblock["criteria"]))

    if not records:
        res.set("search_strategy", {"question": question, "blocks": [], "expression": ""})
        res.set("screening_log", {"criteria": pblock["criteria"], "rows": [], "manual_review": {"queue": []}})
        res.set("prisma_flow", {"identification": {"records_identified": 0}})
        res.set("quality_appraisal", {"rows": []})
        res.set("evidence_matrix", [])
        res.set("synthesis", [])
        res.set("conflicts", [])
        res.set("research_gaps", [])
        res.status["overall"] = "empty"
        res.unmet("题录集", "case.json 未提供任何题录（records/literature/papers 均为空）")
        res.done("前瞻协议落盘", "盘点")
        return res

    # ---- 2. 检索式 + 3. 筛选与计数链 ----
    strategy = build_search_strategy(question, pblock, records)
    kept, dropped = dedupe(records)
    rows, queue = screen_records(kept, pblock)
    row_by_rec = {r["record_id"]: r for r in rows}
    included = [rec for rec in kept if row_by_rec[rec["record_id"]]["outcome"] == "included"]
    flow = build_flow(records, dropped, rows, included, pblock)
    res.log("screen", protocol=pblock["protocol_status"], identified=flow["flow_counts"]["identified"],
            excluded_ta=flow["flow_counts"]["excluded_title_abstract"],
            assessed_ft=flow["flow_counts"]["assessed_full_text"],
            included=flow["flow_counts"]["included"])

    # ---- 4. 质量评价 + 5. 证据矩阵/冲突 + 6. 综合 ----
    scale = rules["protocol"].get("quality_scale", {})
    appraisals = appraise(included, scale)
    matrix = build_evidence_matrix(included, pblock["themes"], pblock, appraisals)
    conflicts = detect_conflicts(matrix, pblock["context_vocabulary"])
    if pblock["protocol_status"] == "missing_fallback_declared":
        # 协议缺失 → 主题未定义：不虚构综合主题（R_PROTOCOL_NO_THEMES）
        conclusions, gaps = [], [{"theme_id": "", "code": "R_PROTOCOL_NO_THEMES",
                                  "gap": "协议缺失时未定义综合主题；不虚构主题，综合留空并声明",
                                  "missing_evidence": "需要案例提供含主题定义的前瞻协议"}]
    else:
        conclusions, gaps = synthesize(pblock["themes"], matrix, appraisals, conflicts, pblock)
    res.log("synthesize", evidence=len(matrix), conflicts=len(conflicts),
            conclusions=len(conclusions), gaps=len(gaps))

    closure_ok = flow["consistency"]["all_closed"]
    if not closure_ok:
        failed = [c["identity"] for c in flow["consistency"]["checks"] if not c["ok"]]
        res.unmet("计数闭合", "PRISMA 计数链断裂：" + "；".join(failed))

    # ---- PRISMA 数据表（可编辑 xlsx，四工作表） ----
    try:
        xlsx_path = Path(args.out) / "prisma_flow.xlsx"
        flow_rows = [["stage", "metric", "value"]]
        for key in ("identification", "screening", "eligibility", "included"):
            for mk, mv in flow[key].items():
                if isinstance(mv, (int, str)) and not isinstance(mv, bool):
                    flow_rows.append([key, mk, mv])
        for chk in flow["consistency"]["checks"]:
            flow_rows.append(["closure", chk["id"] + " " + chk["identity"], "ok" if chk["ok"] else "FAIL"])
        excl_rows = [["stage", "criterion_id", "reason", "count", "record_ids"]]
        for stage in ("title_abstract", "full_text"):
            for r in flow["screening" if stage == "title_abstract" else "eligibility"]["exclusion_reasons"]:
                excl_rows.append([stage, r["criterion_id"], r["reason"], r["count"], ", ".join(r["record_ids"])])
        q_by_rec = {a["record_id"]: a for a in appraisals}
        inc_rows = [["record_id", "title", "year", "type", "language", "source", "source_file",
                     "quality_level", "not_reported_items"]]
        for rec in sorted(included, key=lambda r: r["record_id"]):
            qrow = q_by_rec.get(rec["record_id"], {})
            inc_rows.append([rec["record_id"], rec["title"], rec["year"] or "", rec["type"], rec["language"],
                             rec["source"], rec["source_file"], qrow.get("level", ""),
                             ", ".join(i["item_id"] for i in qrow.get("not_reported_items", []))])
        q_rows = [["record_id", "score", "max_score", "ratio", "level", "not_reported_count"]] + [
            [a["record_id"], a["score"], a["max_score"], a["ratio"] if a["ratio"] is not None else "n/a",
             a["level"], len(a["not_reported_items"])] for a in appraisals]
        write_xlsx(xlsx_path, [
            {"name": "PRISMA_flow", "rows": flow_rows},
            {"name": "exclusions", "rows": excl_rows},
            {"name": "included", "rows": inc_rows},
            {"name": "quality", "rows": q_rows},
        ])
        with zipfile.ZipFile(xlsx_path) as zf:
            names = [n for n in zf.namelist() if n.startswith("xl/worksheets/")]
            sheet_count = len(names)
            nonempty = all(zf.getinfo(n).file_size > 100 for n in names)
        artifacts.append({"file": "prisma_flow.xlsx", "sheets": sheet_count,
                          "self_check": "xlsx 4 个工作表均非空" if (sheet_count == 4 and nonempty) else "工作表数或内容异常"})
    except Exception as exc:  # noqa: BLE001
        res.unsure("流程图数据表", f"prisma_flow.xlsx 生成失败：{type(exc).__name__}: {exc}")
        artifacts.append({"file": "prisma_flow.xlsx", "error": f"{type(exc).__name__}: {exc}"})

    # ---- 装配 ----
    manual_review = {"policy": pblock.get("manual_review_policy", ""),
                     "boundary": "undecidable 条款与回退口径不自动排除；自动筛选止步于此，队列交人工复核",
                     "queue": queue, "count": len(queue)}
    screening_log = {"criteria": pblock["criteria"], "reason_codes": pblock["reason_codes"],
                     "primary_reason_rule": {"order": pblock["primary_reason_order"],
                                             "note": pblock["primary_reason_note"]},
                     "stages": ["title_abstract", "full_text"], "rows": rows,
                     "policy": pblock.get("screening_policy", ""),
                     "protocol_status": pblock["protocol_status"], "protocol_id": pblock["protocol_id"],
                     "fallback": pblock["fallback"], "fallback_rows": sum(1 for r in rows if r["fallback"]),
                     "manual_review": manual_review}
    not_reported_total = sum(len(a["not_reported_items"]) for a in appraisals)
    quality = {"scale": scale, "rows": appraisals,
               "overall": {"high": sum(1 for a in appraisals if a["level"] == "high"),
                           "moderate": sum(1 for a in appraisals if a["level"] == "moderate"),
                           "low": sum(1 for a in appraisals if a["level"] == "low"),
                           "not_assessable": sum(1 for a in appraisals if a["level"] == "not_assessable")},
               "not_reported_total": not_reported_total,
               "boundary_note": scale.get("not_reported_rule", "")}
    payload = {
        "search_strategy": strategy,
        "screening_log": screening_log,
        "prisma_flow": flow,
        "quality_appraisal": quality,
        "evidence_matrix": matrix,
        "synthesis": conclusions,
        "conflicts": conflicts,
        "research_gaps": gaps,
    }
    for key, value in payload.items():
        res.set(key, value)

    # ---- 状态与不确定性 ----
    no_body = [rec for rec in kept if rec["body"]["method"] == "missing"]
    if pblock["input_gap"]:
        res.unmet("筛选协议", f"{pblock['input_gap']['code']} — {pblock['input_gap']['detail']}；"
                              f"已按声明回退口径筛选（fallback=true，整体进入人工复核队列），不冒充已按协议筛选")
    if pblock["protocol_status"] == "registered_not_prospective":
        res.unsure("协议前瞻性", "case 协议标记 prospective=false，筛选按协议执行但不声称前瞻注册")
    if not closure_ok:
        res.unmet("计数闭合", "flow_counts 自校验未全部通过（恒等式或落桶唯一性失败），需人工核对")
    if not included:
        res.status["overall"] = "empty"
        res.unmet("纳入研究", "两轮筛选后纳入集为空，未生成证据矩阵与结论（排除理由见 prisma_flow）")
    elif pblock["protocol_status"] == "missing_fallback_declared":
        # 协议缺失 → 主题未定义 → 综述主结果（主题综合）为空：不虚构主题；
        # 筛选与质量评价审计仍完整保留在 screening_log / quality_appraisal / prisma_flow
        res.status["overall"] = "empty"
        res.note("综述主结果为空（协议缺失不虚构主题）；筛选链、质量评价与人工复核队列仍完整交付")
    else:
        res.status["overall"] = "complete"
    for g in gaps:
        res.unmet(f"主题缺口:{g['theme_id'] or 'overall'}", f"{g['code']} — {g['gap']}")
    for c in conflicts:
        res.note(f"{c['theme_id']} 存在数值冲突（{c['side_a']['evidence_id']} × {c['side_b']['evidence_id']}，"
                 f"{c['conflict_id']}），已双列呈现不择一")
        res.unsure(f"冲突:{c['conflict_id']}", f"{c['detail']}；双方口径差异未仲裁，以双列并列交付")
    if queue:
        res.note(f"人工复核队列 {len(queue)} 条：undecidable 条款与回退口径不自动排除，边界见 screening_log.manual_review")
    if not_reported_total:
        res.unsure("质量评价受限", f"纳入研究共有 {not_reported_total} 项条目因缺字段/缺依据词记 not_reported"
                                   f"（不计入低质量）；结论确定性已按证据受限降级，详见 synthesis[].certainty")
    if no_body:
        res.note(f"{len(no_body)} 条记录正文不可得（{','.join(r['record_id'] for r in no_body)}），"
                 f"已按条款诚实排除或转人工复核，未编造其内容")
    if dropped:
        res.note(f"识别阶段删除 {len(dropped)} 条重复题录（DOI/标题归一去重）")
    if not question:
        res.unsure("研究问题", "case.json 与 task.md 均未给出研究问题，检索式为空壳")
    if not rules["protocol"].get("search_executed_at") and not pblock.get("limits", {}).get("search_executed_at"):
        res.unsure("检索执行时间", "本轮为离线给定题录集，未记录真实数据库检索时间；正式使用时应写明检索日期与数据库")

    # ---- 证据记录（三段式） ----
    claimed = [c for c in conclusions if c["support_level"] != "no_evidence"]
    back_ids = sorted({eid for c in claimed for eid in c["evidence_ids"]})
    unresolved = [eid for eid in back_ids if eid not in {r["evidence_id"] for r in matrix}]
    res.evidence["protocol"] = {k: pblock[k] for k in ("version", "protocol_status", "protocol_id", "prospective",
                                                       "criteria", "reason_codes", "primary_reason_order",
                                                       "primary_reason_note", "source", "input_gap", "fallback_note")}
    res.evidence["protocol"]["recompute"] = ("每条记录的 stage_decisions 可由 criteria 条款顺序重放；"
                                             "主因由 primary_reason_order 唯一确定；undecidable 不参与排除")
    res.evidence["sources_manifest"] = {"sources": sources, "count": len(sources),
                                        "note": "来源文件清单与解析结果；记录通过 source_path/source_id 指到来源文件"}
    res.evidence["counts"] = {"flow_counts": flow["flow_counts"], "closure_checks": flow["consistency"]["checks"],
                              "all_closed": closure_ok}
    res.evidence["matrix_map"] = {
        "rows": len(matrix),
        "per_theme": {t.get("id", ""): [r["evidence_id"] for r in matrix if r["theme_id"] == t.get("id", "")]
                      for t in pblock["themes"]},
        "quote_verified_rows": len([r for r in matrix if r["quote_verified"]]),
        "span_rule": "span = quote_field 字段内字符区间 + 整句（逐字），sha256_quote 可复核；"
                     "source_file + extraction_method 锚定提取文本",
    }
    res.evidence["backlink"] = {
        "rule": "回链覆盖率 = 引用了 evidence_id 的结论数 / 非 no_evidence 结论数；引用 id 必须能在 evidence_matrix 解析",
        "conclusions_total": len(claimed),
        "conclusions_with_evidence": len([c for c in claimed if c["evidence_ids"]]),
        "coverage": rounded(len([c for c in claimed if c["evidence_ids"]]) / len(claimed)) if claimed else 1.0,
        "referenced_ids": back_ids, "unresolved_ids": unresolved,
    }
    res.evidence["quality_boundary"] = {
        "rule": scale.get("not_reported_rule", ""),
        "not_reported_total": not_reported_total,
        "records_with_missing_fields": {a["record_id"]: [i["item_id"] for i in a["not_reported_items"]]
                                        for a in appraisals if a["not_reported_items"]},
        "not_assessable_records": [a["record_id"] for a in appraisals if a["level"] == "not_assessable"],
        "certainty_cap": "strong 且存在 not_reported → 降为 moderate；存在 not_assessable → 降为 weak；冲突 → conflicting",
    }
    res.evidence["manual_review"] = manual_review
    res.evidence["conflicts"] = {"rule": rules["protocol"].get("conflict_rule", ""), "items": conflicts}
    res.evidence["artifacts"] = artifacts
    res.evidence["plan_file"] = {"file": "protocol.json", "prospective": True,
                                 "note": "前瞻协议先于筛选与质量评价落盘（优先 work 目录，不可写时回退 out 目录）"}
    res.evidence["scope"] = {"records_seen": len(records), "no_network": True,
                             "note": "只在给定题录集上执行检索式；未联网、未声称覆盖数据库全量"}

    res.bind(payload, aliases={"检索式": "search_strategy", "筛选": "screening_log", "流程图": "prisma_flow",
                               "质量": "quality_appraisal", "证据": "evidence_matrix", "结论": "synthesis",
                               "证据记录": "evidence", "状态说明": "status"})
    bind_contract(res, payload, rules["projection"])
    res.done("前瞻协议落盘", "检索式构建", "去重与识别计数", "两轮纳排筛选（主因唯一）", "人工复核边界",
             "质量评价（not_reported 边界）", "证据矩阵与冲突双列", "PRISMA 计数链与综合结论")
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
        "search_strategy": {"expression": "", "blocks": []},
        "screening_log": [],
        "prisma_flow": {"identification": {"records_identified": 0}},
        "quality_appraisal": {"rows": []},
        "evidence_matrix": [],
        "synthesis": [{"code": "R_INPUT_ERROR", "reason": str(exc)}],
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
                          "closure": payload.get("prisma_flow", {}).get("consistency", {}).get("all_closed"),
                          "included": payload.get("prisma_flow", {}).get("included", {}).get("records_included")},
                         ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "identified": payload.get("prisma_flow", {}).get("identification", {}).get("records_identified"),
                      "included": payload.get("prisma_flow", {}).get("included", {}).get("records_included"),
                      "closure": payload.get("prisma_flow", {}).get("consistency", {}).get("all_closed"),
                      "evidence": len(payload.get("evidence_matrix", [])),
                      "conflicts": len(payload.get("conflicts", [])),
                      "gaps": len(payload.get("research_gaps", []))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
