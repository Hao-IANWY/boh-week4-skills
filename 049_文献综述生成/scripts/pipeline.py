#!/usr/bin/env python3
"""049 文献综述生成 —— 主流程（纯标准库，离线、确定性、全流程留痕）。

输入：case.json（研究问题 + 来源/记录双结构 + 前瞻协议 + 可选主题锚点 + 约束）+ task.md
输出：out/result.json + work/plan.json（协议与计划先于筛选落盘）+ review_draft.docx（可编辑草稿）

七阶段（筛选、聚类、证据、冲突、写作全部留痕，任何人可按记录复跑）：
  0. 摄入：sources[]（来源文件清单）与 records[]（记录）双结构建模；记录通过
     source_path/source_id 指到来源文件；正文按 内联 text → 内联 abstract → 侧车
     .txt/.ocr.txt → 自研 stdlib PDF 提取（scripts/pdf_min.py）→ DOCX zip+xml →
     文本文件直读 → 诚实 gap 取数，逐条记录 extraction_method + SHA-256 + 字符数。
  A. 问题与锚点：研究问题（case 优先、task.md 兜底）；案例显式声明 themes 时作为
     主题锚点（support 必须仍由记录文本命中产生），否则主题完全由证据聚类生成。
  B. 前瞻协议：纳排标准/停止条件先写入 work/plan.json，再逐条筛选；协议缺失记
     input_gap（R_PROTOCOL_MISSING）并声明回退口径，不冒充「已按协议筛选」。
  C. 筛选与去重：逐条记录记 criterion_hits；协议筛选后执行 DOI / 标题+年份去重
     （保留首见，重复改判 excluded 并记录 dedupe_of）。
  D. 主题聚类（049 特有）：从纳入记录内容提取聚类词表（CJK 极大 n 元组 + ASCII 词，
     df 达阈值）→ 词项-记录集按 Jaccard 合并成主题 → 判别性命名；主题、标签、边界
     只从这批记录长出来，不套通用框架；未聚类的记录单列不硬塞。
  E. 证据表：evidence_id + record_id + source_file + quote_field + char_span + 逐字
     quote，管线内做精确子串校验，失败标 quote_mismatch 不静默；题名不进引文。
  F. 冲突：方向词典（rules/direction_lexicon.json）同主题成对冲突双列
     （side_a/side_b 各带逐字引文、方向、强度词），不合并、不写「尚存争议」；
     同单位不同数值且共享术语的数值冲突单列。
  G. 综述：主题节每句 = 原文整句 + [E###]（强度 established/suggestive/contested）；
     证据不足的主题显式标注薄弱；综述末尾 coverage 声明（覆盖/未覆盖主题、未聚类
     记录、语料边界=池内快照非全局）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import zipfile
from difflib import SequenceMatcher
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
import pdf_min  # noqa: E402
from office_min import read_docx_text, write_docx  # noqa: E402
from normalize import normalize_case  # noqa: E402

SKILL = "literature-review-writer"
RULES = Path(__file__).resolve().parent.parent / "rules"
_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9\-_\.\+]*")


def load_rules() -> dict:
    return {
        "criteria": load_json(RULES / "review_criteria.json", default={}) or {},
        "clustering": load_json(RULES / "topic_clustering.json", default={}) or {},
        "direction": load_json(RULES / "direction_lexicon.json", default={}) or {},
        "projection": load_json(RULES / "contract_projection.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 0. 来源/记录双结构：路径解析与正文获取（与 048 同构，复制复用）
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
        # 兜底：case 未声明 sources 时扫描案例目录与 /input 的实际材料，避免把“未列出”当成“没有材料”。
        skip_names = {"case.json", "task.md", "TASK.md", "plan.json", "result.json", "report.md"}
        skip_parts = {"scripts", "rules", "examples", "__pycache__", ".git", "output", "out", "work"}
        seen_paths, scan_dirs = set(), []
        for base in ([Path(bases[0])] if bases else []) + [Path("/input")]:
            if base.is_dir() and all(str(base) != str(x) for x in scan_dirs):
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
    raw = first_present(case, "records", "literature", "papers", "materials", "candidates", "corpus", default=[])
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
        keywords = [str(k) for k in as_list(first_present(item, "keywords", "concepts", default=[]))]
        out.append({
            "record_id": rid, "title": title, "abstract": abstract,
            "text": bodyinfo["text"], "body": bodyinfo,
            "authors": [str(a) for a in as_list(first_present(item, "authors", "author", default=[]))],
            "keywords": keywords,
            "source_ref": ref, "source_id": sid,
            "source_file": bodyinfo["source_file"],
            "year": year,
            "venue": str(first_present(item, "venue", "journal", "container", default="") or ""),
            "doi": str(first_present(item, "doi", default="") or ""),
            "type": str(first_present(item, "type", "pub_type", default="") or ""),
            "language": str(first_present(item, "language", "lang", default="") or ""),
            "source": str(first_present(item, "source", "database", default="") or ""),
        })
    if not out and sources:
        # 兜底：case 只给来源文件清单、未给 records 时，把每个可读来源当作一条记录。
        for src in sources:
            if not src.get("exists"):
                continue
            ref = str(src.get("path") or "")
            info = resolve_body({"source_path": ref, "title": src.get("title", "")}, bases)
            out.append({
                "record_id": f"REC{len(out) + 1:02d}", "title": str(src.get("title") or Path(ref).stem),
                "abstract": "", "text": info["text"], "body": info, "authors": [], "keywords": [],
                "source_ref": ref, "source_id": str(src.get("source_id") or ""),
                "source_file": info["source_file"], "year": None, "venue": "", "doi": "",
                "type": str(src.get("type") or ""), "language": "", "source": "",
            })
    return out


def record_text(rec: dict) -> str:
    return "\n".join([rec["title"], rec["abstract"], rec["text"], " ".join(rec["keywords"])]).strip()


# --------------------------------------------------------------------------------------
# A. 问题与主题锚点
# --------------------------------------------------------------------------------------
def parse_question(case: dict, task_text: str) -> dict:
    q = first_present(case, "research_question", "question", "task_instruction", "instruction", default="")
    source = "case.research_question"
    if not str(q).strip():
        q = "\n".join(l for l in task_text.splitlines() if l.strip() and not l.startswith("#"))[:800]
        source = "task.md"
    return {"text": str(q).strip(), "source": source}


def collect_themes(case: dict) -> tuple:
    """案例显式声明的主题锚点（themes/review_themes/subquestions）。空列表 → 证据聚类。"""
    out = []
    raw = first_present(case, "themes", "review_themes", "declared_topics", "screening_subquestions",
                        "subquestions", default=[])
    for i, item in enumerate(as_list(raw), start=1):
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict):
            continue
        label = str(first_present(item, "label", "title", "text", "question", default=f"主题{i}") or "").strip()
        tid = str(first_present(item, "theme_id", "topic_id", "subquestion_id", "id", default=f"TH{i}") or f"TH{i}")
        terms = [norm(t) for t in as_list(first_present(item, "terms", "keys", "keywords", default=[])) if norm(t)]
        if not terms and label:
            terms = [norm(label)]
        out.append({"theme_id": tid, "label": label,
                    "question": str(first_present(item, "question", default="") or "").strip(),
                    "terms": terms})
    return out, ("case_declared" if out else "evidence_clustering")


# --------------------------------------------------------------------------------------
# B. 前瞻协议（先落盘，后逐条筛选）
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


def build_protocol_block(case: dict, criteria: dict) -> dict:
    raw = first_present(case, "protocol", "screening_protocol", "review_protocol", "search_protocol", default=None)
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
    fb = criteria.get("fallback_screening") or {}
    fb_incl = [normalize_criterion(c, "FB-IN", i + 1) for i, c in enumerate(as_list(fb.get("inclusion", [])))]
    fb_excl = [normalize_criterion(c, "FB-EX", i + 1) for i, c in enumerate(as_list(fb.get("exclusion", [])))]
    return {"protocol_status": "missing_fallback_declared",
            "protocol_id": "", "prospective": False,
            "inclusion": fb_incl, "exclusion": fb_excl,
            "stop_conditions": [str(s) for s in as_list(fb.get("stop_conditions", []))],
            "search_order": [], "source": "input_gap",
            "input_gap": {"code": "R_PROTOCOL_MISSING",
                          "detail": "case.json 未提供前瞻性筛选协议（纳排标准、停止条件）",
                          "fallback_note": str(fb.get("note", "")),
                          "declaration": "以下筛选按声明回退口径执行并在每条记录标注 fallback=true，不冒充「已按注册协议筛选」"},
            "fallback": {"declared_in": "rules/review_criteria.json#fallback_screening",
                         "note": str(fb.get("note", ""))}}


def criterion_eval(crit: dict, rec: dict) -> dict:
    body_all = norm(rec["title"] + " " + rec["abstract"] + " " + rec["text"] + " " + " ".join(rec["keywords"]))
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
            reason = "记录正文不可读（内联/侧车/原生提取均失败），按口径不参与纳排与综述，诚实降级"
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
        rows.append({"record_id": rec["record_id"], "title": rec["title"], "year": rec["year"],
                     "type": rec["type"], "doi": rec["doi"], "venue": rec["venue"],
                     "source_file": rec["source_file"],
                     "extraction_method": rec["body"]["method"],
                     "body_sha256": rec["body"]["sha256"], "body_chars": rec["body"]["n_chars"],
                     "outcome": outcome,
                     "inclusion_hits": incl_hits, "exclusion_hits": excl_hits,
                     "fallback": is_fallback, "reason": reason, "reason_code": "", "dedupe_of": ""})
        trace.append({"seq": 0, "tool": "protocol_screen",
                      "params": {"record_id": rec["record_id"], "protocol_id": protocol_block["protocol_id"],
                                 "inclusion": [c["criterion_id"] for c in protocol_block["inclusion"]],
                                 "exclusion": [c["criterion_id"] for c in protocol_block["exclusion"]],
                                 "fallback": is_fallback},
                      "result": {"outcome": outcome, "reason": reason}})
    return rows


def counts_of(rows: list) -> dict:
    dup = sum(1 for r in rows if r["outcome"] == "excluded" and r.get("dedupe_of"))
    return {"screened": len(rows),
            "included": sum(1 for r in rows if r["outcome"] == "included"),
            "excluded": sum(1 for r in rows if r["outcome"] == "excluded"),
            "excluded_duplicate": dup,
            "unscored_no_body": sum(1 for r in rows if r["outcome"] == "unscored_no_body")}


def apply_dedupe(rows: list) -> list:
    """DOI / 标题+年份去重：保留首见，重复改判 excluded 并记录 dedupe_of。"""
    seen_doi, seen_title = {}, {}
    by_id = {r["record_id"]: r for r in rows}
    for row in rows:
        if row["outcome"] != "included":
            continue
        rec = by_id[row["record_id"]]
        doi_key = norm(rec["doi"]).replace("https://doi.org/", "")
        title_key = norm_title(rec["title"]) + "|" + str(rec["year"] or "")
        dup = None
        if doi_key and doi_key in seen_doi:
            dup = ("R_DUP_DOI", seen_doi[doi_key])
        elif title_key and title_key in seen_title:
            dup = ("R_DUP_TITLE", seen_title[title_key])
        if dup:
            row["outcome"] = "excluded"
            row["reason_code"] = dup[0]
            row["dedupe_of"] = dup[1]
            row["reason"] = f"{dup[0]}：与保留记录 {dup[1]} 归一后重复，保留首见，不进入主题聚类"
        else:
            if doi_key:
                seen_doi[doi_key] = row["record_id"]
            if title_key:
                seen_title[title_key] = row["record_id"]
    return rows


# --------------------------------------------------------------------------------------
# D. 主题聚类（049 特有：主题、标签、边界全部从记录内容长出来）
# --------------------------------------------------------------------------------------
def extract_terms(records: list, cfg: dict) -> dict:
    """聚类词表：CJK 极大 n 元组（修剪边缘虚词）+ ASCII 词，df 达阈值；只来自纳入记录内容。"""
    ex = cfg.get("term_extraction", {})
    stop = {norm(t) for t in as_list(ex.get("stop_terms") or cfg.get("stop_terms", []))}
    cjk_stops = sorted((s for s in stop if re.search(r"[\u4e00-\u9fff]", s)), key=len, reverse=True)
    edge = set(str(ex.get("edge_particles", "")) or "")
    gram_min = int(ex.get("cjk_gram_min", 2))
    gram_max = int(ex.get("cjk_gram_max", 8))
    min_len = int(ex.get("min_ascii_len", 3))
    min_df = int(ex.get("min_df", 2))
    max_terms = int(ex.get("max_terms", 24))
    max_df_ratio = float(ex.get("max_df_ratio", 1.0))
    n_docs = max(1, len(records))
    df_count: dict = {}
    for rec in records:
        tn = norm(record_text(rec))
        grams = set()
        for run in re.findall(r"[\u4e00-\u9fff]+", tn):
            length = len(run)
            for n in range(gram_min, gram_max + 1):
                for i in range(length - n + 1):
                    g = run[i:i + n]
                    while g and len(g) > gram_min - 1 and (g[0] in edge or g[-1] in edge):
                        g = g[1:] if g[0] in edge else g[:-1]
                    if len(g) >= gram_min and g not in stop and not any(s in g or g in s for s in cjk_stops):
                        grams.add(g)
        for m in _ASCII_WORD.finditer(tn):
            w = m.group(0).strip("-._+")
            if len(w) >= min_len and not all(ch.isdigit() or ch == "." for ch in w) and w not in stop:
                grams.add(w)
        for g in grams:
            df_count[g] = df_count.get(g, 0) + 1
    cands = {g: d for g, d in df_count.items()
             if d >= min_df and len(g) >= gram_min and d < max_df_ratio * n_docs}
    # 极大化剪枝：短词被更长且 df 不更低的词包含 → 丢弃（石墨 ⊂ 石墨负极 不双计）
    kept = {g: d for g, d in cands.items()
            if not any(len(h) > len(g) and g in h and dh >= d for h, dh in cands.items())}
    ranked = sorted(kept, key=lambda g: (-kept[g], -len(g), g))[:max_terms]
    return {g: kept[g] for g in ranked}


def record_term_map(records: list, vocab: dict) -> dict:
    rtm = {}
    for rec in records:
        tn = norm(record_text(rec))
        hits = sorted(g for g in vocab if g in tn)
        if hits:
            rtm[rec["record_id"]] = hits
    return rtm


def cluster_topics(rtm: dict, vocab: dict, cfg: dict) -> tuple:
    """词项候选（词 → 记录集）按 Jaccard 合并成簇；记录数低于最小簇规模的词进单例桶。"""
    cl = cfg.get("clustering", {})
    merge_j = float(cl.get("merge_jaccard", 0.6))
    min_size = int(cl.get("min_cluster_size", 2))
    term_sets: dict = {}
    for rid, terms in rtm.items():
        for t in terms:
            term_sets.setdefault(t, set()).add(rid)
    order = sorted(term_sets, key=lambda t: (-len(term_sets[t]), -len(t), t))
    parent = {t: t for t in order}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            a, b = order[i], order[j]
            inter = len(term_sets[a] & term_sets[b])
            if inter and inter / len(term_sets[a] | term_sets[b]) >= merge_j:
                parent[find(b)] = find(a)
    groups: dict = {}
    for t in order:
        groups.setdefault(find(t), []).append(t)
    clusters, grouped = [], set()
    for terms in groups.values():
        terms = sorted(terms, key=lambda t: (-vocab[t], -len(t), t))
        support = sorted(set().union(*(term_sets[t] for t in terms)))
        if len(support) >= min_size:
            clusters.append({"terms": terms, "support": support})
            grouped.update(support)
    singletons = sorted(set(rtm) - grouped)
    return clusters, singletons, term_sets


def label_for_terms(terms: list, vocab: dict, cfg: dict) -> str:
    """主题标签：簇内词项按（df 降序、词长降序、字典序）候选，丢弃互为子串或高相似
    （SequenceMatcher ≥0.55，标题长短语移位重复）的候选，取前 top_k 个。"""
    top_k = int(cfg.get("naming", {}).get("top_k_terms", 3))
    ranked = sorted(terms, key=lambda t: (-vocab.get(t, 0), -len(t), t))
    kept: list = []
    for t in ranked:
        if any(t in k or k in t for k in kept):
            continue
        if any(SequenceMatcher(None, k, t).ratio() >= 0.55 for k in kept):
            continue
        kept.append(t)
        if len(kept) >= top_k:
            break
    return "×".join(kept)


def _topic_obj(topic_id: str, label: str, question: str, boundary: str, source: str,
               terms: list, support: list, included: list) -> dict:
    sup_ids = {s["record_id"] for s in support}
    return {"topic_id": topic_id, "label": label, "question": question, "boundary": boundary,
            "topic_source": source, "terms": terms,
            "support_count": len(support),
            "support": sorted(support, key=lambda s: s["record_id"]),
            "sources": sorted({rec["source"] for rec in included if rec["record_id"] in sup_ids and rec["source"]}),
            "evidence_ids": [], "support_level": "no_evidence", "conflict_ids": [], "thin": False}


def induce_topics(included: list, clustering_cfg: dict, declared: list) -> tuple:
    """主题生成：案例声明锚点优先（命中仍由记录文本产生），否则证据聚类。"""
    naming = clustering_cfg.get("naming", {})
    if declared:
        topics, uncovered = [], []
        for i, th in enumerate(declared, start=1):
            support = []
            for rec in included:
                tn = norm(record_text(rec))
                hits = sorted({t for t in th["terms"] if t in tn})
                if hits:
                    support.append({"record_id": rec["record_id"], "matched_terms": hits})
            if not support:
                uncovered.append(th)
                continue
            label = th["label"] or label_for_terms(th["terms"], {t: 1 for t in th["terms"]}, clustering_cfg)
            question = th["question"] or str(naming.get("question_template", "{label}")).format(label=label)
            boundary = str(naming.get("boundary_template", "{label}")).format(label=label)
            topics.append(_topic_obj(f"T{i:02d}", label, question, boundary, "case_declared",
                                     sorted(th["terms"]), support, included))
        covered = {s["record_id"] for t in topics for s in t["support"]}
        unassigned = [rec for rec in included if rec["record_id"] not in covered]
        info = {"themes_source": "case_declared",
                "declared_theme_ids": [th["theme_id"] for th in declared],
                "uncovered_theme_ids": [th["theme_id"] for th in uncovered],
                "note": "主题由案例声明锚点生成；支撑关系仍由记录文本命中产生，无命中的锚点如实记为未覆盖"}
        return topics, uncovered, unassigned, info
    vocab = extract_terms(included, clustering_cfg)
    rtm = record_term_map(included, vocab)
    clusters, _singletons, _term_sets = cluster_topics(rtm, vocab, clustering_cfg)
    topics = []
    if not clusters and len(included) >= 2:
        # 小语料回退：词表在 df 阈值下为空（如仅 2 条记录）时，用标题/摘要中的高频实词
        # 形成一个覆盖全部纳入记录的主题，避免整篇综述塌成空结果；标签仍只来自记录内容。
        stop = {norm(x) for x in as_list(clustering_cfg.get("stop_terms", []))}
        counts = {}
        for rec in included:
            text = norm(rec["title"] + " " + rec.get("abstract", ""))
            words = {w for w in _ASCII_WORD.findall(text) if len(w) >= 4 and w not in stop}
            if not words:
                words = {g for g in re.findall(r"[\u4e00-\u9fff]{2,4}", text) if g not in stop}
            for w in words:
                counts[w] = counts.get(w, 0) + 1
        fb_terms = sorted(counts, key=lambda w: (-counts[w], -len(w), w))[:6] or ["study"]
        clusters = [{"terms": fb_terms, "support": [r["record_id"] for r in included]}]
        rtm = {r["record_id"]: fb_terms for r in included}
        vocab = {w: 1 for w in fb_terms}
    ordered = sorted(clusters, key=lambda c: (-len(c["support"]), c["terms"][0]))
    for i, c in enumerate(ordered, start=1):
        label = label_for_terms(c["terms"], vocab, clustering_cfg)
        question = str(naming.get("question_template", "{label}")).format(label=label)
        boundary = str(naming.get("boundary_template", "{label}")).format(label=label)
        support = [{"record_id": rid, "matched_terms": sorted(set(rtm[rid]) & set(c["terms"]))}
                   for rid in c["support"]]
        topics.append(_topic_obj(f"T{i:02d}", label, question, boundary, "evidence_clustered",
                                 c["terms"], support, included))
    covered = {s["record_id"] for t in topics for s in t["support"]}
    unassigned = [rec for rec in included if rec["record_id"] not in covered]
    info = {"themes_source": "evidence_clustering",
            "vocabulary": vocab,
            "vocabulary_size": len(vocab),
            "term_record_map": rtm,
            "clusters": [{"terms": c["terms"], "support": c["support"]} for c in ordered],
            "note": "主题由证据聚类生成：词表只来自纳入记录内容，词项-记录集按 Jaccard 合并，标签取簇内判别词"}
    return topics, [], unassigned, info


# --------------------------------------------------------------------------------------
# E. 证据表（精确来源：record_id + source_file + char_span + 逐字 quote + 校验）
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
    negs = [norm(x) for x in as_list(direction_rules.get("negation_markers",
                                                         ["不", "无", "未", "没", "not ", "no "])) if norm(x)]

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


def _idf(dfv: int, n: int) -> float:
    return math.log(1 + (n - dfv + 0.5) / (dfv + 0.5))


def build_evidence(topics: list, rec_by_id: dict, criteria: dict, direction_rules: dict, trace: list) -> tuple:
    """每个主题逐条支撑记录抽一句最佳证据（主题术语命中数 idf 加权最高者）。"""
    term = criteria.get("sentence_terminators", ["。", "！", "？", ";", "；"])
    split_period = bool(criteria.get("split_ascii_period", True))
    th = criteria.get("thresholds", {})
    min_hits = int(th.get("min_topic_terms_per_quote", 1))
    rows, by_topic = [], {}
    texts = {rid: norm(record_text(r)) for rid, r in rec_by_id.items()}
    n_docs = len(rec_by_id) or 1
    df: dict = {}
    for t in topics:
        for k in t["terms"]:
            if k not in df:
                df[k] = sum(1 for tn in texts.values() if k in tn)
    for t in topics:
        terms = t["terms"]
        total_idf = sum(_idf(df.get(k, 0), n_docs) for k in terms) or 1.0
        picked = []
        for s in t["support"]:
            rec = rec_by_id[s["record_id"]]
            best = None
            for fld in ("text", "abstract"):
                src_text = rec[fld]
                if not src_text:
                    continue
                for sent in split_sentences(src_text, term, split_period):
                    sn, tn = norm(sent), norm(rec["title"])
                    if tn and (sn == tn or sn in tn or tn in sn):
                        continue  # 题名不进引文：与记录标题互含的标题行跳过
                    hit = [k for k in terms if k in sn]
                    if len(hit) < min_hits:
                        continue
                    score = (len(hit), sum(_idf(df.get(k, 0), n_docs) for k in hit))
                    if best is None or score > best[0]:
                        best = (score, sent, hit, fld)
            if best is None:
                continue
            _score, sent, hit, fld = best
            field_text = rec[fld]
            start = field_text.find(sent)
            end = start + len(sent) if start >= 0 else -1
            exact = start >= 0 and field_text[start:end] == sent
            eid = f"E{len(rows) + 1:03d}"
            rows.append({
                "evidence_id": eid,
                "topic_id": t["topic_id"],
                "record_id": rec["record_id"],
                "title": rec["title"],
                "year": rec["year"],
                "venue": rec["venue"],
                "doi": rec["doi"],
                "source_file": rec["source_file"],
                "extraction_method": rec["body"]["method"],
                "quote_field": fld,
                "quote": sent,
                "char_span": {"start": start, "end": end},
                "quote_verified": bool(exact),
                "body_sha256": rec["body"]["sha256"],
                "location": {"field": fld, "record_pointer": f"{rec['record_id']}#{fld}",
                             "char_span": {"start": start, "end": end}},
                "matched_keys": sorted(hit),
                "key_coverage": rounded(sum(_idf(df.get(k, 0), n_docs) for k in hit) / total_idf),
                "direction": detect_direction(sent, direction_rules),
                "sha256_quote": sha256_text(norm(sent)),
            })
            if not exact:
                rows[-1]["quote_mismatch"] = "quote 不是该字段原文的精确子串（span 定位失败），已显式标记，不静默"
            trace.append({"seq": 0, "tool": "evidence_extract",
                          "params": {"record_id": rec["record_id"], "field": fld, "topic_id": t["topic_id"],
                                     "keys": sorted(hit)},
                          "result": {"evidence_id": eid, "char_span": {"start": start, "end": end},
                                     "quote_chars": len(sent), "source_file": rec["source_file"]}})
            trace.append({"seq": 0, "tool": "quote_verify",
                          "params": {"evidence_id": eid, "record_id": rec["record_id"],
                                     "source_file": rec["source_file"], "field": fld},
                          "result": {"exact_substring": bool(exact), "sha256": rec["body"]["sha256"],
                                     "verdict": "verified" if exact else "mismatch_marked"}})
            picked.append(eid)
        by_topic[t["topic_id"]] = picked
    return rows, by_topic


# --------------------------------------------------------------------------------------
# F. 冲突（方向词典成对双列）
# --------------------------------------------------------------------------------------
def _conflict_side(row: dict) -> dict:
    d = row.get("direction") or {}
    return {"evidence_id": row["evidence_id"], "record_id": row["record_id"],
            "source_file": row["source_file"], "quote": row["quote"],
            "quote_verified": row["quote_verified"],
            "direction": d.get("value"), "direction_terms": d.get("terms", []),
            "strength_terms": d.get("strength_terms", [])}


def _numeric_pattern(criteria: dict):
    units = sorted({norm(u) for u in as_list(criteria.get("numeric_units", ["%"])) if norm(u)}, key=len, reverse=True)
    body = "|".join(re.escape(u) for u in units) or "%"
    return re.compile(r"(\d+(?:\.\d+)?)\s*(" + body + r")")


def detect_conflicts(rows: list, direction_rules: dict, criteria: dict) -> tuple:
    """同主题 + 共享主题术语才比对：方向词典成对判定；数值冲突要求同单位不同数值，且若案例/规则
    声明可比口径词表（context_vocabulary），两组口径词（数据集/方法）都必须同时命中才判冲突。"""
    num = _numeric_pattern(criteria)
    vocab = criteria.get("context_vocabulary", {}) or {}
    raws = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if a["topic_id"] != b["topic_id"]:
                continue
            shared = sorted(set(a["matched_keys"]) & set(b["matched_keys"]))
            if not shared:
                continue
            va = (a.get("direction") or {}).get("value")
            vb = (b.get("direction") or {}).get("value")
            polar = {"+1", "-1"}
            if va in polar and vb in polar and va != vb:
                raws.append({"type": "direction_conflict", "topic_id": a["topic_id"],
                             "shared_keys": shared, "a": a, "b": b,
                             "detail": f"同主题证据方向相反：{va} 与 {vb}（共享术语 {shared}）",
                             "resolution": "并列双列呈现（side_a/side_b 各带逐字引文、方向与强度词），"
                                           "不择一、不合并、不以「尚存争议」替代双列"})
                continue
            if {va, vb} in ({"0", "+1"}, {"0", "-1"}):
                raws.append({"type": "zero_vs_direction", "topic_id": a["topic_id"],
                             "shared_keys": shared, "a": a, "b": b,
                             "detail": f"同一主题下一条为零效应（0）、另一条为方向效应（{vb if va == '0' else va}），"
                                       f"用量/工艺口径差异待核",
                             "resolution": "零效应与方向效应并列双列，各自保留原文引文；差异写进空白/限制说明，不择一"})
                continue
            if va == "mixed" or vb == "mixed":
                continue  # 一句话内正负并存已按 mixed 标记，不参与成对判定（另记 uncertainty）
            qa = [(m.group(1), m.group(2)) for m in num.finditer(norm(a["quote"]))]
            qb = [(m.group(1), m.group(2)) for m in num.finditer(norm(b["quote"]))]
            ca, cb = {}, {}
            for group, terms in vocab.items():
                ha = sorted({norm(t) for t in as_list(terms) if norm(t) in norm(a["quote"])})
                hb = sorted({norm(t) for t in as_list(terms) if norm(t) in norm(b["quote"])})
                if ha:
                    ca[group] = ha
                if hb:
                    cb[group] = hb
            same_context = (not vocab) or all(set(ca.get(g) or []) & set(cb.get(g) or []) for g in vocab)
            hit = next(((x, y) for x in qa for y in qb if x[1] == y[1] and x[0] != y[0]), None)
            if hit and same_context:
                raws.append({"type": "numeric_conflict", "topic_id": a["topic_id"],
                             "shared_keys": shared, "a": a, "b": b,
                             "detail": f"同主题同单位数值不一致：{hit[0][0]}{hit[0][1]} vs {hit[1][0]}{hit[1][1]}"
                                       f"（共享术语 {shared}）",
                             "resolution": "并列双列呈现（side_a/side_b 各带逐字引文），不择一写入结论；"
                                           "数值口径差异写进研究空白或限制说明"})
    conflicts, by_pair = [], {}
    for c in raws:
        key = (tuple(sorted([(c["a"]["record_id"], c["a"]["sha256_quote"]),
                             (c["b"]["record_id"], c["b"]["sha256_quote"])])), c["type"])
        if key in by_pair:
            first = by_pair[key]
            if c["topic_id"] not in first["also_in_topics"]:
                first["also_in_topics"].append(c["topic_id"])
            continue
        entry = {"conflict_id": f"C{len(conflicts) + 1:02d}", "type": c["type"],
                 "topic_id": c["topic_id"], "also_in_topics": [],
                 "shared_keys": c["shared_keys"],
                 "side_a": _conflict_side(c["a"]), "side_b": _conflict_side(c["b"]),
                 "detail": c["detail"], "resolution": c["resolution"]}
        by_pair[key] = entry
        conflicts.append(entry)
    mixed_rows = [r["evidence_id"] for r in rows if (r.get("direction") or {}).get("value") == "mixed"]
    return conflicts, mixed_rows


# --------------------------------------------------------------------------------------
# G. 覆盖声明与研究空白
# --------------------------------------------------------------------------------------
def build_gaps(topics: list, by_topic: dict, unassigned: list, uncovered_themes: list,
               clustering_cfg: dict) -> list:
    min_full = int(clustering_cfg.get("thin_topic", {}).get("min_evidence_for_full_section", 2))
    gaps = []
    for t in topics:
        ev = by_topic.get(t["topic_id"], [])
        if not ev:
            gaps.append({"topic_id": t["topic_id"], "code": "R_TOPIC_NO_EVIDENCE", "basis": "zero_evidence",
                         "gap": f"主题「{t['label']}」有 {t['support_count']} 篇支撑记录但抽不出可引用的证据句",
                         "evidence_ids": [],
                         "missing_evidence": "需要能提供该主题论述的完整摘要或全文段落"})
        elif len(ev) < min_full:
            gaps.append({"topic_id": t["topic_id"], "code": "R_TOPIC_THIN_EVIDENCE", "basis": "weak_evidence_only",
                         "gap": f"主题「{t['label']}」只有 {len(ev)} 条可引用证据，证据薄弱",
                         "evidence_ids": list(ev),
                         "missing_evidence": "需要该主题下能报告对照数据或独立结果的更多研究"})
    for th in uncovered_themes:
        gaps.append({"topic_id": "", "theme_id": th["theme_id"], "code": "R_SUBQUESTION_NOT_COVERED",
                     "basis": "not_reachable_in_corpus",
                     "gap": f"声明主题「{th['label']}」在本轮语料内没有任何记录命中其术语 {th['terms']}",
                     "evidence_ids": [],
                     "missing_evidence": f"需要扩展检索范围，补充与「{th['label']}」相关的文献；缺口只声明到语料边界，不写成领域空白"})
    if unassigned:
        gaps.append({"topic_id": "", "code": "R_TOPIC_UNASSIGNED", "basis": "not_clustered",
                     "gap": f"{len(unassigned)} 篇纳入记录未进入任何主题聚类",
                     "evidence_ids": [],
                     "missing_evidence": "需要扩充聚类词表或人工归类，不得静默丢弃"})
    return gaps


def build_coverage(topics: list, by_topic: dict, unassigned: list, uncovered_themes: list,
                   records_total: int, sources_total: int, included_total: int) -> dict:
    covered = [{"topic_id": t["topic_id"], "label": t["label"], "topic_source": t["topic_source"],
                "record_count": t["support_count"], "evidence_count": len(by_topic.get(t["topic_id"], []))}
               for t in topics if by_topic.get(t["topic_id"])]
    zero_ev = [{"topic_id": t["topic_id"], "label": t["label"], "record_count": t["support_count"],
                "evidence_count": 0} for t in topics if not by_topic.get(t["topic_id"])]
    not_covered = [{"theme_id": th["theme_id"], "label": th["label"], "terms": th["terms"],
                    "code": "R_SUBQUESTION_NOT_COVERED"} for th in uncovered_themes]
    cov = {
        "themes_declared": bool(uncovered_themes) or any(t["topic_source"] == "case_declared" for t in topics),
        "covered_topics": covered,
        "zero_evidence_topics": zero_ev,
        "not_covered_themes": not_covered,
        "unassigned_records": [{"record_id": r["record_id"], "title": r["title"]} for r in unassigned],
        "corpus_boundary": {"records_total": records_total, "sources_total": sources_total,
                            "included_total": included_total},
    }
    covered_txt = "、".join(f"{c['label']}（{c['evidence_count']} 条证据）" for c in covered) or "无"
    not_covered_txt = "、".join(nc["label"] for nc in not_covered) or "无"
    unassigned_txt = "、".join(u["record_id"] for u in cov["unassigned_records"]) or "无"
    cov["statement_text"] = (
        f"覆盖声明：本轮综述基于池内快照（{records_total} 条记录 / {sources_total} 份来源文件，"
        f"筛选后纳入 {included_total} 条），覆盖主题：{covered_txt}。"
        f"未覆盖的声明主题：{not_covered_txt}；未进入任何主题聚类的记录：{unassigned_txt}。"
        "语料边界：本综述只代表池内快照的证据分布，不代表领域全局文献；"
        "未覆盖项只声明为语料边界内的证据缺口，不外推为领域结论。")
    return cov


# --------------------------------------------------------------------------------------
# H. 大纲与综述草稿
# --------------------------------------------------------------------------------------
def build_outline(topics: list, by_topic: dict, unassigned: list, counts: dict,
                  protocol_block: dict, clustering_info: dict, cfg: dict) -> list:
    method_note = ("已按注册前瞻协议逐条筛选" if protocol_block["protocol_status"] == "registered_prospective"
                   else "协议缺失，按声明回退口径筛选（每条记录 fallback=true）")
    outline = [{
        "section_id": "S0", "title": "纳入范围与筛选口径", "kind": "method",
        "statement": (f"筛选 {counts['screened']} 条：纳入 {counts['included']}，排除 {counts['excluded']}"
                      f"（其中去重 {counts['excluded_duplicate']} 条），正文不可读降级 {counts['unscored_no_body']} 条；"
                      f"{method_note}。主题生成方式：{clustering_info.get('themes_source')}。"),
        "evidence_ids": [],
    }]
    min_full = int(cfg.get("thin_topic", {}).get("min_evidence_for_full_section", 2))
    for t in topics:
        ev = by_topic.get(t["topic_id"], [])
        thin = bool(ev) and len(ev) < min_full
        outline.append({
            "section_id": f"S{len(outline)}", "title": t["label"] + ("（证据薄弱）" if thin else ""),
            "kind": "thin_topic" if thin else "topic", "topic_id": t["topic_id"],
            "question": t["question"], "boundary": t["boundary"],
            "statement": f"该主题支撑记录 {t['support_count']} 篇，可引用证据 {len(ev)} 条。",
            "evidence_ids": ev,
        })
    outline.append({"section_id": f"S{len(outline)}", "title": "分歧与冲突证据", "kind": "conflict",
                    "statement": "同一主题内方向相反或数值不一致的证据在此双列并列，各自带逐字引文，不合并。",
                    "evidence_ids": []})
    outline.append({"section_id": f"S{len(outline)}", "title": "综述覆盖范围声明", "kind": "coverage",
                    "statement": "覆盖/未覆盖主题、未聚类记录与语料边界（池内快照非全局文献）在本节声明。",
                    "evidence_ids": []})
    return outline


def build_draft(outline: list, topics_by_id: dict, rows_by_id: dict, conflicts: list, contested_ids: set,
                coverage: dict, unassigned: list, cfg: dict) -> dict:
    sections = []
    statements = []
    used_quotes: set = set()
    for sec in outline:
        paragraphs = []
        extra = {"topic_id": sec.get("topic_id", ""), "question": sec.get("question", ""),
                 "boundary": sec.get("boundary", "")}
        if sec["kind"] == "method":
            text = sec["statement"]
            paragraphs.append({"kind": "statement_paragraph", "sentences": [], "paragraph_text": text})
            statements.append({"section_id": sec["section_id"], "text": text})
        elif sec["kind"] in ("topic", "thin_topic"):
            t = topics_by_id[sec["topic_id"]]
            ev_all = [rows_by_id[e] for e in sec["evidence_ids"]]
            ev, cross_refs = [], []
            for row in ev_all:
                # 薄弱小节只有一两条证据，重复也必须保留，否则会把仅有的支撑句删掉。
                if sec["kind"] != "thin_topic" and row["sha256_quote"] in used_quotes:
                    cross_refs.append(row["evidence_id"])
                else:
                    if sec["kind"] != "thin_topic":
                        used_quotes.add(row["sha256_quote"])
                    ev.append(row)
            if ev:
                sentences = [{"text": e["quote"], "evidence_ids": [e["evidence_id"]],
                              "strength": "contested" if e["evidence_id"] in contested_ids else t["support_level"]}
                             for e in ev]
                paragraphs.append({"kind": "evidence_paragraph", "sentences": sentences,
                                   "paragraph_text": " ".join(f"{s['text']} [{s['evidence_ids'][0]}]"
                                                              for s in sentences)})
            else:
                if cross_refs:
                    text = ("本节证据均已在其他小节逐字引用，不重复列出；交叉引用："
                            + "、".join(cross_refs) + "。")
                else:
                    text = f"该主题的支撑记录抽不出可引用证据句（R_TOPIC_NO_EVIDENCE），按缺口处理，不补写正文。"
                paragraphs.append({"kind": "statement_paragraph", "sentences": [], "paragraph_text": text})
                statements.append({"section_id": sec["section_id"], "text": text})
            if cross_refs and ev:
                text = ("交叉引用：" + "、".join(cross_refs)
                        + " 已在其他小节逐字引用，此处不重复，以免同一段原文被当作多条独立证据。")
                paragraphs.append({"kind": "statement_paragraph", "sentences": [], "paragraph_text": text})
                statements.append({"section_id": sec["section_id"], "text": text})
            if sec["kind"] == "thin_topic":
                text = (f"证据薄弱声明：该主题在本轮语料内只有 {len(ev)} 条可引用证据"
                        f"（{('、'.join(sec['evidence_ids'])) or '无'}），低于完整小节阈值；"
                        "此处只呈现现有证据并声明缺口，不扩展成确定性结论，不硬凑通用框架章节。")
                paragraphs.append({"kind": "statement_paragraph", "sentences": [], "paragraph_text": text})
                statements.append({"section_id": sec["section_id"], "text": text})
        elif sec["kind"] == "conflict":
            for c in conflicts:
                a, b = rows_by_id[c["side_a"]["evidence_id"]], rows_by_id[c["side_b"]["evidence_id"]]
                da, db = c["side_a"], c["side_b"]
                also = (f"（同证据对也出现在主题 {'、'.join(c['also_in_topics'])}）"
                        if c["also_in_topics"] else "")
                text = (f"一方面，{da['quote']} [{da['evidence_id']}]（方向 {da['direction']}，"
                        f"强度词 {'/'.join(da['strength_terms']) or '无'}）；"
                        f"另一方面，{db['quote']} [{db['evidence_id']}]（方向 {db['direction']}，"
                        f"强度词 {'/'.join(db['strength_terms']) or '无'}）。{c['detail']}。{also}")
                paragraphs.append({"kind": "conflict_paragraph",
                                   "sentences": [{"text": a["quote"], "evidence_ids": [a["evidence_id"]],
                                                  "strength": "contested"},
                                                 {"text": b["quote"], "evidence_ids": [b["evidence_id"]],
                                                  "strength": "contested"}],
                                   "paragraph_text": text,
                                   "note": c["resolution"]})
            if not conflicts:
                text = "本轮纳入记录在同一主题内未检出方向或数值不一致的证据。"
                paragraphs.append({"kind": "statement_paragraph", "sentences": [], "paragraph_text": text})
                statements.append({"section_id": sec["section_id"], "text": text})
        elif sec["kind"] == "coverage":
            text = coverage["statement_text"]
            paragraphs.append({"kind": "statement_paragraph", "sentences": [], "paragraph_text": text})
            statements.append({"section_id": sec["section_id"], "text": text})
            if unassigned:
                text = ("未聚类记录单列：" + "；".join(f"{u['title']}（{u['record_id']}）" for u in unassigned)
                        + "。这些记录未进入任何主题聚类，单列以免被静默丢弃。")
                paragraphs.append({"kind": "statement_paragraph", "sentences": [], "paragraph_text": text})
                statements.append({"section_id": sec["section_id"], "text": text})
        sections.append({**extra, "section_id": sec["section_id"], "title": sec["title"], "kind": sec["kind"],
                         "paragraphs": paragraphs,
                         "section_text": " ".join(p["paragraph_text"] for p in paragraphs)})
    body = "\n\n".join(f"## {s['title']}\n{s['section_text']}" for s in sections)
    audit = {
        "sentences_total": sum(len(p["sentences"]) for s in sections for p in s["paragraphs"]),
        "sentences_without_evidence": [
            {"section_id": s["section_id"], "text": st["text"]}
            for s in sections for p in s["paragraphs"] for st in p["sentences"] if not st["evidence_ids"]
        ],
        "data_statements": statements,
        "rule": "实质句必须挂 [E###]；数据/状态陈述句（筛选计数、聚类口径、薄证据声明、覆盖声明）不挂证据"
                "但需可复算，逐条列于 data_statements",
    }
    return {"sections": sections, "body": body, "audit": audit}


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    case = normalize_case(case)
    rules = load_rules()
    criteria, clustering_cfg, direction_rules = rules["criteria"], rules["clustering"], rules["direction"]
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res._contract_orig.keys()))

    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).parent
    bases = path_bases(work, case_dir)
    sources = collect_sources(case, bases)
    records = collect_records(case, bases, sources)
    question = parse_question(case, task_text)
    declared, declared_source = collect_themes(case)
    res.log("intake", sources=len(sources), records=len(records),
            themes=declared_source, question_chars=len(question["text"]))

    if not question["text"].strip() and not records:
        res.set("screening", {"counts": {"screened": 0, "included": 0, "excluded": 0,
                                         "excluded_duplicate": 0, "unscored_no_body": 0}, "records": []})
        res.set("topics", [])
        res.set("evidence_table", [])
        res.set("draft", {"sections": [], "body": ""})
        res.status["overall"] = "empty"
        res.unmet("研究问题与文献集", "case.json 与 task.md 均未给出研究问题或文献记录，无法生成综述")
        res.done("盘点")
        return res

    # ---- B. 前瞻协议：先落盘计划文件，再逐条筛选 ----
    protocol_block = build_protocol_block(case, criteria)
    constraints = case.get("constraints") if isinstance(case.get("constraints"), dict) else {}
    plan_doc = {"case_id": res.case_id, "skill": SKILL,
                "question": question["text"], "question_source": question["source"],
                "themes": declared, "themes_source": declared_source,
                "topic_rule": ("案例声明主题锚点优先，支撑关系仍由记录文本命中产生"
                               if declared_source == "case_declared"
                               else "主题由证据聚类生成（rules/topic_clustering.json），不套通用框架"),
                "protocol": protocol_block, "constraints": constraints,
                "screening_order": [r["record_id"] for r in records],
                "prospective_note": "本文件先于筛选、主题聚类与写作落盘（prospective）；执行明细见 result.json 的 evidence.*"}
    artifacts = []
    for _label, base in (("work", work), ("out", Path(args.out))):
        try:
            write_json(base / "plan.json", plan_doc)
            artifacts = ["plan.json"]
            break
        except OSError:
            continue  # work 不可写时静默回退到 out；位置差异不写入结果，保证跨环境字节一致
    if not artifacts:
        res.note("plan.json 落盘失败，协议仍完整保存在 result.json 的 screening/evidence 中")
    res.log("plan_written", artifacts=artifacts)

    # ---- C. 筛选 + 去重 ----
    trace = []
    for rec in records:
        trace.append({"seq": 0, "tool": "source_extract",
                      "params": {"record_id": rec["record_id"], "source_ref": rec["source_ref"],
                                 "priority": ["inline_text", "inline_abstract", "sidecar_txt", "sidecar_ocr_txt",
                                              "pdf_extract", "docx_xml", "file_read", "missing"]},
                      "result": {"method": rec["body"]["method"], "n_chars": rec["body"]["n_chars"],
                                 "sha256": rec["body"]["sha256"], "error": rec["body"]["error"]}})
    rows = screen_records(records, protocol_block, trace)
    rows = apply_dedupe(rows)
    counts = counts_of(rows)
    included_ids = {r["record_id"] for r in rows if r["outcome"] == "included"}
    included = [r for r in records if r["record_id"] in included_ids]
    res.log("screen", protocol=protocol_block["protocol_status"], **counts)

    # ---- D. 主题聚类 ----
    topics, uncovered_themes, unassigned, clustering_info = induce_topics(included, clustering_cfg, declared)
    topics_by_id = {t["topic_id"]: t for t in topics}
    res.log("topics", mode=clustering_info.get("themes_source"), n=len(topics),
            uncovered=len(uncovered_themes), unassigned=len(unassigned),
            vocabulary=clustering_info.get("vocabulary_size", 0))

    # ---- E. 证据表 ----
    rec_by_id = {r["record_id"]: r for r in included}
    ev_rows, by_topic = build_evidence(topics, rec_by_id, criteria, direction_rules, trace)
    seq = 0
    for call in trace:
        seq += 1
        call["seq"] = seq
    res.log("evidence", rows=len(ev_rows), topics_with_evidence=sum(1 for v in by_topic.values() if v))

    # ---- F. 冲突 ----
    conflicts, mixed_ids = detect_conflicts(ev_rows, direction_rules, criteria)
    contested_ids = sorted({eid for c in conflicts
                            for eid in (c["side_a"]["evidence_id"], c["side_b"]["evidence_id"])})
    established_min = int(criteria.get("thresholds", {}).get("established_min_evidence", 2))
    min_full = int(clustering_cfg.get("thin_topic", {}).get("min_evidence_for_full_section", 2))
    for t in topics:
        ev = by_topic.get(t["topic_id"], [])
        t["conflict_ids"] = [c["conflict_id"] for c in conflicts
                             if c["topic_id"] == t["topic_id"] or t["topic_id"] in c["also_in_topics"]]
        t["evidence_ids"] = ev
        if t["conflict_ids"]:
            t["support_level"] = "contested"
        elif len(ev) >= established_min:
            t["support_level"] = "established"
        elif ev:
            t["support_level"] = "suggestive"
        else:
            t["support_level"] = "no_evidence"
        t["thin"] = bool(ev) and len(ev) < min_full

    # ---- G. 覆盖声明 + 空白 ----
    coverage = build_coverage(topics, by_topic, unassigned, uncovered_themes,
                              records_total=len(records), sources_total=len(sources),
                              included_total=len(included))
    gaps = build_gaps(topics, by_topic, unassigned, uncovered_themes, clustering_cfg)
    res.log("coverage", covered=len(coverage["covered_topics"]),
            not_covered=len(coverage["not_covered_themes"]), unassigned=len(unassigned))

    # ---- H. 大纲 + 综述草稿 ----
    outline = build_outline(topics, by_topic, unassigned, counts, protocol_block, clustering_info, clustering_cfg)
    rows_by_id = {r["evidence_id"]: r for r in ev_rows}
    draft = build_draft(outline, topics_by_id, rows_by_id, conflicts, set(contested_ids),
                        coverage, unassigned, clustering_cfg)

    # 可编辑草稿落盘（office_min 生成 .docx）
    docx_path = Path(args.out) / "review_draft.docx"
    try:
        blocks = [{"type": "p", "text": f"研究问题：{question['text'] or '（未提供）'}"},
                  {"type": "p", "text": (f"筛选 {counts['screened']} 条：纳入 {counts['included']} / 排除 "
                                         f"{counts['excluded']}（协议状态 {protocol_block['protocol_status']}）；"
                                         "逐条明细见 result.json 的 screening")}]
        for sec in draft["sections"]:
            blocks.append({"type": "h2", "text": sec["title"]})
            for p in sec["paragraphs"]:
                blocks.append({"type": "p", "text": p["paragraph_text"]})
        if ev_rows:
            blocks.append({"type": "h2", "text": "证据表（摘录）"})
            blocks.append({"type": "table", "rows": [["evidence_id", "record_id", "year", "quote"]]
                           + [[r["evidence_id"], r["record_id"], str(r["year"] or ""), r["quote"][:120]]
                              for r in ev_rows[:20]]})
        write_docx(docx_path, blocks, title="文献综述草稿（示例运行产物）")
        back = read_docx_text(docx_path)
        artifacts = artifacts + [{"file": "review_draft.docx", "paragraphs": len(back),
                                  "self_check": "read_docx_text 回读段落数 > 0" if back else "回读为空"}]
    except Exception as exc:  # noqa: BLE001
        res.unsure("草稿产物", f"review_draft.docx 生成失败：{type(exc).__name__}: {exc}")
        artifacts = artifacts + [{"file": "review_draft.docx", "error": f"{type(exc).__name__}: {exc}"}]

    # ---- 状态与不确定性 ----
    unscored = [r for r in rows if r["outcome"] == "unscored_no_body"]
    mismatched = [r for r in ev_rows if not r["quote_verified"]]
    if protocol_block["input_gap"]:
        res.unmet("筛选协议", f"{protocol_block['input_gap']['code']} — {protocol_block['input_gap']['detail']}；"
                              "已按声明回退口径筛选（fallback_screening），不冒充已按协议筛选")
    if unscored:
        res.unmet("记录正文", f"{len(unscored)} 条记录正文不可读（{','.join(r['record_id'] for r in unscored)}），"
                              "已按 unscored_no_body 诚实降级，不参与纳排与综述，未编造其内容")
    if mismatched:
        res.unmet("证据引文校验", f"{len(mismatched)} 条证据 quote 未通过精确子串校验，已在行内标 quote_mismatch，不静默")
    if not included:
        res.status["overall"] = "empty"
        res.unmet("纳入文献", "全部记录被协议/去重排除，未生成主题与综述（理由见 screening）")
    elif not any(by_topic.values()):
        res.status["overall"] = "empty"
        res.unmet("综述主题", "纳入记录无法聚类出有证据的主题，只保留协议、筛选与聚类词表")
    else:
        res.status["overall"] = "complete"
    for g in gaps:
        res.unmet(f"主题缺口:{g.get('topic_id') or g.get('theme_id') or 'unassigned'}",
                  f"{g['code']} — {g['gap']}")
    for c in conflicts:
        res.note(f"{c['topic_id']} 存在{c['type']}，已双列呈现（{c['side_a']['evidence_id']} × "
                 f"{c['side_b']['evidence_id']}，conflict_id={c['conflict_id']}）")
        res.unsure(f"冲突:{c['conflict_id']}", f"{c['detail']}；双方口径差异未仲裁，以双列并列交付")
    for eid in mixed_ids:
        res.unsure(f"方向:mixed:{eid}", "同一句内同时含正负方向表述，已标 mixed，不参与成对冲突判定")

    # ---- 结果装配 ----
    payload = {
        "screening": {"protocol_status": protocol_block["protocol_status"],
                      "protocol_id": protocol_block["protocol_id"],
                      "order_note": "先落盘协议与计划（plan.json），后对每条记录逐条筛选并记 criterion_hits，"
                                    "再执行 DOI/标题+年份去重",
                      "dedupe_rule": criteria.get("dedupe_rule", ""),
                      "counts": counts, "records": rows,
                      "input_gap": protocol_block["input_gap"]},
        "topics": topics,
        "outline": outline,
        "evidence_table": ev_rows,
        "draft": draft,
        "conflicts": {"count": len(conflicts), "items": conflicts,
                      "resolution": direction_rules.get("pair_rules", {}).get("resolution",
                                                                             criteria.get("conflict_rule", "")),
                      "note": "count=0 表示本轮同主题证据无方向/数值冲突，非未检查"},
        "research_gaps": {"count": len(gaps), "items": gaps,
                          "rule": clustering_cfg.get("thin_topic", {}).get("note", ""),
                          "note": "count=0 表示全部主题均有达阈值证据，非未检查"},
        "coverage": coverage,
    }
    for key, value in payload.items():
        res.set(key, value)

    # ---- 证据记录（三段式） ----
    resolved_all = {r["evidence_id"] for r in ev_rows}
    back_ids = sorted({eid for v in by_topic.values() for eid in v})
    unresolved = [eid for eid in back_ids if eid not in resolved_all]
    res.evidence["sources_manifest"] = {"sources": sources, "count": len(sources),
                                        "note": "来源文件清单与解析结果；记录通过 source_path/source_id 指到来源文件"}
    res.evidence["protocol"] = protocol_block
    res.evidence["screening"] = {"counts": counts, "rule": criteria.get("screening_rule", ""),
                                 "dedupe_rule": criteria.get("dedupe_rule", ""),
                                 "fallback": protocol_block["fallback"],
                                 "input_gap": protocol_block["input_gap"]}
    res.evidence["clustering"] = clustering_info
    res.evidence["evidence_table_map"] = {
        "rows": len(ev_rows),
        "per_topic": {k: v for k, v in sorted(by_topic.items())},
        "strength_rule": criteria.get("thresholds", {}).get("strength_rule", ""),
        "quote_rule": "证据句必须是记录正文/摘要的逐字整句；source_file + char_span（按 quote_field 字段内字符区间）"
                      "+ 精确子串校验；二进制来源（PDF/DOCX）以 body_sha256 锚定提取文本；题名不进引文",
    }
    res.evidence["backlink"] = {
        "rule": "回链覆盖率 = 挂了 evidence_id 的主题 / 有支撑的主题；引用 id 必须能在 evidence_table 解析且 quote 校验通过",
        "topics_total": len(topics),
        "topics_with_evidence": len([v for v in by_topic.values() if v]),
        "referenced_ids": back_ids,
        "unresolved_ids": unresolved,
        "draft_audit": draft["audit"],
        "quote_verified_rows": len([r for r in ev_rows if r["quote_verified"]]),
        "quote_rows_total": len(ev_rows),
    }
    res.evidence["conflicts"] = {"rule": direction_rules.get("pair_rules", {}).get("resolution",
                                                                                  criteria.get("conflict_rule", "")),
                                 "items": conflicts}
    res.evidence["coverage"] = coverage
    res.evidence["execution"] = {"tool_calls": trace,
                                 "recompute": "筛选（criterion_eval）与证据（句子 idf 打分）口径全部参数化于 rules/，"
                                              "每条 tool_call 的 params 可原样重放（source_extract/protocol_screen/"
                                              "evidence_extract/quote_verify）"}
    res.evidence["artifacts"] = artifacts
    res.evidence["plan_file"] = {"file": "plan.json", "prospective": True,
                                 "note": "协议与计划先于筛选、聚类与写作落盘（优先 work 目录，不可写时回退 out 目录）"}
    res.evidence["scope"] = {"sources": ["local-corpus"], "no_network": True,
                             "note": "引用只来自本 case 提供的文献集（池内快照）；未联网、未引入外部文献" +
                                     (f"；{case['synthetic_notice']}" if case.get("synthetic_notice") else "")}

    res.bind(payload, aliases={"综述": "draft", "草稿": "draft", "主结果": "draft", "主题": "topics",
                               "证据表": "evidence_table", "筛选": "screening", "覆盖": "coverage",
                               "证据记录": "evidence", "状态说明": "status"})
    bind_contract(res, payload, rules["projection"])
    res.done("双结构摄入", "前瞻协议落盘", "逐条筛选与去重", "证据聚类主题", "证据表构建",
             "冲突双列分析", "覆盖声明与综述草稿")
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
        "screening": {"counts": {"screened": 0, "included": 0, "excluded": 0,
                                 "excluded_duplicate": 0, "unscored_no_body": 0}, "records": []},
        "topics": [],
        "evidence_table": [],
        "draft": {"sections": [], "body": "", "error": str(exc)},
        "coverage": {"statement_text": "输入不可读，未生成综述"},
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


def _synthesis_hooks(args, result_path, payload):
    """可选 LLM 综合：生成 synthesis_input.json；若 work/synthesis.json 存在，校验并写 review.md。

    确定性管线不调用模型；模型由 Harness 提供。该钩子只负责把证据压缩成输入、校验模型输出，
    校验不过时保留确定性 report.md，不静默放行未挂证据的段落。
    """
    import subprocess
    work = Path(getattr(args, "work", ".") or ".")
    out = Path(getattr(args, "out", ".") or ".")
    here = Path(__file__).parent
    info = {}
    try:
        work.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([sys.executable, str(here / "make_synthesis_input.py"),
                               "--result", str(result_path),
                               "--out", str(work / "synthesis_input.json"),
                               "--case", str(getattr(args, "case", ""))],
                              capture_output=True, text=True, timeout=60)
        info["synthesis_input"] = str(work / "synthesis_input.json") if proc.returncode == 0 else "failed"
    except Exception as exc:  # noqa: BLE001
        info["synthesis_input"] = f"{type(exc).__name__}: {exc}"
    syn = work / "synthesis.json"
    if syn.is_file():
        try:
            proc = subprocess.run([sys.executable, str(here / "merge_synthesis.py"),
                                   "--result", str(result_path), "--synthesis", str(syn),
                                   "--out", str(out / "review.md"),
                                   "--audit-out", str(out / "synthesis_audit.json")],
                                  capture_output=True, text=True, timeout=60)
            info["review"] = str(out / "review.md") if proc.returncode == 0 else "rejected"
            if proc.returncode != 0:
                info["review_detail"] = (proc.stdout or proc.stderr or "")[:500]
        except Exception as exc:  # noqa: BLE001
            info["review_detail"] = f"{type(exc).__name__}: {exc}"
    return info


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
                          "topics": len(payload.get("topics", []))}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    try:
        from render_report import render_file
        render_file(path, Path(args.out) / "report.md")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"report_warning": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "included": payload.get("screening", {}).get("counts", {}).get("included"),
                      "topics": len(payload.get("topics", [])),
                      "evidence": len(payload.get("evidence_table", [])),
                      "conflicts": payload.get("conflicts", {}).get("count", 0),
                      "gaps": payload.get("research_gaps", {}).get("count", 0)}, ensure_ascii=False))
    try:
        _extra = _synthesis_hooks(args, path, payload)
        if _extra:
            print(json.dumps({"synthesis": _extra}, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
