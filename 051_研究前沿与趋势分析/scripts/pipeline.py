#!/usr/bin/env python3
"""051 研究前沿与趋势分析 —— 主流程（纯标准库，离线、确定性、全流程留痕）。

输入：case.json（主题 + 统计窗口 + sources/records 双结构 + 前瞻协议 + 约束 + 子问题）+ task.md
输出：out/result.json（时间趋势 / 前沿与趋势判定 / 主题演化 / 代表作 / 研究空白 / 证据矩阵 / 不确定性 / 状态）

可复算原则：
  1. 所有阈值与公式都在 rules/statistics.json、rules/representative.json、rules/gap_rules.json、
     rules/screening_rules.json；评审按规则文件可独立重算每个统计量与判定；
  2. 每条趋势/前沿主张 = 主题 + 年份证据桶（逐桶计数 + record_ids）+ 代表记录（逐字引文 +
     char_span 校验）+ 判定依据；词频热度只作对照审计，任何主张不得以 raw_hits 作为趋势依据；
  3. 年份口径分序列统计（journal / online / preprint），缺失与回填逐条记账，不静默丢样本；
  4. 前沿 = 池内相对增量（近桶新起 + 基数对比），薄证据主题只报计数，空白只来自检索缺口，
     全部结论限定「池内快照」，不写成领域全局结论。

流程：读题 → sources/records 双结构摄入（正文按 内联 → 侧车 → 自研 PDF 提取 → DOCX →
      文本直读 → 诚实 missing）→ 去重 → 前瞻协议逐条筛选 → 年份口径 → 时间序列与拐点
      → 主题归并与演化事件 → 趋势证据桶与前沿判定 → 逐字证据表与冲突双列 → 词频热度审计
      → 代表作多口径遴选 → 空白判据（零证据子问题）→ 证据矩阵 → 契约绑定与落盘
"""

from __future__ import annotations

import csv
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
    snippet,
    stable_id,
    tokens,
)
import pdf_min  # noqa: E402
from normalize import normalize_case  # noqa: E402

SKILL = "research-frontier-trend-analysis"
RULES = Path(__file__).resolve().parent.parent / "rules"
_SENT_TERM = ["。", "！", "？", ";", "；"]


# --------------------------------------------------------------------------------------
# 规则装载
# --------------------------------------------------------------------------------------
def load_rules() -> dict:
    return {
        "stats": load_json(RULES / "statistics.json", default={}) or {},
        "taxonomy": load_json(RULES / "topic_taxonomy.json", default={}) or {},
        "rep": load_json(RULES / "representative.json", default={}) or {},
        "gap": load_json(RULES / "gap_rules.json", default={}) or {},
        "codes": (load_json(RULES / "reason_codes.json", default={}) or {}).get("codes", {}),
        "routing": load_json(RULES / "contract_routing.json", default={}) or {},
        "screening": load_json(RULES / "screening_rules.json", default={}) or {},
        "direction": load_json(RULES / "direction_lexicon.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 0. sources/records 双结构：路径解析与正文获取（复用 048 真实画像口径）
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


def _records_from_file(path: Path) -> list:
    if not path.exists():
        return []
    suffix = path.suffix.lower()
    if suffix == ".csv":
        rows = []
        with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append({k: (v or "").strip() for k, v in row.items() if k})
        return rows
    if suffix == ".jsonl":
        rows = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows
    if suffix != ".json":
        # PDF/DOCX/TXT/MD 是正文材料，不是题录文件；交给 resolve_body 读取，不能当 JSON 解析。
        return []
    try:
        data = load_json(path, default=None)
    except Exception:  # noqa: BLE001
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("records", "items", "candidates", "papers", "corpus"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    return []


def collect_records(case: dict, bases: list) -> list:
    raw = first_present(case, "records", "candidates", "corpus", "papers", "items", "literature", default=[])
    items = [x for x in as_list(raw) if isinstance(x, dict)]
    for key in ("corpus_path", "records_path", "candidates_path", "bibliography_path"):
        val = first_present(case, key)
        if isinstance(val, str) and val.strip():
            resolved, _cands = resolve_ref(val, bases)
            items.extend(_records_from_file(resolved))
    for mat in as_list(first_present(case, "materials", "files", "attachments", default=[])):
        ref = ""
        if isinstance(mat, dict):
            ref = str(first_present(mat, "path", "file", "filepath", "location", default="") or "")
        elif isinstance(mat, str):
            ref = mat
        if ref.strip():
            resolved, _cands = resolve_ref(ref, bases)
            items.extend(_records_from_file(resolved))

    out = []
    for it in items:
        title = str(first_present(it, "title", "name", "record_title", "题名", default="")).strip()
        abstract = str(first_present(it, "abstract", "summary", "text", "content", "摘要", default="")).strip()
        if not title and not abstract:
            continue
        authors = [str(a) for a in as_list(first_present(it, "authors", "author", "作者", default=[]))]
        kws = [str(k) for k in as_list(first_present(it, "keywords", "concepts", "keywords_plus", "关键词", default=[]))]
        cites = first_present(it, "citations", "cited_by", "cited_by_count", "times_cited", "被引", default=None)
        try:
            cites = int(float(cites)) if cites not in (None, "") else None
        except (TypeError, ValueError):
            cites = None
        rid = str(first_present(it, "id", "record_id", "key", "doi", default="")).strip()
        if not rid:
            rid = "R-" + stable_id(title, abstract[:120], n=8)
        bodyinfo = resolve_body(it, bases)
        out.append(
            {
                "record_id": rid,
                "title": title,
                "abstract": abstract,
                "text": bodyinfo["text"],
                "body": bodyinfo,
                "authors": authors,
                "venue": str(first_present(it, "venue", "journal", "container", "源刊", default="")).strip(),
                "doi": str(first_present(it, "doi", "DOI", default="")).strip(),
                "type": str(first_present(it, "type", "pub_type", "kind", "document_type", default="")).strip().lower(),
                "source": str(first_present(it, "source", "database", "index", default="")).strip().lower(),
                "keywords": kws,
                "citations": cites,
                "year": first_present(it, "year", "publication_year", "issue_year", "见刊年"),
                "online_year": first_present(it, "online_year", "year_online", "online_first_year"),
                "preprint_year": first_present(it, "preprint_year", "arxiv_year"),
                "source_ref": str(first_present(it, "source_path", "source_file", "path", "file", default="") or ""),
                "source_id": str(first_present(it, "source_id", default="") or ""),
                "source_file": bodyinfo["source_file"],
                "raw": it,
            }
        )
    if not out:
        # 兜底：case 只给来源文件清单、未给 records 时，把每个可读来源当作一条记录。
        for src in collect_sources(case, bases):
            if not src.get("exists"):
                continue
            ref = str(src.get("path") or "")
            info = resolve_body({"source_path": ref, "title": src.get("title", "")}, bases)
            out.append({
                "record_id": f"REC{len(out) + 1:02d}", "title": str(src.get("title") or Path(ref).stem),
                "abstract": "", "text": info["text"], "body": info, "authors": [], "venue": "",
                "doi": "", "type": str(src.get("type") or ""), "source": "", "keywords": [],
                "citations": None, "year": None, "online_year": None, "preprint_year": None,
                "source_ref": ref, "source_id": str(src.get("source_id") or ""),
                "source_file": info["source_file"], "raw": {},
            })
    return out


def record_text(rec: dict) -> str:
    return norm(" ".join([rec["title"], rec["abstract"], rec["text"]]))


# --------------------------------------------------------------------------------------
# 1. 题录去重（DOI 优先、标题强归一兜底；预印本让位正式版）
# --------------------------------------------------------------------------------------
def _int_year(value):
    if value in (None, "", []):
        return None
    try:
        y = int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None
    return y if 1500 <= y <= 2200 else None


def _richness(rec: dict) -> int:
    fields = ("title", "abstract", "authors", "venue", "doi", "type", "keywords", "citations")
    return sum(1 for f in fields if rec.get(f) not in (None, "", [], 0))


def dedupe(records: list, dropped: list, codes: dict) -> list:
    """DOI 优先、标题强归一兜底；同组保留信息最全者（预印本让位正式版）。"""
    groups: dict = {}
    order: list = []
    for idx, r in enumerate(records):
        doi = norm(r["doi"]).replace("https://doi.org/", "")
        tkey = norm_title(r["title"])
        key = ("doi", doi) if doi else (("title", tkey) if tkey else ("id", r["record_id"]))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((idx, r))

    kept = []
    for key in order:
        bucket = groups[key]
        if len(bucket) == 1:
            kept.append(bucket[0][1])
            continue

        def sort_key(pair):
            idx, r = pair
            preprint = 1 if "preprint" in (r["type"] or "") or r["source"] in ("arxiv", "biorxiv", "medrxiv") else 0
            return (preprint, -_richness(r), idx)

        bucket_sorted = sorted(bucket, key=sort_key)
        winner = bucket_sorted[0][1]
        kept.append(winner)
        for _, r in bucket_sorted[1:]:
            code = "R_DUP_DOI" if key[0] == "doi" else "R_DUP_TITLE"
            dropped.append(
                {
                    "record_id": r["record_id"],
                    "title": r["title"],
                    "code": code,
                    "reason": codes.get(code, ""),
                    "kept_record_id": winner["record_id"],
                }
            )

    # 第二轮：跨 DOI 的标题碰撞（预印本与正式版 DOI 不同，必须靠标题强归一并拢）
    by_title = {}
    final = []
    for r in kept:
        tkey = norm_title(r["title"])
        if not tkey:
            final.append(r)
            continue
        if tkey not in by_title:
            by_title[tkey] = r
            final.append(r)
            continue
        prev = by_title[tkey]

        def pref(x):
            preprint = 1 if "preprint" in (x["type"] or "") or x["source"] in ("arxiv", "biorxiv", "medrxiv") else 0
            return (preprint, -_richness(x))

        if pref(r) < pref(prev):
            final[final.index(prev)] = r
            by_title[tkey] = r
            loser = prev
        else:
            loser = r
        dropped.append({"record_id": loser["record_id"], "title": loser["title"], "code": "R_DUP_TITLE",
                        "reason": codes.get("R_DUP_TITLE", ""), "kept_record_id": by_title[tkey]["record_id"]})
    return final


# --------------------------------------------------------------------------------------
# 2. 前瞻协议：先落协议块，再逐条筛选（复用 048 前瞻口径）
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
                          "detail": "case.json 未提供前瞻性筛选协议（纳入/排除标准、检索顺序、停止条件）",
                          "fallback_note": str(fb.get("note", "")),
                          "declaration": "以下筛选按声明回退口径执行并在每条记录标注 fallback=true，"
                                         "不冒充「已按注册协议筛选」"},
            "fallback": {"declared_in": "rules/screening_rules.json#fallback_screening",
                         "note": str(fb.get("note", ""))}}


def criterion_eval(crit: dict, rec: dict) -> dict:
    body_all = norm(rec["title"] + " " + rec["abstract"] + " " + rec["text"])
    terms, ok = [], True
    if crit["body_readable"]:
        readable = rec["body"]["method"] != "missing" and rec["body"]["n_chars"] >= crit["min_chars"]
        ok = ok and readable
        if readable:
            terms.append(f"body_chars={rec['body']['n_chars']}")
    for k in crit["all_keys"]:
        if _key_hit(k, body_all):
            terms.append(k)
        else:
            ok = False
    if crit["any_keys"]:
        hits = [k for k in crit["any_keys"] if _key_hit(k, body_all)]
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


def screen_records(records: list, protocol_block: dict) -> tuple:
    """对去重后的每条记录逐条执行纳排：命中/未命中哪条标准记入 criterion_hits。"""
    rows = []
    is_fallback = protocol_block["protocol_status"] == "missing_fallback_declared"
    for rec in records:
        if rec["body"]["method"] == "missing" or rec["body"]["n_chars"] == 0:
            outcome, incl_hits, excl_hits = "unscored_no_body", [], []
            reason = "记录正文不可读（内联/侧车/自研提取均失败），按口径不参与纳排与趋势统计，诚实降级"
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
                     "source_file": rec["source_file"], "source_id": rec["source_id"],
                     "extraction_method": rec["body"]["method"],
                     "body_sha256": rec["body"]["sha256"], "body_chars": rec["body"]["n_chars"],
                     "outcome": outcome,
                     "inclusion_hits": incl_hits, "exclusion_hits": excl_hits,
                     "fallback": is_fallback, "reason": reason})
    counts = {"screened": len(rows),
              "included": sum(1 for r in rows if r["outcome"] == "included"),
              "excluded": sum(1 for r in rows if r["outcome"] == "excluded"),
              "unscored_no_body": sum(1 for r in rows if r["outcome"] == "unscored_no_body")}
    return rows, counts


# --------------------------------------------------------------------------------------
# 3. 窗口 / 年份口径 / 证据桶
# --------------------------------------------------------------------------------------
def resolve_window(case: dict, records: list, stats: dict) -> dict:
    c = case.get("constraints") if isinstance(case.get("constraints"), dict) else {}

    def pick(*keys):
        for k in keys:
            for src in (c, case):
                if isinstance(src, dict) and src.get(k) not in (None, "", []):
                    return src[k]
        return None

    y_from = _int_year(pick("year_from", "from_year", "start_year", "window_start"))
    y_to = _int_year(pick("year_to", "to_year", "end_year", "window_end"))
    years = [y for y in (_int_year(r["year"]) or _int_year(r["online_year"]) or _int_year(r["preprint_year"])
                         for r in records) if y]
    start = y_from if y_from else (min(years) if years else None)
    end = y_to if y_to else (max(years) if years else None)
    return {
        "start": start,
        "end": end,
        "source": "case.constraints" if (y_from or y_to) else "records_span",
        "provided": {"year_from": y_from, "year_to": y_to},
        "physics_note": stats.get("window", {}).get("note", ""),
    }


def build_year_view(records: list, policy: dict) -> dict:
    field_map = policy.get("field_map", {})
    primary = policy.get("default_primary", "journal")
    view = {}
    for r in records:
        series = {}
        for name, fields in field_map.items():
            y, fld = None, None
            for f in fields:
                y = _int_year(r.get(f))
                if y:
                    fld = f
                    break
            series[name] = {"year": y, "field": fld}
        prim = series.get(primary, {"year": None, "field": None})
        imputed = False
        if not prim["year"]:
            fallback = _int_year(r.get("year"))
            if fallback:
                prim = {"year": fallback, "field": "year"}
                imputed = True
        view[r["record_id"]] = {
            "series": series,
            "primary": prim["year"],
            "primary_field": prim["field"],
            "year_imputed": imputed,
            "year_missing": prim["year"] is None,
        }
    return view


def build_buckets(window: dict, stats: dict) -> list:
    """证据桶：按 rules/statistics.json.trend_buckets.bucket_width_years 把窗口切成连续闭区间。"""
    cfg = stats.get("trend_buckets", {})
    width = max(1, int(cfg.get("bucket_width_years", 2)))
    start, end = window.get("start"), window.get("end")
    if start is None or end is None or end < start:
        return []
    buckets, cur, i = [], start, 0
    while cur <= end:
        b_end = min(end, cur + width - 1)
        buckets.append({"bucket_id": f"B{i + 1}", "start": cur, "end": b_end, "label": f"{cur}-{b_end}"})
        cur = b_end + 1
        i += 1
    return buckets


# --------------------------------------------------------------------------------------
# 4. 时间趋势（年度序列 / 同比 / CAGR / 拐点）
# --------------------------------------------------------------------------------------
def temporal_analysis(records: list, year_view: dict, window: dict, stats: dict, codes: dict, dropped: list) -> dict:
    start, end = window["start"], window["end"]
    included, out_of_window = [], []
    for r in records:
        y = year_view[r["record_id"]]["primary"]
        if y is None:
            dropped.append({"record_id": r["record_id"], "title": r["title"], "code": "R_YEAR_MISSING",
                            "reason": codes.get("R_YEAR_MISSING", "")})
            continue
        if start is not None and y < start:
            out_of_window.append(r["record_id"])
            dropped.append({"record_id": r["record_id"], "title": r["title"], "code": "R_OUT_OF_WINDOW",
                            "reason": codes.get("R_OUT_OF_WINDOW", ""), "year": y})
            continue
        if end is not None and y > end:
            out_of_window.append(r["record_id"])
            dropped.append({"record_id": r["record_id"], "title": r["title"], "code": "R_OUT_OF_WINDOW",
                            "reason": codes.get("R_OUT_OF_WINDOW", ""), "year": y})
            continue
        included.append(r)

    if start is None or end is None:
        start, end = 0, -1  # 池内无任何可用年份：序列为空，只保留缺失记账
    counts = {y: 0 for y in range(start, end + 1)}
    per_year_ids = {y: [] for y in range(start, end + 1)}
    series_counts = {name: {y: 0 for y in range(start, end + 1)}
                     for name in stats.get("year_policy", {}).get("series", ["journal"])}
    for r in included:
        rid = r["record_id"]
        y = year_view[rid]["primary"]
        counts[y] += 1
        per_year_ids[y].append(rid)
        for name in series_counts:
            sy = year_view[rid]["series"].get(name, {}).get("year")
            if sy is not None and start <= sy <= end:
                series_counts[name][sy] += 1

    gcfg = stats.get("growth", {})
    years = sorted(counts)
    yoy, floored = [], []
    for y in years[1:]:
        prev = counts[y - 1]
        denom = prev if prev > 0 else 1
        if prev == 0:
            floored.append(y)
        yoy.append({"year": y, "count": counts[y], "prev_count": prev,
                    "yoy": rounded((counts[y] - prev) / denom)})

    cagr = None
    nonzero = [y for y in years if counts[y] > 0]
    if len(years) >= int(gcfg.get("min_years_for_cagr", 3)) and len(nonzero) >= 2:
        y0, y1 = nonzero[0], nonzero[-1]
        if counts[y0] > 0 and y1 > y0:
            cagr = rounded((counts[y1] / counts[y0]) ** (1.0 / (y1 - y0)) - 1)

    infl = detect_inflection(counts, stats)

    missing = [r["record_id"] for r in records if year_view[r["record_id"]]["year_missing"]]
    imputed = [r["record_id"] for r in records if year_view[r["record_id"]]["year_imputed"]]
    total = len(records) or 1
    return {
        "window": {"start": window["start"], "end": window["end"], "source": window["source"]},
        "counting_unit": "一条题录计 1 次产出，按主年份字段归入单一年份（不重复计数）",
        "year_series": {name: [{"year": y, "count": c} for y, c in sorted(v.items())] for name, v in series_counts.items()},
        "primary_series": [{"year": y, "count": counts[y]} for y in years],
        "primary_series_name": stats.get("year_policy", {}).get("default_primary", "journal"),
        "total_in_window": len(included),
        "per_year_record_ids": {str(y): sorted(ids) for y, ids in per_year_ids.items() if ids},
        "yoy": yoy,
        "yoy_floor_applied": floored,
        "cagr": cagr,
        "cagr_basis": {"start_year": nonzero[0], "end_year": nonzero[-1],
                       "formula": gcfg.get("cagr_formula", "")} if cagr is not None else None,
        "inflection": infl,
        "year_missing": {"count": len(missing), "rate": rounded(len(missing) / total), "record_ids": sorted(missing)},
        "year_imputed": {"count": len(imputed), "rate": rounded(len(imputed) / total), "record_ids": sorted(imputed)},
        "excluded_out_of_window": {"count": len(out_of_window), "record_ids": sorted(out_of_window)},
        "counting_policy_note": stats.get("year_policy", {}).get("missing_policy", ""),
    }


def detect_inflection(counts: dict, stats: dict) -> dict:
    """滑均看形态 → 极值年定位断点 → 两侧水平均值判持久性（否则记为离散波动）。"""
    cfg = stats.get("inflection", {})
    w = max(1, int(cfg.get("smooth_window", 3)))
    years = sorted(counts)
    if len(years) < 3:
        return {"method": cfg.get("note", ""), "params": {"smooth_window": w},
                "candidates": [], "accepted": [], "volatility_only": [],
                "caveat": "窗口内可用年份不足 3 个，无法判定拐点（仅保留年度序列）"}
    smoothed = {}
    for i, y in enumerate(years):
        lo, hi = max(0, i - w // 2), min(len(years), i + w // 2 + 1)
        vals = [counts[years[j]] for j in range(lo, hi)]
        smoothed[y] = sum(vals) / len(vals)
    diff = [(years[i], smoothed[years[i]] - smoothed[years[i - 1]]) for i in range(1, len(years))]
    accel = [(diff[i][0], diff[i][1] - diff[i - 1][1]) for i in range(1, len(diff))]
    min_abs = float(cfg.get("min_abs_acceleration", 1.0))
    side = int(cfg.get("persist_side_years", 2))
    rel = float(cfg.get("break_rel_change", 0.4))
    labels = cfg.get("labels", {})

    candidates, seen = [], set()
    for i in range(1, len(diff)):
        prev_dir, cur_dir = diff[i - 1][1], diff[i][1]
        a = accel[i - 1][1] if i - 1 < len(accel) else 0.0
        if abs(a) < min_abs:
            continue
        if not ((prev_dir > 0 >= cur_dir) or (prev_dir < 0 <= cur_dir)):
            continue
        kind = "peak" if prev_dir > 0 >= cur_dir else "rebound"
        y_i = years.index(diff[i][0])
        near = years[max(0, y_i - 1): min(len(years), y_i + 2)]
        bp = (max if kind == "peak" else min)(near, key=lambda y: (counts[y], y))
        if (kind, bp) in seen:
            continue
        seen.add((kind, bp))
        before = [counts[y] for y in years if y < bp][-side:]
        after = [counts[y] for y in years if y > bp][:side]
        if not before or not after:
            continue
        mb, ma = sum(before) / len(before), sum(after) / len(after)
        change = (ma - mb) / (mb if mb > 0 else 1.0)
        persistent = change <= -rel if kind == "peak" else change >= rel
        candidates.append({
            "breakpoint_year": bp,
            "flip_year": diff[i][0],
            "type": kind,
            "label": labels.get(kind, kind) if persistent else labels.get("volatility", "离散波动"),
            "acceleration": rounded(a),
            "pre_side_counts": before,
            "post_side_counts": after,
            "pre_side_mean": rounded(mb),
            "post_side_mean": rounded(ma),
            "relative_change": rounded(change),
            "persistent": persistent,
            "basis": f"加速度 {rounded(a)}（阈值 {min_abs}）在 {diff[i][0]} 翻转；断点年 {bp} 两侧各 {side} 年"
                     f"水平均值 {rounded(mb)}→{rounded(ma)}，相对变化 {rounded(change)}（阈值 ±{rel}）",
        })
    candidates.sort(key=lambda c: (c["breakpoint_year"], c["type"]))
    return {
        "method": "3 点滑动均值 → 一阶差分 → 二阶差分定位加速度翻转；再用断点年两侧水平均值判定是否为持久变化",
        "params": {"smooth_window": w, "min_abs_acceleration": min_abs, "persist_side_years": side,
                   "break_rel_change": rel},
        "candidates": candidates,
        "accepted": [c for c in candidates if c["persistent"]],
        "volatility_only": [c for c in candidates if not c["persistent"]],
        "caveat": cfg.get("note", ""),
    }


# --------------------------------------------------------------------------------------
# 5. 主题归并与演化
# --------------------------------------------------------------------------------------
def get_taxonomy(case: dict, rules: dict) -> dict:
    custom = first_present(case, "topic_taxonomy", "topics", "topic_dictionary", default=None)
    if isinstance(custom, dict) and custom:
        norm_topics = {}
        for name, val in custom.items():
            if isinstance(val, dict):
                kws = val.get("keywords") or val.get("terms") or []
            else:
                kws = as_list(val)
            norm_topics[name] = {"label_zh": (val.get("label_zh") if isinstance(val, dict) else "") or name,
                                 "keywords": [str(k) for k in as_list(kws)]}
        return {"topics": norm_topics, "source": "case.topic_taxonomy", "matching": rules["taxonomy"].get("matching", {})}
    return {"topics": rules["taxonomy"].get("topics", {}), "source": "rules/topic_taxonomy.json",
            "matching": rules["taxonomy"].get("matching", {})}


def assign_topics(records: list, tax: dict) -> dict:
    cap = int(tax.get("matching", {}).get("max_topics_per_record", 3))
    assignment = {}
    for r in records:
        text = norm(" ".join([r["title"], r["abstract"], r["text"]]))
        hits = []
        for name, cfg in tax["topics"].items():
            kws = [norm(k) for k in cfg.get("keywords", []) if norm(k)]
            if any(_key_hit(k, text) for k in kws):
                hits.append(name)
        assignment[r["record_id"]] = sorted(hits)[:cap]
    return assignment


def make_periods(start: int, end: int, k: int) -> list:
    """均分且把余数分给靠前的段，避免出现只有 1 年的尾段（小样本段无法支撑占比判定）。"""
    span = end - start + 1
    if span <= 0:
        return []
    k = max(1, min(k, span))
    base, extra = divmod(span, k)
    sizes = [base + (1 if i < extra else 0) for i in range(k)]
    periods, cur = [], start
    for i, size in enumerate(sizes):
        p_start, p_end = cur, min(end, cur + size - 1)
        periods.append({"period_id": f"P{i + 1}", "start": p_start, "end": p_end, "label": f"{p_start}-{p_end}"})
        cur = p_end + 1
    return periods


def topic_evolution(records: list, assignment: dict, year_view: dict, temporal: dict, periods: list,
                    stats: dict, codes: dict) -> dict:
    thr = stats.get("thresholds", {})
    share_cfg = stats.get("topic_share", {})
    min_denom = int(share_cfg.get("min_records_for_share", 3))
    min_evt = int(thr.get("min_topic_records_for_event", 2))
    by_id = {r["record_id"]: r for r in records}

    def period_of(year):
        for p in periods:
            if p["start"] <= year <= p["end"]:
                return p["period_id"]
        return None

    # 薄证据 / 年份全缺主题：不产生演化事件，单列记账
    all_topics = sorted({t for ts in assignment.values() for t in ts})
    thin_topics, noyear_topics = set(), set()
    evidence_thin, year_data_missing = [], []
    for t in all_topics:
        ids = sorted(rid for rid, ts in assignment.items() if t in ts)
        if len(ids) < min_evt:
            thin_topics.add(t)
            evidence_thin.append({"topic": t, "record_ids": ids, "n_records": len(ids),
                                  "code": "R_EVIDENCE_THIN", "reason": codes.get("R_EVIDENCE_THIN", ""),
                                  "note": "池内记录数低于趋势判定下限：不产生演化事件，只报池内计数"})
            continue
        if not any(year_view[rid]["primary"] for rid in ids):
            noyear_topics.add(t)
            year_data_missing.append({"topic": t, "record_ids": ids, "n_records": len(ids),
                                      "code": "R_YEAR_MISSING", "reason": codes.get("R_YEAR_MISSING", ""),
                                      "note": "该主题记录年份字段全缺：不产生时间类演化事件，只报池内计数并声明"})

    tagged_by_period = {p["period_id"]: [] for p in periods}
    topic_records = {name: [] for name in all_topics if name not in thin_topics and name not in noyear_topics}
    untagged = []
    for rid, topics in assignment.items():
        y = year_view[rid]["primary"]
        if y is None:
            continue
        pid = period_of(y)
        if pid is None:
            continue
        if not topics:
            untagged.append(rid)
            continue
        tagged_by_period[pid].append(rid)
        for t in topics:
            if t in topic_records:
                topic_records[t].append(rid)

    matrix = {}
    for p in periods:
        pid = p["period_id"]
        denom = len(tagged_by_period[pid])
        matrix[pid] = {"period": p["label"], "denominator": denom, "share_reliable": denom >= min_denom, "topics": {}}
        for t in sorted(topic_records):
            ids = [rid for rid in topic_records[t] if period_of(year_view[rid]["primary"]) == pid]
            share = rounded(len(ids) / denom) if denom else 0.0
            matrix[pid]["topics"][t] = {"count": len(ids), "share": share, "record_ids": sorted(ids)}

    def share(t, pid):
        return matrix[pid]["topics"].get(t, {}).get("share", 0.0)

    def reliable(pid):
        return matrix[pid]["share_reliable"]

    events = []
    pids = [p["period_id"] for p in periods]
    for t in sorted(topic_records):
        shares = [share(t, pid) for pid in pids]
        counts = [matrix[pid]["topics"].get(t, {}).get("count", 0) for pid in pids]
        # 出现
        emerged_at = None
        for i, pid in enumerate(pids):
            if i == 0 or not reliable(pid):
                continue
            if counts[i] >= int(thr.get("emergence_min_records", 2)) \
                    and shares[i] >= float(thr.get("emergence_share", 0.12)) \
                    and all(s <= float(thr.get("emergence_prior_max_share", 0.02)) for s in shares[:i]):
                emerged_at = pid
                events.append({
                    "event": "emerging", "topic": t, "period": matrix[pid]["period"], "period_id": pid,
                    "evidence": {"period_share": shares[i], "prior_shares": shares[:i],
                                 "period_count": counts[i],
                                 "record_ids": matrix[pid]["topics"][t]["record_ids"]},
                    "basis": f"本窗占比 {shares[i]} ≥ 阈值 {thr.get('emergence_share')}，此前各窗占比均 ≤ "
                             f"{thr.get('emergence_prior_max_share')}",
                })
                break
        # 增长 / 衰减
        for i in range(1, len(pids)):
            if not (reliable(pids[i]) and reliable(pids[i - 1])):
                continue
            if emerged_at is not None and pids[i] == emerged_at:
                continue
            if counts[i - 1] < 1:
                continue
            delta = rounded(shares[i] - shares[i - 1])
            if delta >= float(thr.get("growing_delta_share", 0.08)):
                prior_max = max(shares[:i]) if shares[:i] else 0.0
                kind = "growing" if shares[i] > prior_max else "recovering"
                events.append({"event": kind, "topic": t, "period": matrix[pids[i]]["period"],
                               "period_id": pids[i],
                               "evidence": {"delta_share": delta, "shares": shares, "prior_max_share": prior_max,
                                            "current_share": shares[i],
                                            "record_ids": matrix[pids[i]]["topics"][t]["record_ids"]},
                               "basis": f"占比环比 +{delta} ≥ 阈值 {thr.get('growing_delta_share')}；"
                                        + ("创窗口内新高" if kind == "growing" else f"仍未回到前高 {prior_max}（按回升处理，不算增长）")})
        peak_i = max(range(len(shares)), key=lambda i: (shares[i], counts[i]))
        if any(e["topic"] == t and e["period_id"] == pids[-1] and e["event"] in ("growing", "recovering") for e in events):
            continue
        if shares and shares[-1] - shares[peak_i] <= float(thr.get("declining_delta_share", -0.08)) \
                and counts[peak_i] >= int(thr.get("declining_min_peak_records", 2)) \
                and counts[-1] < counts[peak_i]:
            events.append({"event": "declining", "topic": t, "period": matrix[pids[-1]]["period"],
                           "period_id": pids[-1],
                           "evidence": {"peak_period": matrix[pids[peak_i]]["period"], "peak_share": shares[peak_i],
                                        "last_share": shares[-1],
                                        "record_ids": matrix[pids[-1]]["topics"][t]["record_ids"],
                                        "peak_record_ids": matrix[pids[peak_i]]["topics"][t]["record_ids"]},
                           "basis": f"相对峰值回落 {rounded(shares[-1] - shares[peak_i])} ≤ 阈值 "
                                    f"{thr.get('declining_delta_share')}"})
        # 合并：与另一主题共现率跃升
        for other in sorted(topic_records):
            if other <= t:
                continue
            jac = []
            for pid in pids:
                a = set(id_ for id_ in topic_records[t] if period_of(year_view[id_]["primary"]) == pid)
                b = set(id_ for id_ in topic_records[other] if period_of(year_view[id_]["primary"]) == pid)
                jac.append(rounded(len(a & b) / len(a | b)) if (a | b) else 0.0)
            min_rec = int(thr.get("merge_min_topic_records", 2))
            for i in range(1, len(jac)):
                ca = matrix[pids[i]]["topics"].get(t, {}).get("count", 0)
                cb = matrix[pids[i]]["topics"].get(other, {}).get("count", 0)
                pa = matrix[pids[i - 1]]["topics"].get(t, {}).get("count", 0)
                pb = matrix[pids[i - 1]]["topics"].get(other, {}).get("count", 0)
                if min(ca, cb) < min_rec or pa < 1 or pb < 1:
                    continue
                if jac[i] >= float(thr.get("merge_jaccard", 0.35)) and jac[i - 1] < float(thr.get("merge_jaccard", 0.35)):
                    pid = pids[i]
                    a = {x for x in topic_records[t] if period_of(year_view[x]["primary"]) == pid}
                    b = {x for x in topic_records[other] if period_of(year_view[x]["primary"]) == pid}
                    events.append({"event": "merging", "topic": f"{t} + {other}", "period": matrix[pid]["period"],
                                   "period_id": pid,
                                   "evidence": {"jaccard_series": jac, "shared_record_ids": sorted(a & b)},
                                   "basis": f"两主题共现 Jaccard 由 {jac[i - 1]} 升至 {jac[i]} ≥ 阈值 "
                                            f"{thr.get('merge_jaccard')}"})
        # 分裂：分支在子集中分化
        for i, pid in enumerate(pids):
            base = {x for x in topic_records[t] if period_of(year_view[x]["primary"]) == pid}
            if len(base) < int(thr.get("split_min_base", 4)):
                continue
            branches = {}
            for other in sorted(topic_records):
                if other == t:
                    continue
                inter = {x for x in base if other in assignment.get(x, [])}
                if len(inter) / len(base) >= float(thr.get("split_branch_min_share", 0.3)):
                    branches[other] = inter
            names = sorted(branches)
            if len(names) < 2:
                continue
            pairs = [rounded(len(branches[a] & branches[b]) / len(branches[a] | branches[b]))
                     for a in names for b in names if a < b]
            sep = min(pairs) if pairs else 0.0
            if sep <= float(thr.get("split_pair_jaccard_max", 0.2)):
                events.append({"event": "splitting", "topic": t, "period": matrix[pid]["period"], "period_id": pid,
                               "evidence": {"shared_record_ids": sorted({x for v in branches.values() for x in v}),
                                            "branches": names,
                                            "branch_share": {k: rounded(len(v) / len(base)) for k, v in branches.items()},
                                            "min_pair_jaccard": sep, "max_pair_jaccard": max(pairs) if pairs else 0.0},
                               "basis": f"阶段内出现 ≥2 个分支（占比 ≥ {thr.get('split_branch_min_share')}），且至少一对分支 Jaccard ≤ "
                                        f"{thr.get('split_pair_jaccard_max')}（存在分化的子社群）"})

    order = {"emerging": 0, "merging": 1, "splitting": 2, "growing": 3, "recovering": 4, "declining": 5}
    for e in events:
        pid = e["period_id"]
        denom = matrix[pid]["denominator"]
        ev_ids = e["evidence"].get("record_ids") or e["evidence"].get("shared_record_ids") or []
        e["confidence"] = "low" if (denom < 5 or len(ev_ids) < 2) else "medium"
        e["needs_review"] = e["event"] in ("merging", "splitting") or e["confidence"] == "low"
        e["evidence_scope"] = f"阶段内已打标签题录 {denom} 条，事件涉及 {len(ev_ids)} 条"
    events.sort(key=lambda e: (e["period_id"], order.get(e["event"], 9), e["topic"]))

    considered = [x for v in tagged_by_period.values() for x in v] + untagged
    untagged_rate = rounded(len(untagged) / max(1, len(considered)))
    return {
        "taxonomy_source": None,  # 由调用方回填
        "periods": periods,
        "matrix": matrix,
        "events": events,
        "evidence_thin": evidence_thin,
        "year_data_missing": year_data_missing,
        "untagged": {"count": len(untagged), "rate": untagged_rate, "record_ids": sorted(untagged)},
        "phase_rule": "阶段划分 = 统计窗口均分为 "
                      f"{len(periods)} 段（rules/statistics.json 的 period 约定由调用方传入）；"
                      "阶段标签只描述时间区间，不代表领域公认的历史分期",
        "thin_guard": "薄证据主题（记录数 < min_topic_records_for_event）与年份全缺主题不产生演化事件，"
                      "单列 evidence_thin / year_data_missing；趋势句见 frontier_trends 的同名单列",
        "caveat": "演化事件的『因何变化』只在证据矩阵里给出题录层面的共现/占比证据；"
                  "本 skill 不联网取政策或产业事件，因果归因一律 needs_review",
    }


# --------------------------------------------------------------------------------------
# 6. 逐字证据表（record_id + source_file + char_span + 逐字 quote + 校验）与冲突双列
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


def _key_hit(key: str, text: str) -> bool:
    """CJK 键用子串命中；纯 ASCII 键用词边界命中并允许复数后缀（pouch cell → pouch cells），
    避免 'ion' 命中 'function' 一类误报。"""
    if any("\u4e00" <= ch <= "\u9fff" for ch in key):
        return key in text
    return re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?:s|es)?(?![a-z0-9])", text) is not None


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


def _quote_fields(rec: dict) -> list:
    """quote 字段优先级：正文 text 优先；正文与摘要同文（inline_abstract）时只取 abstract，避免重复计引。"""
    same = bool(rec["text"]) and bool(rec["abstract"]) and norm(rec["text"]) == norm(rec["abstract"])
    if same:
        return ["abstract"]
    return [f for f in ("text", "abstract") if rec[f]]


def build_topic_evidence(pool: list, assignment: dict, tax: dict, direction_rules: dict, stats: dict) -> tuple:
    """每个（池内记录 × 命中主题）取证据最强的一句逐字引文：matched_keywords → 句长。"""
    cfg = stats.get("evidence_quotes", {})
    term = [str(t) for t in as_list(cfg.get("sentence_terminators", _SENT_TERM))]
    split_period = bool(cfg.get("split_ascii_period", True))
    rows, by_topic = [], {}
    for rec in pool:
        topics = assignment.get(rec["record_id"], [])
        if not topics:
            continue
        for t in topics:
            kws = [norm(k) for k in (tax["topics"].get(t, {}).get("keywords") or []) if norm(k)]
            if not kws:
                continue
            best = None
            for fld in _quote_fields(rec):
                src_text = rec[fld]
                for sent in split_sentences(src_text, term, split_period):
                    sn, tn = norm(sent), norm(rec["title"])
                    if tn and (sn == tn or sn in tn or tn in sn):
                        continue  # 题名不进引文：与记录标题互含的标题行跳过
                    hit = sorted({k for k in kws if _key_hit(k, sn)})
                    if not hit:
                        continue
                    if best is None or (len(hit), len(sent)) > (best[0], best[1]):
                        best = (len(hit), len(sent), fld, sent, hit)
            if best is None:
                continue
            _n_hit, _n_chars, fld, sent, hit = best
            field_text = rec[fld]
            start = field_text.find(sent)
            end = start + len(sent) if start >= 0 else -1
            exact = start >= 0 and field_text[start:end] == sent
            eid = f"E{len(rows) + 1:03d}"
            row = {
                "evidence_id": eid,
                "topic": t,
                "record_id": rec["record_id"],
                "title": rec["title"],
                "year": _int_year(rec.get("year")),
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
                "matched_keywords": hit,
                "direction": detect_direction(sent, direction_rules),
                "sha256_quote": sha256_text(norm(sent)),
            }
            if not exact:
                row["quote_mismatch"] = "quote 不是该字段原文的精确子串（span 定位失败），已显式标记，不静默"
            rows.append(row)
            by_topic.setdefault(t, []).append(row)
    return rows, by_topic


def _conflict_side(row: dict) -> dict:
    d = row.get("direction") or {}
    return {"evidence_id": row["evidence_id"], "record_id": row["record_id"],
            "source_file": row["source_file"], "quote": row["quote"],
            "quote_verified": row["quote_verified"],
            "direction": d.get("value"), "direction_terms": d.get("terms", []),
            "strength_terms": d.get("strength_terms", [])}


def detect_topic_conflicts(rows: list, direction_rules: dict) -> list:
    """同主题 + 共享命中关键词的两条证据做方向比对：反向 / 零效 vs 方向 → 双列，不合并不择一。"""
    conflicts = []
    by_topic = {}
    for r in rows:
        by_topic.setdefault(r["topic"], []).append(r)
    polar = {"+1", "-1"}
    for topic in sorted(by_topic):
        rs = sorted(by_topic[topic], key=lambda r: r["evidence_id"])
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                a, b = rs[i], rs[j]
                shared = sorted(set(a["matched_keywords"]) & set(b["matched_keywords"]))
                if not shared:
                    continue
                va = (a.get("direction") or {}).get("value")
                vb = (b.get("direction") or {}).get("value")
                if va in polar and vb in polar and va != vb:
                    kind = "direction_conflict"
                    detail = f"同主题证据方向相反：{va} 与 {vb}（共享关键词 {shared}）"
                elif {va, vb} in ({"0", "+1"}, {"0", "-1"}):
                    kind = "zero_vs_direction"
                    detail = (f"同主题下一条为零效应（0）、另一条为方向效应（{vb if va == '0' else va}），口径差异待核"
                              f"（共享关键词 {shared}）")
                else:
                    continue
                conflicts.append({
                    "conflict_id": f"C{len(conflicts) + 1:02d}", "type": kind, "topic": topic,
                    "shared_keywords": shared, "detail": detail,
                    "side_a": _conflict_side(a), "side_b": _conflict_side(b),
                    "resolution": "并列双列呈现（side_a/side_b 各带逐字引文、方向与强度词），不择一、不合并、"
                                  "不以「尚存争议」替代双列；趋势计数按记录数统计，不受方向判定影响",
                })
    return conflicts


# --------------------------------------------------------------------------------------
# 7. 趋势证据桶与前沿判定（rising / established / thin / no_year_data / flat + frontier）
# --------------------------------------------------------------------------------------
def _background_quote(rec: dict) -> tuple:
    """无关键词命中句时的背景代表引文：正文/摘要最长整句（仍为逐字精确子串）。"""
    for fld in _quote_fields(rec):
        text = rec[fld]
        sents = split_sentences(text, _SENT_TERM, True) or [text.strip()]
        best = max(sents, key=lambda s: (len(s), s))
        start = text.find(best)
        if start < 0:
            continue
        return best, fld, {"start": start, "end": start + len(best)}
    return "", "", {"start": -1, "end": -1}


def claim_representatives(topic: str, st: dict, rows_by_topic: dict, pool: list, rep_cfg: dict) -> list:
    """趋势主张的代表记录：去重后取证据最强的 2-3 条，逐字引文 + char_span；不足时补背景代表。"""
    cfg = rep_cfg.get("trend_evidence", {})
    max_n = int(cfg.get("max_per_claim", 3))
    min_strong = int(cfg.get("min_strong", 2))
    by_id = {r["record_id"]: r for r in pool}
    rows = sorted(rows_by_topic.get(topic, []),
                  key=lambda r: (-len(r["matched_keywords"]), -len(r["quote"]), r["record_id"]))
    picked, seen = [], set()
    for r in rows:
        if r["record_id"] in seen:
            continue
        seen.add(r["record_id"])
        picked.append({
            "record_id": r["record_id"], "title": r["title"], "year": r["year"],
            "source_file": r["source_file"], "extraction_method": r["extraction_method"],
            "quote_field": r["quote_field"], "quote": r["quote"],
            "char_span": r["char_span"], "quote_verified": r["quote_verified"],
            "matched_keywords": r["matched_keywords"], "evidence_id": r["evidence_id"],
            "selection_basis": "桶内证据最强（matched_keywords 数 → 引文长度），逐字引文 + char_span，同记录去重",
        })
        if len(picked) >= max_n:
            break
    if len(picked) < min_strong:
        for rid in st["ids"]:
            if rid in seen:
                continue
            rec = by_id.get(rid)
            if rec is None:
                continue
            quote, fld, span = _background_quote(rec)
            if not quote:
                continue  # 无正文/摘要的记录不得进入代表记录
            seen.add(rid)
            picked.append({
                "record_id": rid, "title": rec["title"], "year": _int_year(rec.get("year")),
                "source_file": rec["source_file"], "extraction_method": rec["body"]["method"],
                "quote_field": fld, "quote": quote, "char_span": span, "quote_verified": True,
                "matched_keywords": [], "evidence_id": "",
                "selection_basis": "关键词命中句不足 min_strong，补入背景代表：正文最长整句（逐字精确子串，无方向判定）",
            })
            if len(picked) >= max_n:
                break
    return picked


def build_trend_claims(pool: list, assignment: dict, year_view: dict, buckets: list, rows_by_topic: dict,
                       conflicts: list, tax: dict, stats: dict, rep_cfg: dict, codes: dict) -> list:
    """每条趋势主张 = 主题 + 分桶计数表 + 代表记录（逐字）+ 判定依据 +（rising 时的）前沿基数对比。"""
    cfg_b = stats.get("trend_buckets", {})
    cfg_v = stats.get("trend_verdict", {})
    min_buckets = int(cfg_b.get("min_buckets_with_records", 2))
    width = int(cfg_b.get("bucket_width_years", 2))
    labels = {name: (cfg.get("label_zh") or name) for name, cfg in tax["topics"].items()}
    all_topics = sorted({t for ts in assignment.values() for t in ts})

    state = {}
    for t in all_topics:
        ids = sorted(rid for rid, ts in assignment.items() if t in ts)
        yids = [rid for rid in ids if year_view[rid]["primary"]]
        noyear = sorted(rid for rid in ids if not year_view[rid]["primary"])
        btable = []
        for b in buckets:
            b_ids = sorted(rid for rid in yids if b["start"] <= year_view[rid]["primary"] <= b["end"])
            btable.append({"bucket_id": b["bucket_id"], "label": b["label"], "start": b["start"], "end": b["end"],
                           "count": len(b_ids), "record_ids": b_ids})
        state[t] = {"ids": ids, "n": len(ids), "yids": yids, "noyear": noyear,
                    "btable": btable, "counts": [b["count"] for b in btable]}

    # 判定一：verdict（thin → no_year_data → rising → established → flat）
    for t, st in state.items():
        counts = st["counts"]
        n = st["n"]
        if n <= int(cfg_v.get("thin_max_records", 1)):
            st["verdict"] = "thin"
            st["basis"] = (f"池内仅 {n} 条记录（≤ thin_max_records={cfg_v.get('thin_max_records')}），证据太薄："
                           f"不作趋势主张，只报池内计数（{codes.get('R_EVIDENCE_THIN', 'R_EVIDENCE_THIN')}）")
            continue
        if not st["yids"]:
            st["verdict"] = "no_year_data"
            st["basis"] = (f"{n} 条记录年份字段全缺（{codes.get('R_YEAR_MISSING', 'R_YEAR_MISSING')}）："
                           f"不做时间趋势，只报池内计数 {n} 条并显式声明")
            continue
        nz = [i for i, c in enumerate(counts) if c > 0]
        n_buckets = len(nz)
        total = sum(counts)
        if n_buckets < min_buckets or total == 0:
            st["verdict"] = "flat"
            st["basis"] = (f"有记录桶数 {n_buckets} < min_buckets_with_records={min_buckets}：不判时间方向，"
                           f"只作池内计数观察（各桶计数 {counts}）")
            continue
        early_n = (len(counts) + 1) // 2
        early_share = sum(counts[:early_n]) / total
        late_share = 1.0 - early_share
        first_nz, last = counts[nz[0]], counts[-1]
        if (last >= int(cfg_v.get("rising_min_last_bucket_count", 2))
                and (last - first_nz) >= int(cfg_v.get("rising_min_bucket_gain", 1))
                and (late_share - early_share) >= float(cfg_v.get("rising_min_late_early_share_gap", 0.1))):
            st["verdict"] = "rising"
            st["basis"] = (f"末桶计数 {last} ≥ rising_min_last_bucket_count={cfg_v.get('rising_min_last_bucket_count')}，"
                           f"较首个非空桶（{buckets[nz[0]]['label']}，{first_nz} 条）增量 {last - first_nz} ≥ "
                           f"rising_min_bucket_gain={cfg_v.get('rising_min_bucket_gain')}，后半窗占比 {rounded(late_share)} "
                           f"高出前半窗 {rounded(early_share)} 达 {rounded(late_share - early_share)} ≥ "
                           f"rising_min_late_early_share_gap={cfg_v.get('rising_min_late_early_share_gap')}"
                           f"（桶计数 {counts}）")
        elif (total >= int(cfg_v.get("established_min_records", 2))
                and early_share >= float(cfg_v.get("established_min_early_share", 0.6))
                and first_nz >= int(cfg_v.get("established_min_first_bucket_count", 2))):
            st["verdict"] = "established"
            st["basis"] = (f"前 {early_n} 个证据桶占比 {rounded(early_share)} ≥ "
                           f"established_min_early_share={cfg_v.get('established_min_early_share')}，首个非空桶"
                           f"（{buckets[nz[0]]['label']}）计数 {first_nz} ≥ "
                           f"established_min_first_bucket_count={cfg_v.get('established_min_first_bucket_count')}"
                           f"（桶计数 {counts}）；established 只描述『记录集中于早期桶』的池内快照分布，"
                           f"不下全局成熟/衰退结论，是否回落由 topic_evolution 事件按其阈值另行判定")
        else:
            st["verdict"] = "flat"
            st["basis"] = ("桶间分布未达 rising/established 阈值（判据见 rules/statistics.json.trend_verdict），"
                           f"仅报桶计数 {counts}")

    # 判定二：前沿 = rising 且近桶新起，且近桶计数高于既有主题基数（对比数值写进 claim）
    def base_of(claim_topic: str) -> tuple:
        names = [x for x in all_topics if x != claim_topic
                 and (state[x]["verdict"] == "established" or (state[x]["counts"] and state[x]["counts"][0] > 0))]
        vals = sorted(state[x]["counts"][-1] for x in names)
        if not vals:
            return [], None
        k = len(vals)
        med = vals[k // 2] if k % 2 == 1 else (vals[k // 2 - 1] + vals[k // 2]) / 2
        return names, med

    for t, st in state.items():
        st["frontier"] = None
        if st["verdict"] != "rising" or not buckets:
            continue
        nz = [i for i, c in enumerate(st["counts"]) if c > 0]
        first_idx = nz[0] if nz else -1
        near = st["counts"][-1]
        base_topics, base_value = base_of(t)
        new_start = first_idx >= int(cfg_v.get("frontier_new_start_min_bucket_index", 1))
        enough = near >= int(cfg_v.get("frontier_min_near_bucket_count", 2))
        beats = base_value is not None and near > base_value
        frontier = {
            "new_start": bool(new_start),
            "first_nonempty_bucket": buckets[first_idx]["label"] if first_idx >= 0 else "",
            "prior_bucket_counts": {buckets[i]["label"]: st["counts"][i] for i in range(first_idx)},
            "near_bucket": {"bucket_id": buckets[-1]["bucket_id"], "label": buckets[-1]["label"], "count": near},
            "base_comparison": {
                "stat": cfg_v.get("frontier_base_stat", "median"),
                "base_topics": sorted(base_topics),
                "base_near_bucket_counts": [state[x]["counts"][-1] for x in sorted(base_topics)],
                "base_value": rounded(base_value) if base_value is not None else None,
                "claim_topic_near_count": near,
            },
            "is_frontier_candidate": bool(new_start and enough and beats),
            "reason_if_not": "",
            "rule": cfg_v.get("verdict_rules", {}).get("frontier", ""),
        }
        reasons = []
        if not new_start:
            reasons.append(f"非新起：首个非空桶 {buckets[first_idx]['label']} 已有记录")
        if not enough:
            reasons.append(f"近桶计数 {near} < frontier_min_near_bucket_count={cfg_v.get('frontier_min_near_bucket_count')}")
        if base_value is None:
            reasons.append("池内无既有主题可作基数对照，相对增量无法判定")
        elif not beats:
            reasons.append(f"近桶计数 {near} 未高于既有主题近桶{cfg_v.get('frontier_base_stat', 'median')}值 {rounded(base_value)}")
        frontier["reason_if_not"] = "；".join(reasons)
        st["frontier"] = frontier

    # 组装主张
    claims = []
    for t in all_topics:
        st = state[t]
        fr = st["frontier"] or {}
        head = f"主题 {t}（{labels.get(t, t)}）"
        if st["verdict"] == "thin":
            claim = f"{head}池内仅 {st['n']} 条记录：证据太薄，不作趋势主张，只报池内计数"
        elif st["verdict"] == "no_year_data":
            claim = f"{head}{st['n']} 条记录年份字段全缺：不做时间趋势，只报池内计数 {st['n']} 条并显式声明"
        else:
            claim = f"{head}判为 {st['verdict']}"
            if fr.get("is_frontier_candidate"):
                stat_zh = {"median": "中位"}.get(str(fr["base_comparison"]["stat"]), fr["base_comparison"]["stat"])
                claim += (f"；前沿候选：近桶（{fr['near_bucket']['label']}）计数 {fr['near_bucket']['count']} 高于既有主题"
                          f"近桶{stat_zh}值 {fr['base_comparison']['base_value']}"
                          f"（基数 {fr['base_comparison']['base_topics']}），相对增量明确")
            elif st["verdict"] == "rising" and fr:
                claim += f"；非前沿候选（{fr.get('reason_if_not', '')}）"
            claim += "。" + st["basis"]
        cids = sorted({c["conflict_id"] for c in conflicts if c["topic"] == t})
        claims.append({
            "claim_id": f"T{len(claims) + 1:02d}",
            "topic": t,
            "topic_label": labels.get(t, t),
            "verdict": st["verdict"],
            "claim": claim,
            "bucket_table": st["btable"],
            "bucket_stats": {"bucket_width_years": width,
                             "buckets_with_records": sum(1 for c in st["counts"] if c > 0),
                             "min_buckets_required": min_buckets,
                             "counts": st["counts"],
                             "records_without_year": st["noyear"]},
            "representative_records": claim_representatives(t, st, rows_by_topic, pool, rep_cfg),
            "frontier": st["frontier"],
            "conflict_ids": cids,
            "record_ids": st["ids"],
            "basis": st["basis"],
            "claim_scope": cfg_v.get("claim_scope", "pool_snapshot"),
            "scope_note": "本判定只描述本 case 池内快照（去重 + 前瞻协议筛选后），不构成领域全局趋势或前沿结论",
        })
    return claims


# --------------------------------------------------------------------------------------
# 8. 词频热度审计（词频高 ≠ 趋势：去重后热度坍缩显式呈现）
# --------------------------------------------------------------------------------------
def keyword_heat_audit(pool: list, tax: dict, stats: dict, buckets: list, year_view: dict) -> dict:
    cfg = stats.get("keyword_heat", {})
    min_hits = int(cfg.get("min_raw_hits_to_audit", 3))
    texts = {r["record_id"]: record_text(r) for r in pool}
    src_of = {r["record_id"]: (r["source_id"] or r["source_file"] or "inline") for r in pool}
    rows, below = [], 0
    for t in sorted(tax["topics"]):
        for kw in sorted({norm(k) for k in tax["topics"][t].get("keywords", []) if norm(k)}):
            raw = sum(text.count(kw) for text in texts.values())
            ids = sorted(rid for rid, tx in texts.items() if _key_hit(kw, tx))
            if raw < min_hits or not ids:
                below += 1
                continue
            years = sorted({year_view[rid]["primary"] for rid in ids if year_view[rid]["primary"]})
            covered = []
            for b in buckets:
                n_b = sum(1 for rid in ids
                          if year_view[rid]["primary"] and b["start"] <= year_view[rid]["primary"] <= b["end"])
                if n_b:
                    covered.append({"bucket_id": b["bucket_id"], "label": b["label"], "records": n_b})
            distinct = len(ids)
            single_bucket = (bool(covered) and len(covered) == 1
                             and covered[0]["records"] <= int(cfg.get("collapse_max_single_bucket_records", 1)))
            collapse = distinct <= int(cfg.get("collapse_max_distinct_records", 1)) or single_bucket
            rows.append({
                "topic": t, "keyword": kw, "raw_hits": raw,
                "distinct_records": distinct, "record_ids": ids,
                "distinct_sources": len({src_of[rid] for rid in ids}),
                "record_years": years, "buckets_covered": covered,
                "heat_collapse": bool(collapse),
                "collapse_reason": (f"词频 {raw} 次但去重后仅 {distinct} 条记录"
                                    + ("，且全部落在同一证据桶" if single_bucket else "")
                                    + "：词频热度坍缩，不构成趋势（R_HEAT_COLLAPSED）") if collapse else "",
                "verdict": "词频仅作对照审计；该关键词相关趋势一律按记录级证据桶计数",
            })
    rows.sort(key=lambda r: (-r["raw_hits"], r["topic"], r["keyword"]))
    return {
        "rows": rows,
        "summary": {
            "audited_keywords": len(rows),
            "heat_collapsed": sum(1 for r in rows if r["heat_collapse"]),
            "below_audit_threshold": below,
            "note": cfg.get("note", ""),
        },
    }


# --------------------------------------------------------------------------------------
# 9. 子问题与零证据检索缺口（复用 048 口径：空白只来自检索缺口）
# --------------------------------------------------------------------------------------
def expand_keys(terms: list) -> list:
    out = []
    for term in terms:
        t = norm(term)
        if t and t not in out:
            out.append(t)
        for tok in tokens(term):
            if len(tok) < 2 or tok.isdigit() or tok in out:
                continue
            out.append(tok)
    return out


def build_subquestions(case: dict, topic_text: str) -> list:
    raw = first_present(case, "subquestions", "research_subquestions", "questions", default=[])
    subs = []
    for i, item in enumerate(as_list(raw), start=1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = str(first_present(item, "text", "question", "title", default="") or "").strip()
        sid = str(first_present(item, "subquestion_id", "id", default=f"SQ{i}"))
        keys = [norm(k) for k in as_list(first_present(item, "keys", "query_keys", "search_keys", "terms", default=[])) if norm(k)]
        if not keys and text:
            keys = expand_keys([text])[:8]
        subs.append({"subquestion_id": sid, "text": text, "keys": keys, "source": "case_declared",
                     "evidence_requirement": str(first_present(item, "evidence_requirement", default="") or "")})
    if subs:
        return subs
    keys = expand_keys([topic_text])[:8] if topic_text else []
    return [{"subquestion_id": "SQ1", "text": (topic_text[:200] or "（未提供研究问题，按题面主题词覆盖统计）"),
             "keys": keys, "source": "generic_fallback",
             "evidence_requirement": "需要与主题直接对应的原始记录"}]


def subquestion_coverage(subs: list, pool: list) -> list:
    texts = {r["record_id"]: record_text(r) for r in pool}
    rows = []
    for s in subs:
        keys = s["keys"]
        hits = {k: [rid for rid, tx in sorted(texts.items()) if _key_hit(k, tx)] for k in keys}
        ids = sorted({rid for klist in hits.values() for rid in klist})
        rows.append({
            "subquestion_id": s["subquestion_id"], "text": s["text"], "keys": keys,
            "keys_absent": sorted(k for k in keys if not hits.get(k)),
            "evidence_records": len(ids), "record_ids": ids,
            "level": "zero" if not ids else ("weak" if len(ids) == 1 else "covered"),
            "evidence_requirement": s.get("evidence_requirement", ""),
        })
    return rows


# --------------------------------------------------------------------------------------
# 10. 代表作（阶段级多口径，保留原三口径）
# --------------------------------------------------------------------------------------
def select_representatives(records: list, assignment: dict, year_view: dict, periods: list,
                           rep_cfg: dict, codes: dict) -> dict:
    by_id = {r["record_id"]: r for r in records}
    per_period_max = int(rep_cfg.get("limits", {}).get("per_period_max", 3))
    rows, gaps = [], []
    cites_available = any(r["citations"] is not None for r in records)

    def period_of(year):
        for p in periods:
            if p["start"] <= year <= p["end"]:
                return p
        return None

    for p in periods:
        pid, label = p["period_id"], p["label"]
        in_period = [rid for rid in by_id if year_view[rid]["primary"] and period_of(year_view[rid]["primary"]) == p]
        topic_set = sorted({t for rid in in_period for t in assignment.get(rid, [])})
        picked = {}
        for t in topic_set:
            tids = [rid for rid in in_period if t in assignment.get(rid, [])]
            if not tids:
                continue
            # 首创候选：窗口内该主题最早
            all_topic_ids = [rid for rid in by_id if t in assignment.get(rid, []) and year_view[rid]["primary"]]
            earliest_window = min((year_view[rid]["primary"], rid) for rid in all_topic_ids) if all_topic_ids else None
            fm = sorted(((year_view[rid]["primary"], rid) for rid in tids))[0]
            if earliest_window and fm[0] == earliest_window[0]:
                picked.setdefault(fm[1], {"record_id": fm[1], "criteria": [], "topics": []})
                picked[fm[1]]["criteria"].append("first_mover")
            # 高被引候选
            if cites_available:
                cited = [(by_id[rid]["citations"] or 0, rid) for rid in tids if by_id[rid]["citations"] is not None]
                if cited:
                    best = sorted(cited, key=lambda x: (-x[0], x[1]))[0]
                    picked.setdefault(best[1], {"record_id": best[1], "criteria": [], "topics": []})
                    picked[best[1]]["criteria"].append("most_cited")
            # 主题中心候选
            centroid = set()
            for rid in tids:
                centroid |= set(tokens(by_id[rid]["title"] + " " + by_id[rid]["abstract"]))
            scored = []
            for rid in tids:
                tk = set(tokens(by_id[rid]["title"] + " " + by_id[rid]["abstract"]))
                inter = len(tk & centroid)
                union = len(tk | centroid) or 1
                scored.append((inter / union, rid))
            best_c = sorted(scored, key=lambda x: (-x[0], x[1]))[0]
            picked.setdefault(best_c[1], {"record_id": best_c[1], "criteria": [], "topics": []})
            picked[best_c[1]]["criteria"].append("topic_central")
            for _rid, row in picked.items():
                if t in assignment.get(_rid, []) and t not in row["topics"]:
                    row["topics"].append(t)

        chosen = sorted(picked.values(), key=lambda r: (len(r["criteria"]), r["record_id"]), reverse=True)[:per_period_max]
        for row in chosen:
            r = by_id[row["record_id"]]
            row.update({
                "period": label,
                "period_id": pid,
                "title": r["title"],
                "year": year_view[r["record_id"]]["primary"],
                "venue": r["venue"],
                "citations": r["citations"],
                "topics": sorted(set(row["topics"])),
                "criteria": sorted(set(row["criteria"])),
                "selection_rationale": "；".join(
                    {"first_mover": "该主题在统计窗口内最早出现（不看被引，避免漏掉长尾首创工作）",
                     "most_cited": "阶段内该主题被引最高（题录自带引用数）",
                     "topic_central": "与该阶段该主题词袋平均 Jaccard 最高（最典型的做法）"}[c]
                    for c in sorted(set(row["criteria"]))
                ),
                "abstract_quote": snippet(r["abstract"], 0, 120) if r["abstract"] else "",
            })
            rows.append(row)
        if not chosen:
            gaps.append({"type": "R_NO_REP", "period": label, "period_id": pid,
                         "reason": codes.get("R_NO_REP", ""), "record_ids": sorted(in_period)})

    covered = {r["period_id"] for r in rows}
    return {
        "criteria_policy": rep_cfg,
        "citations_available": cites_available,
        "representatives": rows,
        "period_coverage": rounded(len(covered) / max(1, len(periods))),
        "gaps": gaps,
        "bias_note": "代表作同时给首创与高被引两口径；两者不一致时两条都保留，避免『只按总被引排序』。",
    }


# --------------------------------------------------------------------------------------
# 11. 研究空白（零证据子问题 + 覆盖缺口 + 未收敛问题 + 维度缺口）
# --------------------------------------------------------------------------------------
def research_gaps(records: list, assignment: dict, year_view: dict, periods: list, gap_cfg: dict,
                  stats: dict, codes: dict, matrix: dict, sub_ev: list, screen_counts: dict) -> dict:
    by_id = {r["record_id"]: r for r in records}
    topics = sorted({t for ts in assignment.values() for t in ts})
    pids = [p["period_id"] for p in periods]
    labels = {p["period_id"]: p["label"] for p in periods}
    gaps = []

    # A. 零证据 / 弱证据子问题（检索缺口，复用 048 口径：空白只来自检索缺口）
    rg = gap_cfg["gap_types"]["retrieval_gap"]
    for row in sub_ev:
        if row["level"] == "covered":
            continue
        zero = row["level"] == "zero"
        key_note = "（涉及键 " + ", ".join(row["keys"][:4]) + "）" if row["keys"] else ""
        gaps.append({
            "type": "retrieval_gap",
            "subquestion_id": row["subquestion_id"],
            "claim": f"子问题「{row['text'][:80]}」在本池内"
                     f"{'零达阈值证据' if zero else '仅有 1 条弱关联记录'}{key_note}",
            "claim_strength": rg["claim_strength"] if zero else rg["weak_claim_strength"],
            "basis": rg["basis"] if zero else rg["weak_basis"],
            "evidence_record_ids": row["record_ids"],
            "retrieval_scope": "本池（去重 + 前瞻协议筛选后）题名 + 摘要 + 正文的键覆盖统计；键来自 case.subquestions[].keys（或题面分词）",
            "exclusions": f"被协议筛除 {screen_counts.get('excluded', 0)} 条、正文不可读 "
                          f"{screen_counts.get('unscored_no_body', 0)} 条，均不参与该覆盖统计",
            "upgrade_requires": rg["upgrade_requires"] if zero else rg["weak_upgrade_requires"],
            "needs_review": True,
        })

    min_other = int(gap_cfg.get("gap_types", {}).get("coverage_gap", {}).get("min_other_period_records", 2))

    # B. 覆盖缺口
    for t in topics:
        total = sum(matrix[pid]["topics"].get(t, {}).get("count", 0) for pid in pids)
        if total < min_other:
            continue
        for pid in pids:
            if matrix[pid]["topics"].get(t, {}).get("count", 0) == 0:
                others = [f"{labels[q]}={matrix[q]['topics'].get(t, {}).get('count', 0)}" for q in pids if q != pid]
                gaps.append({
                    "type": "coverage_gap",
                    "topic": t,
                    "period": labels[pid],
                    "claim": f"主题 {t} 在 {labels[pid]} 窗口内零命中，其他窗口命中（{', '.join(others)}）",
                    "claim_strength": gap_cfg["gap_types"]["coverage_gap"]["claim_strength"],
                    "evidence_record_ids": sorted(rid for rid in by_id if t in assignment.get(rid, [])
                                                  and year_view[rid]["primary"]
                                                  and not (periods[pids.index(pid)]["start"] <= year_view[rid]["primary"] <= periods[pids.index(pid)]["end"])),
                    "retrieval_scope": "本池内题录集合 + 主题词表（" + str(gap_cfg.get("_taxonomy_source", "rules/topic_taxonomy.json")) + "）",
                    "exclusions": "缺失年份题录已剔除（见 temporal.year_missing）；未命名词表的题录不计入",
                    "upgrade_requires": gap_cfg["gap_types"]["coverage_gap"]["upgrade_requires"],
                    "needs_review": True,
                })

    # C. 反复提出但未收敛的问题
    markers = [m.lower() for m in gap_cfg.get("problem_markers", [])]
    min_marker = int(gap_cfg.get("gap_types", {}).get("unresolved_problem", {}).get("min_marker_records", 2))
    no_abs = []
    for t in topics:
        hits = []
        for rid, ts in assignment.items():
            if t not in ts:
                continue
            ab = by_id[rid]["abstract"]
            if not ab:
                no_abs.append(rid)
                continue
            abn = norm(ab)
            ms = [m for m in markers if m in abn]
            if ms:
                hits.append({"record_id": rid, "markers": sorted(set(ms)),
                             "quote": snippet(ab, max(0, abn.find(ms[0])), max(0, abn.find(ms[0])) + 90)})
        if len(hits) >= min_marker:
            gaps.append({
                "type": "unresolved_problem",
                "topic": t,
                "claim": f"主题 {t} 下有 {len(hits)} 篇题录在摘要中出现未解问题表述（如 {', '.join(sorted({m for h in hits for m in h['markers']}))}）",
                "claim_strength": gap_cfg["gap_types"]["unresolved_problem"]["claim_strength"],
                "evidence_record_ids": sorted(h["record_id"] for h in hits),
                "quotes": sorted(hits, key=lambda x: x["record_id"]),
                "retrieval_scope": "仅用摘要文本；未读全文，未做语义判定",
                "exclusions": f"缺摘要题录 {len(set(no_abs))} 条未参与判定（记 R_NO_ABS）",
                "upgrade_requires": gap_cfg["gap_types"]["unresolved_problem"]["upgrade_requires"],
                "needs_review": True,
            })

    # D. 对象/尺度/工况维度缺口
    axes = gap_cfg.get("scope_axes", {})
    total = len(records) or 1
    for axis, cfg in sorted(axes.items()):
        terms = [str(t) for t in cfg.get("terms", [])]
        found = {}
        for term in terms:
            tn = norm(term)
            ids = [r["record_id"] for r in records if tn and _key_hit(tn, norm(r["title"] + " " + r["abstract"] + " " + " ".join(r["keywords"])))]
            if ids:
                found[term] = ids
        max_share = rounded(max((len(v) / total for v in found.values()), default=0.0))
        if max_share < float(gap_cfg["gap_types"]["scope_gap"].get("min_absent_axis_share", 0.05)):
            gaps.append({
                "type": "scope_gap",
                "axis": axis,
                "axis_label": cfg.get("label", axis),
                "claim": f"{cfg.get('label', axis)} 维度在本池题录中近乎缺席（最高单术语覆盖率 {max_share}）",
                "claim_strength": gap_cfg["gap_types"]["scope_gap"]["claim_strength"],
                "evidence_record_ids": sorted({rid for v in found.values() for rid in v}),
                "terms_checked": terms,
                "terms_hit": sorted(found),
                "retrieval_scope": "题名+摘要+关键词的字符串覆盖统计",
                "exclusions": "题录未覆盖该轴时，无法区分『领域没做』与『本次检索式没写』",
                "upgrade_requires": gap_cfg["gap_types"]["scope_gap"]["upgrade_requires"],
                "needs_review": True,
            })

    type_keys = ("retrieval_gap", "coverage_gap", "unresolved_problem", "scope_gap")
    return {
        "gaps": gaps,
        "counts_by_type": {k: sum(1 for g in gaps if g["type"] == k) for k in type_keys},
        "abstain": "所有 gap 均为『证据/检索缺口』记录，不是领域空白结论；升级为结论需要 upgrade_requires 中列出的补充动作",
        "forbidden_phrase_check": check_forbidden(gaps, gap_cfg),
    }


def check_forbidden(gaps: list, gap_cfg: dict) -> dict:
    phrases = [p.lower() for p in gap_cfg.get("reporting", {}).get("forbid_phrases", [])]
    hits = []
    for g in gaps:
        blob = json.dumps(g, ensure_ascii=False).lower()
        for p in phrases:
            if p.lower() in blob:
                hits.append({"gap_claim": g.get("claim", ""), "phrase": p})
    return {"checked": True, "hits": hits, "clean": not hits}


# --------------------------------------------------------------------------------------
# 12. 契约绑定
# --------------------------------------------------------------------------------------
def route_contract(res: Result, payload: dict, routing: dict) -> list:
    routed = []
    for key in list(res.contract.keys()):
        if key in payload:
            res.set(key, payload[key])
            routed.append({"contract_key": key, "source": key, "how": "exact"})
            continue
        kn = norm(key)
        hit = None
        for rule in routing.get("rules", []):
            if any(norm(m) in kn for m in rule.get("match", [])):
                target = rule.get("target")
                if target in payload:
                    hit = ("payload", target)
                elif target in ("evidence", "uncertainty", "status"):
                    hit = ("virtual", target)
                if hit:
                    break
        if not hit:
            continue
        kind, target = hit
        if kind == "payload":
            res.set(key, payload[target])
        elif target == "evidence":
            res.set(key, res.evidence_view())
        elif target == "uncertainty":
            res.set(key, res.uncertainty)
        elif target == "status":
            res.set(key, res.status)
        routed.append({"contract_key": key, "source": target, "how": "routing"})
    return routed


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    case = normalize_case(case)
    rules = load_rules()
    stats, rep_cfg, gap_cfg, codes = rules["stats"], rules["rep"], rules["gap"], rules["codes"]
    direction_rules = rules["direction"]
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res.contract))
    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).parent
    bases = path_bases(work, case_dir)

    topic_text = str(first_present(case, "topic", "research_topic", "question", "task_instruction", default="")).strip()
    if not topic_text and task_text:
        topic_text = "\n".join(l for l in task_text.splitlines() if l.strip() and not l.startswith("#"))[:800]
    res.log("topic", chars=len(topic_text))

    sources = collect_sources(case, bases)
    records = collect_records(case, bases)
    res.log("intake", sources=len(sources), records_raw=len(records))
    if not records:
        res.set("研究产物", {"temporal_trends": None, "topic_evolution": None, "frontier_trends": None,
                           "keyword_heat": None, "representative_works": None, "research_gaps": None})
        res.status["overall"] = "empty"
        res.unmet("题录集合", "case.json 未提供任何题录（records/candidates/corpus_path/materials 均为空）")
        res.unsure("趋势结论", "无题录时不做任何年度分布、趋势或前沿推断")
        res.note("空结果不等于领域没有研究；补齐题录导出（CSV/JSON）后重跑")
        res.evidence["scope"] = {"pool_records": 0, "note": "未联网，未使用任何外部检索"}
        return res

    # ---- 摄入 → 去重 → 前瞻协议筛选 ----
    dropped = []
    deduped = dedupe(records, dropped, codes)
    protocol_block = build_protocol_block(case, rules["screening"])
    screening_rows, screen_counts = screen_records(deduped, protocol_block)
    included_ids = {r["record_id"] for r in screening_rows if r["outcome"] == "included"}
    pool = [r for r in deduped if r["record_id"] in included_ids]
    res.log("screen", protocol=protocol_block["protocol_status"], **screen_counts)

    # ---- 年份口径与时间趋势 ----
    window = resolve_window(case, pool, stats)
    year_view = build_year_view(pool, stats.get("year_policy", {}))
    temporal = temporal_analysis(pool, year_view, window, stats, codes, dropped)
    buckets = build_buckets(window, stats)
    periods = make_periods(window["start"] if window["start"] is not None else 0,
                           window["end"] if window["end"] is not None else -1,
                           int(first_present(case, "period_count", "periods", default=rep_cfg.get("period_assignment", {}).get("period_count_default", 3)) or 3))

    # ---- 主题归并 / 演化 / 逐字证据 / 冲突 ----
    tax = get_taxonomy(case, rules)
    assignment = assign_topics(pool, tax)
    evolution = topic_evolution(pool, assignment, year_view, temporal, periods, stats, codes)
    evolution["taxonomy_source"] = tax["source"]
    evolution["taxonomy_topics"] = sorted(tax["topics"])
    evidence_rows, rows_by_topic = build_topic_evidence(pool, assignment, tax, direction_rules, stats)
    conflicts = detect_topic_conflicts(evidence_rows, direction_rules)

    # ---- 趋势证据桶与前沿判定 / 词频审计 ----
    claims = build_trend_claims(pool, assignment, year_view, buckets, rows_by_topic, conflicts, tax, stats, rep_cfg, codes)
    heat = keyword_heat_audit(pool, tax, stats, buckets, year_view)

    # ---- 子问题覆盖与空白 ----
    subquestions = build_subquestions(case, topic_text)
    sub_ev = subquestion_coverage(subquestions, pool)
    representatives = select_representatives(pool, assignment, year_view, periods, rep_cfg, codes)
    gap_cfg["_taxonomy_source"] = tax["source"]
    gaps = research_gaps(pool, assignment, year_view, periods, gap_cfg, stats, codes, evolution["matrix"],
                         sub_ev, screen_counts)
    res.log("analytics", pool=len(pool), dropped=len(dropped), periods=len(periods), buckets=len(buckets),
            topics=len(tax["topics"]), claims=len(claims), evidence_rows=len(evidence_rows),
            conflicts=len(conflicts), gaps=len(gaps["gaps"]))

    # ---- 快照范围（coverage 声明语料边界） ----
    snapshot_scope = {
        "sources_total": len(sources),
        "records_raw": len(records),
        "after_dedupe": len(deduped),
        "screened_in": screen_counts["included"],
        "screened_out": screen_counts["excluded"],
        "unscored_no_body": screen_counts["unscored_no_body"],
        "window": {"start": window["start"], "end": window["end"], "source": window["source"]},
        "buckets": [b["label"] for b in buckets],
        "claim_scope": stats.get("trend_verdict", {}).get("claim_scope", "pool_snapshot"),
        "note": "本报告全部结论只描述本 case 池内快照（去重 + 前瞻协议筛选后的记录集合）；"
                "未联网、未调用外部数据库，不声称池外覆盖，不构成领域全局趋势/前沿/空白结论。",
    }

    # ---- 证据矩阵：每条主张 → 分桶计数 / record_ids / 逐字代表 / 可复算口径 ----
    trend_claims = []
    series = temporal["primary_series"]
    if series and any(x["count"] for x in series):
        trend_claims.append({
            "claim": f"{window['start']}-{window['end']} 年产出量从 {series[0]['count']} 篇变化到 {series[-1]['count']} 篇；"
                     f"CAGR={temporal['cagr']}",
            "record_ids_by_year": temporal["per_year_record_ids"],
            "recompute": "按 primary_series 求和；CAGR=(末/首)^(1/年差)-1，参数见 rules/statistics.json.growth",
        })
    for c in temporal["inflection"]["accepted"]:
        trend_claims.append({
            "claim": f"断点年 {c['breakpoint_year']} 判为{c['label']}（加速度 {c['acceleration']}，两侧水平均值相对变化 {c['relative_change']}）",
            "record_ids_by_year": temporal["per_year_record_ids"],
            "recompute": c["basis"],
        })
    topic_claims = []
    for c in claims:
        topic_claims.append({
            "claim": c["claim"], "verdict": c["verdict"], "topic": c["topic"],
            "bucket_counts": c["bucket_stats"]["counts"],
            "record_ids": c["record_ids"],
            "representative_record_ids": [r["record_id"] for r in c["representative_records"]],
            "recompute": c["basis"],
        })
    rep_claims = [{"claim": f"{r['period']} 代表作：{r['title']}", "record_ids": [r["record_id"]],
                   "criteria": r["criteria"], "basis": r["selection_rationale"]} for r in representatives["representatives"]]
    gap_claims = [{"claim": g["claim"], "record_ids": g.get("evidence_record_ids", []),
                   "type": g["type"], "needs_review": g.get("needs_review", True)} for g in gaps["gaps"]]
    conflict_claims = [{"claim": f"{c['topic']} 存在 {c['type']}（{c['conflict_id']}）",
                        "record_ids": [c["side_a"]["record_id"], c["side_b"]["record_id"]],
                        "resolution": c["resolution"]} for c in conflicts]
    evidence_matrix = {
        "trend_claims": trend_claims,
        "topic_claims": topic_claims,
        "representative_claims": rep_claims,
        "gap_claims": gap_claims,
        "conflict_claims": conflict_claims,
        "locator_format": "{record_id} 直连 case 题录条目；逐字引文按 evidence_table 的 source_file + char_span 复核；"
                          "统计量按 rules/statistics.json 参数复算",
        "note": "本题录集合外的证据（如政策文件、产业事件）不在矩阵内，相关因果表述一律 needs_review",
    }

    policy = {
        "统计窗口": f"{window['start']}-{window['end']}（来源：{window['source']}）",
        "计数单位": temporal["counting_unit"],
        "年份口径": f"主序列={temporal['primary_series_name']}，并列报告 online/preprint 序列；"
                    f"缺失 {temporal['year_missing']['count']} 条（{temporal['year_missing']['rate']}），"
                    f"回填 {temporal['year_imputed']['count']} 条（{temporal['year_imputed']['rate']}）",
        "去重口径": "DOI 归一优先、标题强归一兜底；同组保留信息最全且非预印本者",
        "前瞻协议": f"{protocol_block['protocol_status']}；仅 included 记录（{screen_counts['included']} 条）进入趋势统计池，"
                    f"筛除 {screen_counts['excluded']} 条、正文不可读 {screen_counts['unscored_no_body']} 条",
        "证据桶": f"桶宽 {stats.get('trend_buckets', {}).get('bucket_width_years')} 年，共 {len(buckets)} 桶 "
                  f"（{', '.join(b['label'] for b in buckets)}）；每条趋势主张带逐桶计数与代表记录",
        "主题归并": f"词表来源 {tax['source']}，共 {len(tax['topics'])} 个主题，未命中率 {evolution['untagged']['rate']}",
        "占比分母": "各阶段已成功打标签的题录数（未命中词表者不入分母）",
        "拐点判据": "滑动均值二阶差分 + 两侧均值相对变化 ≥ 阈值才判为拐点，否则按离散波动处理",
        "前沿判据": "rising 且近桶新起且近桶计数高于既有主题基数（中位）才判前沿候选；全部为池内相对增量",
        "词频口径": "词频热度只作对照审计；趋势主张一律按记录级证据桶计数，不以 raw_hits 为据",
        "代表作口径": "首创（不看被引）+ 高被引 + 主题中心三口径并置；趋势主张另按 trend_evidence 口径携带逐字代表记录",
        "空白判据": "零证据/弱证据子问题（检索缺口）/ 覆盖缺口 / 未收敛问题 / 维度缺口，均带检索范围与升级条件，"
                    "不与『领域空白』等同",
        "缺失处理": "年份缺失与未命中词表逐条记账，不静默丢弃",
    }

    deliverable = {
        "topic": topic_text[:200],
        "temporal_trends": temporal,
        "frontier_trends": None,  # 下面回填
        "topic_evolution": evolution,
        "keyword_heat": None,  # 下面回填
        "representative_works": representatives,
        "research_gaps": gaps,
        "snapshot_scope": snapshot_scope,
        "statistics_policy": policy,
    }

    # ---- 状态与不确定性 ----
    thr = stats.get("thresholds", {})
    if protocol_block["input_gap"]:
        res.unmet("筛选协议", f"{protocol_block['input_gap']['code']} — {protocol_block['input_gap']['detail']}；"
                              f"已按声明回退口径筛选（rules/screening_rules.json#fallback_screening），"
                              f"不冒充已按协议筛选")
    if screen_counts["unscored_no_body"]:
        ids = sorted(r["record_id"] for r in screening_rows if r["outcome"] == "unscored_no_body")
        res.unmet("记录正文", f"{len(ids)} 条记录正文不可读（{','.join(ids)}），已按 unscored_no_body 诚实降级，"
                              f"不参与纳排与趋势统计，未编造其内容")
    mismatched = [r for r in evidence_rows if not r["quote_verified"]]
    if mismatched:
        res.unmet("证据引文校验", f"{len(mismatched)} 条证据 quote 未通过精确子串校验，已在行内标 quote_mismatch，不静默")
    if temporal["year_missing"]["rate"] > float(stats.get("year_policy", {}).get("max_imputed_rate_for_trend_claim", 0.25)):
        res.unmet("时间趋势", f"年份缺失率 {temporal['year_missing']['rate']} 超过阈值，趋势仅作观察性描述")
        res.unsure("年度分布", "缺失年份题录已剔除，产出量可能被低估；不外推年度总量")
    if evolution["untagged"]["rate"] >= float(thr.get("untagged_rate_alert", 0.3)):
        res.unmet("主题演化", f"未命中词表比例 {evolution['untagged']['rate']} 过高，新兴主题可能被词表截断")
        res.unsure("主题集合", "未命名主题未聚类，建议扩词表或用案例自带 topic_taxonomy")
    if len(pool) < int(thr.get("min_records_for_trend_claim", 10)):
        res.note(f"统计池仅 {len(pool)} 条，低于 {thr.get('min_records_for_trend_claim')} 条，趋势结论按小样本观察性描述处理")
        res.unsure("趋势稳健性", "小样本下 CAGR 与拐点对单条题录敏感，需扩大题录后再判定")
    if not representatives["citations_available"]:
        res.unmet("代表作-高被引口径", "题录未提供引用数（citations 字段），该口径不可用，未以任何估计值代替")
    for g in representatives["gaps"]:
        res.unmet(f"代表作覆盖:{g['period']}", g["reason"])
    if gaps["forbidden_phrase_check"]["hits"]:
        res.unsure("空白表述", f"输出触发禁用越界表述 {gaps['forbidden_phrase_check']['hits']}，需改写为证据缺口")
    if gap_cfg.get("reporting", {}).get("must_include_fields"):
        missing_fields = [f for f in gap_cfg["reporting"]["must_include_fields"]
                          if any(f not in g for g in gaps["gaps"])]
        if missing_fields:
            res.unsure("空白条目字段完整性", f"部分 gap 缺字段 {missing_fields}，已按类型差异保留")
    for row in sub_ev:
        if row["level"] == "zero":
            res.unmet(f"子问题缺口:{row['subquestion_id']}", "本池内零达阈值证据（检索缺口），已记入 research_gaps（retrieval_gap）")
        elif row["level"] == "weak":
            res.note(f"子问题 {row['subquestion_id']} 仅有 1 条弱关联记录，不足以支撑结论（R_WEAK_EVIDENCE）")
    for c in claims:
        if c["verdict"] == "thin":
            extra = "；含缺年份记录" if c["bucket_stats"]["records_without_year"] else ""
            res.note(f"主题 {c['topic']} 证据太薄（池内 {len(c['record_ids'])} 条{extra}）："
                     f"只报计数，不写趋势句（R_EVIDENCE_THIN）")
        if c["verdict"] == "no_year_data":
            res.unmet(f"时间趋势:{c['topic']}", "该主题记录年份字段全缺：不做时间趋势，只报池内计数并声明（R_YEAR_MISSING）")
        if (c.get("frontier") or {}).get("is_frontier_candidate"):
            res.note(f"前沿候选 {c['topic']}：近桶新起且基数对比明确（{c['frontier']['near_bucket']['label']} "
                     f"{c['frontier']['near_bucket']['count']} vs 中位 {c['frontier']['base_comparison']['base_value']}）")
        elif c["verdict"] == "rising":
            res.unsure(f"前沿:{c['topic']}", f"rising 但不判前沿候选：{c['frontier'].get('reason_if_not', '')}；"
                                             f"前沿=池内相对增量，不做领域新兴度断言")
    for c in conflicts:
        res.note(f"主题 {c['topic']} 存在{c['type']}，已双列呈现（{c['side_a']['evidence_id']} × "
                 f"{c['side_b']['evidence_id']}，conflict_id={c['conflict_id']}）")
        res.unsure(f"冲突:{c['conflict_id']}", f"{c['detail']}；双方口径差异未仲裁，以双列并列交付")
    for r in heat["rows"]:
        if r["heat_collapse"]:
            res.unsure(f"词频热度:{r['keyword']}", r["collapse_reason"] or "词频热度坍缩")
    for row in evidence_rows:
        if (row.get("direction") or {}).get("value") == "mixed":
            res.unsure(f"方向:mixed:{row['evidence_id']}", "同一句内同时含正负方向表述，已标 mixed，不参与成对冲突判定")
    if not buckets:
        res.unsure("证据桶", "池内无任何可用年份或窗口未定，未生成证据桶；全部主题只报池内计数")
    res.unsure("因果归因", "本 skill 不联网、不读政策与产业事件，演化『因何变化』仅给题录共现证据，未做因果归因")
    res.unsure("术语漂移", "词表按词面命中（CJK 子串 / ASCII 词边界），同义改写可能漏检（记为 untagged），新兴术语可能整体缺席")
    res.unsure("年份口径差异", "在线发表年与见刊年可差 1-2 年；已分序列报告，跨库合并时口径可能不可比")
    res.unsure("快照边界", "全部结论限定池内快照：引用缺口、词表缺口、年份缺口已显式降级，不做池外推断")

    has_bucketed_claim = any(c["bucket_table"] for c in claims)
    overall = "complete"
    if len(pool) < 3 or not buckets or not has_bucketed_claim:
        overall = "partial"
        if len(pool) < 3:
            res.note(f"统计池不足 3 条，趋势分析降级为 partial")
    if temporal["year_missing"]["rate"] > float(stats.get("year_policy", {}).get("max_imputed_rate_for_trend_claim", 0.25)):
        overall = "partial"
    res.status["overall"] = overall
    res.done("题录归一与去重", "前瞻协议逐条筛选", "年份口径与序列统计", "拐点判定", "主题归并与演化事件",
             "趋势证据桶与前沿判定", "逐字证据表与冲突双列", "词频热度审计",
             "代表作多口径遴选", "研究空白判据", "证据矩阵组装")

    frontier_trends = {
        "claims": claims,
        "verdict_rules": stats.get("trend_verdict", {}).get("verdict_rules", {}),
        "bucket_rule": stats.get("trend_buckets", {}),
        "summary": {
            "topics": len(claims),
            "rising": sum(1 for c in claims if c["verdict"] == "rising"),
            "established": sum(1 for c in claims if c["verdict"] == "established"),
            "flat": sum(1 for c in claims if c["verdict"] == "flat"),
            "thin": sum(1 for c in claims if c["verdict"] == "thin"),
            "no_year_data": sum(1 for c in claims if c["verdict"] == "no_year_data"),
            "frontier_candidates": [c["topic"] for c in claims if (c.get("frontier") or {}).get("is_frontier_candidate")],
            "claim_scope": snapshot_scope["claim_scope"],
            "note": "趋势主张由证据桶（逐桶计数 + 代表记录逐字引文）支撑；前沿 = 池内相对增量（近桶新起 + 基数对比）；"
                    "thin/no_year_data 主题不写趋势句",
        },
    }
    deliverable["frontier_trends"] = frontier_trends
    deliverable["keyword_heat"] = heat

    payload = {"研究产物": deliverable, "证据矩阵": evidence_matrix,
               "evidence_table": evidence_rows,
               "temporal_trends": temporal, "topic_evolution": evolution,
               "frontier_trends": frontier_trends, "keyword_heat": heat,
               "screening": {"protocol_status": protocol_block["protocol_status"],
                             "protocol_id": protocol_block["protocol_id"],
                             "order_note": "先解析协议块（prospective），后对去重后的每条记录逐条筛选并记 criterion_hits；"
                                           "仅 included 记录进入趋势统计池",
                             "counts": screen_counts, "records": screening_rows},
               "snapshot_scope": snapshot_scope,
               "representative_works": representatives, "research_gaps": gaps,
               "statistics_policy": policy}
    res.evidence["sources_manifest"] = {"sources": sources, "count": len(sources),
                                        "note": "来源文件清单与解析结果；记录通过 source_path/source_id 指到来源文件，"
                                                "正文按 内联→侧车→自研 PDF 提取→DOCX→文本直读→诚实 missing 取数"}
    res.evidence["protocol"] = protocol_block
    res.evidence["screening"] = {"counts": screen_counts, "rule": rules["screening"].get("screening_rule", ""),
                                 "fallback": protocol_block["fallback"],
                                 "input_gap": protocol_block["input_gap"]}
    res.evidence["temporal"] = {"primary_series": temporal["primary_series"], "cagr": temporal["cagr"],
                                "inflection": temporal["inflection"]["accepted"],
                                "year_series": temporal["year_series"]}
    res.evidence["topics"] = {"matrix": evolution["matrix"], "events": evolution["events"],
                              "periods": periods, "untagged": evolution["untagged"],
                              "evidence_thin": evolution["evidence_thin"],
                              "year_data_missing": evolution["year_data_missing"]}
    res.evidence["frontier"] = {"summary": frontier_trends["summary"],
                                "verdicts": {c["topic"]: c["verdict"] for c in claims},
                                "bucket_rule": frontier_trends["bucket_rule"],
                                "verdict_rules": frontier_trends["verdict_rules"]}
    res.evidence["evidence_table_map"] = {
        "rows": len(evidence_rows),
        "per_topic": {t: len(rows_by_topic.get(t, [])) for t in sorted(rows_by_topic)},
        "quote_rule": "证据句必须是记录正文/摘要字段的逐字整句；source_file + char_span（按 quote_field 字段内字符区间）"
                      "+ 精确子串校验；二进制来源（PDF/DOCX）以 body_sha256 锚定提取文本；题名不进引文",
    }
    res.evidence["conflicts"] = {"rule": rules["direction"].get("pair_rules", {}).get("resolution", ""),
                                 "items": conflicts}
    res.evidence["keyword_heat_summary"] = heat["summary"]
    res.evidence["representatives"] = representatives["representatives"]
    res.evidence["gaps"] = gaps["gaps"]
    res.evidence["exclusions"] = dropped[:300]
    res.evidence["subquestions"] = {"source": subquestions[0]["source"] if subquestions else "",
                                    "coverage": sub_ev}
    res.evidence["rules"] = {"statistics": stats, "representative": rep_cfg.get("criteria", {}),
                             "gap_types": list(gap_cfg.get("gap_types", {})),
                             "screening": rules["screening"], "direction": rules["direction"]}
    res.evidence["scope"] = {
        "pool_records": len(records),
        "kept_after_dedupe": len(deduped),
        "in_window": temporal["total_in_window"],
        "periods": len(periods),
        "snapshot_scope": snapshot_scope,
        "note": "仅在本 case 池内（去重 + 前瞻协议筛选后）做统计；未联网、未调用外部数据库，未声称池外覆盖",
    }
    routed = route_contract(res, payload, rules["routing"])
    for k, v in payload.items():
        if k not in res.fields:
            res.set(k, v)
    res.evidence["contract_routing"] = routed
    for key in res.contract:
        if key not in res.fields:
            res.unmet(key, "该合同字段名未命中 rules/contract_routing.json，未以推测值填充")
    return res


def _write_input_error(args, exc: Exception) -> int:
    payload = {
        "case_id": "UNKNOWN-CASE",
        "skill": {"name": SKILL, "version": "0.1.0"},
        "研究产物": {"status": "not_produced", "reason": str(exc)},
        "evidence": {"error": f"{type(exc).__name__}: {exc}"},
        "status": {"overall": "input_error", "completed": [],
                   "unmet": [{"item": "case.json", "reason": str(exc)}],
                   "notes": ["输入不可读时立即失败并保留错误原文，不产出伪造结果"]},
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
        screen = payload.get("screening", {}).get("counts", {})
        print(json.dumps({"case_id": payload["case_id"], "status": payload["status"]["overall"],
                          "screened": screen.get("screened", 0), "included": screen.get("included", 0),
                          "claims": len(payload.get("frontier_trends", {}).get("claims", []))},
                         ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    try:
        from render_report import render_file
        render_file(path, Path(args.out) / "report.md")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"report_warning": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"]}, ensure_ascii=False))
    try:
        _extra = _synthesis_hooks(args, path, payload)
        if _extra:
            print(json.dumps({"synthesis": _extra}, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
