#!/usr/bin/env python3
"""011 专利与技术文献检索 —— 主流程。

输入：/input/case.json（号码型/主题型查询 + 有限专利记录语料 + USPTO 影印件 PDF/OCR 侧车 + 技术对比文献）+ task.md
输出：/app/output/result.json（号码规范化与字段表 / 族关系 / 权利要求来源定位 / 法律状态 / 检索式 / 材料正文命中 / 状态说明）

流程（每步都能按 rules/ 复算）：
  1. 读查询与记录，抽号码、主题与申请人三类检索意图
  2. 读材料正文：影印件优先吃侧车文本（<pdf>.ocr.txt / <pdf>.txt / <stem>.ocr.txt / <stem>.txt），
     其次用 pdf_min（zlib + BT/ET/Tj 流扫描）做 stdlib 文本提取；逐文件记 SHA-256、
     extraction_method 与字符数；提取失败如实标 gap，不编文本
  3. 号码规范化：国家码 + 纯数字 + 种类码 → canonical；位数不足/缺种类码标 ambiguous；
     WO 号支持斜杠变体与 OCR 合并（丢斜杠丢空格）的拆解候选与 alternatives
  4. 检索式构造：号码型 PN/AN/PR，主题型 IPC/CPC + 关键词；主题型/申请人型另对材料正文
     做关键词命中 + 原文短引（quote + char_span），与 records 打分分开呈现
  5. 族归并：优先权号归一为第一判据，申请人+标题+时间窗为辅助；一律标 scope=observed_not_exhaustive
  6. 权利要求来源定位：优先内联字段（inline_field），缺内联时落到影印件/侧车文本
     （facsimile:<file> page:N / ocr_sidecar:<file>）；quote 必须是来源文本的精确子串，
     char_span 可逐字节复核；OCR 来源一律标 ocr_boundary
  7. 法律状态：默认 unverified，只有来源分级为 official_registry 且带查询日期才允许写确定值，
     其余给回官方库复核路径
  8. 落盘 result.json（contract 字段 + evidence + status + uncertainty；除 generated_at 外逐字节稳定）
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pdf_min  # noqa: E402
from kit import (  # noqa: E402
    Clock,
    Result,
    as_list,
    clamp,
    first_present,
    jaccard,
    load_case,
    load_json,
    load_task_text,
    norm,
    norm_title,
    parse_args,
    read_text,
    resolve_table_ref,
    rounded,
    stable_id,
    tokens,
)

SKILL = "patent-literature-retrieval"
RULES = Path(__file__).resolve().parent.parent / "rules"


def load_rules() -> dict:
    return {
        "fields": load_json(RULES / "field_dictionary.json", default={}) or {},
        "topics": load_json(RULES / "ipc_topics.json", default={}) or {},
        "scoring": load_json(RULES / "scoring.json", default={}) or {},
        "facsimile": load_json(RULES / "facsimile.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 号码规范化
# --------------------------------------------------------------------------------------
CC_RE = re.compile(r"^\s*([A-Z]{2})(?=[\s\-/]|\d)")
KIND_RE = re.compile(r"[A-Z]\d?$")


def normalize_number(raw: str, cfg: dict) -> dict:
    """把任意写法的专利号拆成 国家码 / 纯数字 / 种类码，并按 rules 判定号码类型。

    覆盖 OCR/二手转载常见噪声：全角字符、千分位、任意空白、小写、尾部标点、
    斜杠变体（US 15/123,456、WO 2018/123456），以及 WO 号 OCR 合并
    （丢斜杠丢空格，如 ``WO2018123456A1``）：合并形态标 ``ambiguous`` 并给
    「年份/序号」拆解候选，不把单一拆解写成确定结论。
    """
    original = (raw or "").strip()
    s = unicodedata.normalize("NFKC", original).upper().replace("，", ",")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[.,;:]+$", "", s).strip()  # OCR 常见尾部标点
    out = {
        "raw": original,
        "country": "",
        "digits": "",
        "kind_code": "",
        "number_kind": "unknown",
        "canonical": "",
        "confidence": "low",
        "alternatives": [],
        "has_slash": "/" in s,
        "ambiguous": False,
        "wo_merged": False,
        "wo_split": "",
    }
    if not s:
        return out
    m = CC_RE.match(s)
    body = s
    if m:
        out["country"] = m.group(1)
        body = s[m.end():].strip(" -/")
    body_nospace = re.sub(r"[\s\u00a0]+", "", body)
    km = KIND_RE.search(body_nospace)
    if km and any(ch.isalpha() for ch in km.group(0)):
        out["kind_code"] = km.group(0)
        body_nospace = body_nospace[: km.start()]
    digits = re.sub(r"\D", "", body_nospace)
    out["digits"] = digits
    kinds = cfg.get("number_kinds") or {}
    grant_kinds = [str(x) for x in as_list(cfg.get("grant_kind_codes"))]
    has_slash = "/" in body or re.match(r"^\d{2}/", body or "") is not None
    out["has_slash"] = has_slash
    if has_slash and 7 <= len(digits) <= 9:
        out["number_kind"] = "application"
        out["confidence"] = "high"
    elif out["kind_code"] in grant_kinds:
        out["number_kind"] = "grant"
        out["confidence"] = "high"
    elif len(digits) in (10, 11) and digits[:4].isdigit() and 1990 <= int(digits[:4]) <= 2035:
        out["number_kind"] = "application_publication"
        out["confidence"] = "high" if out["kind_code"] in ("A1", "A2") else "medium"
    elif out["kind_code"] in ("A1", "A2") and 7 <= len(digits) <= 8:
        out["number_kind"] = "application_publication"
        out["confidence"] = "high"
    elif len(digits) in (7, 8):
        out["number_kind"] = "unknown"
        out["confidence"] = "low"
        out["ambiguous"] = True
        out["alternatives"] = [
            {"number_kind": "grant", "reason": "7~8 位号码 + 授权类种类码（如 B2）时按授权公告号解释"},
            {"number_kind": "application", "reason": "本国申请号也可能是 7~8 位，需回官方登记确认"},
        ]
    if digits:
        out["canonical"] = f"{out['country']}{digits}{out['kind_code']}".strip()
    if out["country"] == "WO":
        if has_slash:
            # body_nospace 在种类码提取时已剥掉 A1/B2 等后缀
            out["wo_split"] = body_nospace
        elif digits and len(digits) >= 9:
            # WO 合并边界：标准写法是 WO YYYY/NNNNNN，OCR 常把斜杠与空格一起丢掉。
            # 归一仍给 canonical（便于跨写法命中同一记录），但拆解点必须显式挂出来待确认。
            out["wo_merged"] = True
            out["ambiguous"] = True
            splits = []
            year, serial = digits[:4], digits[4:]
            if year.isdigit() and 1990 <= int(year) <= 2035:
                splits.append(
                    {
                        "number_kind": "application_publication",
                        "wo_split": f"{year}/{serial}",
                        "reason": "OCR 合并丢斜杠：按「4 位年份 + 序号」拆解（标准 WO 布局），需回 WIPO/受理局登记确认",
                    }
                )
                if len(serial) != 6:
                    splits.append(
                        {
                            "number_kind": "unknown",
                            "wo_split": "",
                            "reason": f"合并后的 {len(digits)} 位数字按年份拆出的序号为 {len(serial)} 位（标准为 6 位），拆分点存疑，需登记库确认",
                        }
                    )
            if splits:
                out["number_kind"] = "application_publication" if splits[0]["number_kind"] == "application_publication" else "unknown"
                out["confidence"] = "medium" if out["number_kind"] == "application_publication" else "low"
                out["wo_split"] = splits[0]["wo_split"]
            else:
                out["number_kind"] = "unknown"
                out["confidence"] = "low"
                splits.append(
                    {
                        "number_kind": "unknown",
                        "wo_split": "",
                        "reason": f"合并后的 {len(digits)} 位数字无法按年份前缀拆解，拆分点不明，需登记库确认",
                    }
                )
            seen = {json.dumps(a, ensure_ascii=False, sort_keys=True): a for a in (out["alternatives"] + splits)}
            out["alternatives"] = list(seen.values())
    _ = kinds
    return out


def detect_source_tier(rec: dict, cfg: dict) -> str:
    declared = norm(rec.get("source_tier"))
    tiers = cfg.get("source_tiers") or {}
    if declared and declared in tiers:
        return declared
    src = norm(rec.get("source_db"))
    for alias, tier in (cfg.get("source_tier_aliases") or {}).items():
        if norm(alias) and norm(alias) in src:
            return tier
    return "unknown"


# --------------------------------------------------------------------------------------
# 材料归一
# --------------------------------------------------------------------------------------
def collect_records(case: dict, work: Path, cfg: dict) -> list:
    raw = first_present(case, "records", "patents", "candidates", "documents", default=[])
    items = [x for x in as_list(raw) if isinstance(x, dict)]
    for key in ("records_path", "patents_path", "corpus_path"):
        ref = first_present(case, key)
        if isinstance(ref, str):
            p = Path(ref)
            p = p if p.is_absolute() else (work / p)
            data = load_json(p, default=None)
            if isinstance(data, list):
                items.extend(x for x in data if isinstance(x, dict))
            elif isinstance(data, dict):
                items.extend(x for x in as_list(first_present(data, "records", "patents", "items", default=[])) if isinstance(x, dict))
    out = []
    for i, it in enumerate(items):
        rid = str(first_present(it, "record_id", "id", "doc_id", default=f"P{i + 1}"))
        claims_raw = first_present(it, "claims", "claim_texts", default=None)
        out.append(
            {
                "record_id": rid,
                "_raw": it,
                "title": str(first_present(it, "title", "invention_title", default="")).strip(),
                "assignee": str(first_present(it, "assignee", "applicant", "owner", default="")).strip(),
                "inventors": [str(x) for x in as_list(first_present(it, "inventors", "inventor", default=[]))],
                "publication_raw": str(first_present(it, "publication_number", "pub_no", "publication", default="")),
                "application_raw": str(first_present(it, "application_number", "app_no", "application", default="")),
                "priority_raw": str(first_present(it, "priority_number", "priority", "priority_no", default="")),
                "priority_date": str(first_present(it, "priority_date", "earliest_priority", default="")),
                "filing_date": str(first_present(it, "filing_date", "application_date", default="")),
                "publication_date": str(first_present(it, "publication_date", "pub_date", "grant_date", default="")),
                "ipc": [str(x) for x in as_list(first_present(it, "ipc", "ipc_codes", "classification", default=[]))],
                "cpc": [str(x) for x in as_list(first_present(it, "cpc", "cpc_codes", default=[]))],
                "abstract": str(first_present(it, "abstract", "summary", default="")),
                "claims_raw": claims_raw,
                "source_db": str(first_present(it, "source_db", "source", "database", default="")),
                "queried_at": str(first_present(it, "queried_at", "query_date", "retrieved_at", default="")),
                "ocr": bool(first_present(it, "ocr", "is_ocr", "facsimile_ocr", default=False)),
                "legal_status_observed": first_present(it, "legal_status_observed", "legal_status", "status", default=None),
                "doc_type": str(first_present(it, "doc_type", "type", "kind", default="")),
                "source_tier_declared": str(first_present(it, "source_tier", default="")),
                "restricted": bool(first_present(it, "restricted", "license_restricted", default=False)),
            }
        )
        out[-1]["source_tier"] = detect_source_tier(
            {"source_db": out[-1]["source_db"], "source_tier": out[-1]["source_tier_declared"]}, cfg
        )
        out[-1]["fac_refs"] = []
    return out


# --------------------------------------------------------------------------------------
# 材料正文：影印件 PDF / OCR 侧车文本 / 技术对比文献
# --------------------------------------------------------------------------------------
_RECORD_FAC_KEYS = ("facsimiles", "facsimile", "facsimile_pdfs", "pdf", "pdfs")
_RECORD_LIT_KEYS = ("fulltext", "fulltext_path", "text_path")
_CASE_FAC_KEYS = ("facsimiles", "facsimile_pdfs")
_CASE_LIT_KEYS = ("literature", "comparison_papers", "papers", "documents")
_CASE_BOTH_KEYS = ("materials",)


def _ref_parts(item) -> tuple:
    """从 case 引用项取 (路径声明, ocr 声明, record_id, kind 声明)。字符串引用三者为空。"""
    if isinstance(item, str):
        return item, None, "", ""
    if isinstance(item, dict):
        path = ""
        for k in ("path", "file", "filepath", "location"):
            if isinstance(item.get(k), str) and item[k]:
                path = item[k]
                break
        ocr = item.get("ocr", item.get("is_ocr", None))
        rid = str(first_present(item, "record_id", "for_record", default=""))
        kind = norm(first_present(item, "kind", "doc_kind", default=""))
        return path, ocr, rid, kind
    return "", None, "", ""


def gather_materials(case: dict, records: list, base_dir, cfg: dict) -> list:
    """把 case 里声明的影印件/文献引用解析成统一的材料文档列表。

    每份材料记录：声明路径、SHA-256、提取方法（ocr_sidecar > sidecar_text > pdf_stdlib_text >
    plain_text；失败/缺失如实标注）、字符数与分页文本。影印件优先吃侧车文本，其次才用
    pdf_min 做 stdlib 提取；两者都拿不到文本时保持 text_available=false，绝不编造正文。
    """
    max_chars = int(cfg.get("max_text_chars", 200000))
    by_path, order = {}, []
    rec_by_id = {r["record_id"]: r for r in records}

    def add(ref, declared, ocr_decl, rid, kind_hint):
        if not ref:
            return
        path, _tried = resolve_table_ref(ref, str(base_dir))
        doc = by_path.get(str(path))
        if doc is None:
            doc = {
                "material_id": f"M{len(order) + 1}",
                "declared_path": declared,
                "path": str(path),
                "exists": path.is_file(),
                "kind": "",
                "linked_record_ids": [],
                "linkage": [],
                "ocr_boundary": bool(ocr_decl),
                "sha256": "",
                "extraction_method": "",
                "extraction_note": "",
                "char_count": 0,
                "page_count": 0,
                "pages": [],
                "text": "",
                "text_available": False,
            }
            by_path[str(path)] = doc
            order.append(doc)
        if rid:
            if rid not in doc["linked_record_ids"]:
                doc["linked_record_ids"].append(rid)
            doc["linkage"].append({"record_id": rid, "how": "declared"})
            if rid in rec_by_id and doc["material_id"] not in rec_by_id[rid]["fac_refs"]:
                rec_by_id[rid]["fac_refs"].append(doc["material_id"])
        if kind_hint and not doc["kind"]:
            doc["kind"] = kind_hint

    for r in records:
        raw = r.get("_raw") or {}
        for key in _RECORD_FAC_KEYS:
            for item in as_list(first_present(raw, key, default=None)):
                ref, ocr_decl, rid, kind = _ref_parts(item)
                add(ref, ref, ocr_decl if ocr_decl is not None else bool(r.get("ocr")),
                    r["record_id"], norm(kind) or "facsimile")
        for key in _RECORD_LIT_KEYS:
            for item in as_list(first_present(raw, key, default=None)):
                ref, ocr_decl, rid, kind = _ref_parts(item)
                add(ref, ref, ocr_decl, r["record_id"], norm(kind) or "literature")
    for keys, default_kind in (_CASE_FAC_KEYS, "facsimile"), (_CASE_LIT_KEYS, "literature"), (_CASE_BOTH_KEYS, ""):
        for key in keys:
            for item in as_list(first_present(case, key, default=None)):
                ref, ocr_decl, rid, kind = _ref_parts(item)
                hint = norm(kind)
                if not hint:
                    hint = default_kind
                add(ref, ref, ocr_decl, rid, hint)

    # 未显式挂接的影印件按「号码数字出现在文件名」回填；只做提示性挂接并显式声明 how。
    for doc in order:
        if doc["kind"] == "facsimile" and not doc["linked_record_ids"]:
            stem = Path(doc["path"]).stem.upper()
            for r in records:
                digits = (r.get("pub_norm") or {}).get("digits") or ""
                if len(digits) >= 6 and digits in stem:
                    doc["linked_record_ids"].append(r["record_id"])
                    doc["linkage"].append({"record_id": r["record_id"], "how": "filename_match"})
                    if doc["material_id"] not in r["fac_refs"]:
                        r["fac_refs"].append(doc["material_id"])
                    break

    for doc in order:
        if not doc["exists"]:
            doc["extraction_method"] = "missing"
            doc["extraction_note"] = "文件不存在，未读取，不编造文本"
            continue
        path = Path(doc["path"])
        suffix = path.suffix.lower()
        try:
            doc["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:  # noqa: PERF203
            doc["extraction_method"] = "failed"
            doc["extraction_note"] = f"文件不可读：{exc}"
            continue
        if suffix == ".pdf":
            sidecar, method = "", ""
            for cand, mname in (
                (Path(str(path) + ".ocr.txt"), "ocr_sidecar"),
                (Path(str(path) + ".txt"), "sidecar_text"),
                (path.with_suffix(".ocr.txt"), "ocr_sidecar"),
                (path.with_suffix(".txt"), "sidecar_text"),
            ):
                if cand.is_file():
                    sidecar, method = cand, mname
                    break
            if sidecar:
                text = read_text(sidecar)[:max_chars]
                doc["text"], doc["char_count"] = text, len(text)
                doc["pages"] = [text] if text else []
                doc["text_available"] = bool(text)
                doc["extraction_method"] = method
                doc["extraction_note"] = f"优先侧车文本 {sidecar.name}；未解析 PDF 本体"
                if method == "ocr_sidecar":
                    doc["ocr_boundary"] = True
            else:
                ext = pdf_min.extract_pdf_text(path)
                if ext["ok"]:
                    pages = [p[:max_chars] for p in ext["pages"]]
                    text = "\n".join(pages)[:max_chars]
                    doc["text"], doc["char_count"] = text, len(text)
                    doc["pages"] = pages
                    doc["page_count"] = len(pages)
                    doc["text_available"] = bool(text)
                    doc["extraction_method"] = "pdf_stdlib_text"
                    doc["extraction_note"] = "pdf_min 流扫描提取（zlib + BT/ET/Tj），非通用 PDF 解析器"
                else:
                    doc["extraction_method"] = "failed"
                    doc["extraction_note"] = f"PDF 未抽出文本：{ext['reason']}；扫描件/编码不受支持时如实记缺口"
        else:
            text = read_text(path)[:max_chars]
            method = "ocr_sidecar" if ".ocr.txt" in path.name.lower() else "plain_text"
            if method == "ocr_sidecar":
                doc["ocr_boundary"] = True
            doc["text"], doc["char_count"] = text, len(text)
            doc["pages"] = [text] if text else []
            doc["text_available"] = bool(text)
            doc["extraction_method"] = method
        if not doc["kind"]:
            doc["kind"] = "facsimile" if suffix == ".pdf" else "literature"
    return order


def material_public_view(doc: dict) -> dict:
    """结果里对外暴露的材料视图：带哈希与提取方法，但不内联正文全文（正文只以 quote+span 引用）。"""
    return {
        "material_id": doc["material_id"],
        "file": doc["declared_path"],
        "kind": doc["kind"],
        "linked_record_ids": list(doc["linked_record_ids"]),
        "linkage": doc["linkage"],
        "ocr_boundary": doc["ocr_boundary"],
        "sha256": doc["sha256"],
        "extraction_method": doc["extraction_method"],
        "extraction_note": doc["extraction_note"],
        "char_count": doc["char_count"],
        "page_count": doc["page_count"] or (1 if doc["text_available"] else 0),
        "text_available": doc["text_available"],
    }


def parse_claims(rec: dict) -> list:
    raw = rec.get("claims_raw")
    claims = []
    if isinstance(raw, list):
        for i, c in enumerate(raw, start=1):
            if isinstance(c, dict):
                claims.append(
                    {
                        "claim_no": str(first_present(c, "claim_no", "no", "number", default=i)),
                        "version": str(first_present(c, "version", "claim_version", default="")),
                        "text": str(first_present(c, "text", "claim", "content", default="")),
                    }
                )
            else:
                claims.append({"claim_no": str(i), "version": "", "text": str(c)})
    elif isinstance(raw, str) and raw.strip():
        parts = re.split(r"(?m)^\s*(\d{1,3})[\.\)、]\s+", raw)
        if len(parts) > 1:
            for i in range(1, len(parts), 2):
                claims.append({"claim_no": parts[i], "version": "", "text": parts[i + 1].strip()})
        else:
            claims.append({"claim_no": "1", "version": "", "text": raw.strip()})
    return claims


def _inline_full_text(rec: dict) -> str:
    raw = rec.get("claims_raw")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "\n".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in raw)
    return ""


def locate_inline_claims(rec: dict, claims: list, quote_chars: int, materials_by_id: dict) -> list:
    """内联权项定位：source_ref=inline_field；quote 是来源文本的精确切片（无省略号），
    char_span 在内联拼接文本内逐字节可复核；挂接的影印件文本若含同一权项则记 corroborated。"""
    full = _inline_full_text(rec)
    out = []
    for c in claims:
        text = c["text"]
        pos = full.find(text) if text and full else -1
        corrob = []
        collapsed = norm(text)
        for mid in rec.get("fac_refs") or []:
            doc = materials_by_id.get(mid) or {}
            if doc.get("text_available") and collapsed and collapsed in norm(doc.get("text", "")):
                corrob.append({"file": doc["declared_path"], "ocr_boundary": doc["ocr_boundary"]})
        if pos < 0:
            out.append(
                {
                    "claim_no": c["claim_no"],
                    "version": c["version"] or "as provided",
                    "text": text,
                    "located": False,
                    "source_ref": "inline_field",
                    "page": None,
                    "char_span": [-1, -1],
                    "quote": "",
                    "ocr_boundary": bool(rec.get("ocr")),
                    "facsimile_corroborated": bool(corrob),
                    "corroborating_files": corrob,
                    "status": "R_CLAIM_NOT_LOCATED",
                }
            )
            continue
        quote = full[pos : pos + quote_chars] if 0 < quote_chars < len(text) else full[pos : pos + len(text)]
        out.append(
            {
                "claim_no": c["claim_no"],
                "version": c["version"] or "as provided",
                "text": text,
                "located": True,
                "source_ref": "inline_field",
                "page": None,
                "char_span": [pos, pos + len(quote)],
                "quote": quote,
                "ocr_boundary": bool(rec.get("ocr")),
                "facsimile_corroborated": bool(corrob),
                "corroborating_files": corrob,
                "status": "located",
            }
        )
    return out


_CLAIM_ANCHOR_RE = re.compile(r"(?m)^(\s*)(\d{1,3})\s*\.\s+")


def extract_claims_from_text(pages: list, kind_code: str, quote_chars: int) -> list:
    """从影印件/侧车文本解析权项：只认「行首 N. 」字面锚点，取从 1 开始连续 +1 的段落串。

    找不到这样的串就返回空列表（调用方记 gap），不用标题/摘要冒充权项；
    quote 与 char_span 都取自该页原文的原始切片，未做任何空白归一。
    """
    claims = []
    for page_no, page in enumerate(pages, start=1):
        anchors = [(m.start(2), m.end(), m.group(2)) for m in _CLAIM_ANCHOR_RE.finditer(page)]
        runs, cur = [], []
        for pos in anchors:
            no = int(pos[2])
            if not cur:
                cur = [pos] if no == 1 else []
            elif no == int(cur[-1][2]) + 1:
                cur.append(pos)
            else:
                if cur:
                    runs.append(cur)
                cur = [pos] if no == 1 else []
        if cur:
            runs.append(cur)
        page_claims = []
        for run in runs:
            if not run or run[0][2] != "1":
                continue
            for i, (num_start, text_start, no) in enumerate(run):
                end = run[i + 1][1] if i + 1 < len(run) else len(page)
                region = page[text_start:end]
                if not region.strip():
                    continue
                quote = region[:quote_chars] if 0 < quote_chars < len(region) else region
                page_claims.append(
                    {
                        "claim_no": no,
                        "version": f"{kind_code} (facsimile)" if kind_code else "facsimile (kind code not stated)",
                        "text": region,
                        "quote": quote,
                        "char_span": [text_start, text_start + len(quote)],
                        "page": page_no,
                    }
                )
            break  # 只取第一段从 1 连续排下来的权项（一份影印件只有一段 claims）
        if page_claims:
            claims = page_claims
            break
    return claims


def build_claim_view(rec: dict, materials_by_id: dict, scoring: dict) -> tuple:
    """权利要求来源定位：内联优先，缺内联时落到挂接的影印件/侧车文本。

    返回 (entries, summary)。每条 entry 带 source_ref（inline_field / facsimile:<file> /
    ocr_sidecar:<file>）、page、精确 char_span 与 quote；OCR 来源一律 ocr_boundary=true。
    """
    quote_chars = int((scoring.get("thresholds") or {}).get("claims_quote_chars", 200))
    kind_code = (rec.get("pub_norm") or {}).get("kind_code", "")
    claims = parse_claims(rec)
    entries = []
    if claims:
        entries = locate_inline_claims(rec, claims, quote_chars, materials_by_id)
    else:
        for mid in rec.get("fac_refs") or []:
            doc = materials_by_id.get(mid) or {}
            if not doc.get("text_available"):
                continue
            found = extract_claims_from_text(doc.get("pages") or [], kind_code, quote_chars)
            if not found:
                continue
            if doc.get("extraction_method") == "ocr_sidecar":
                source_ref = f"ocr_sidecar:{doc['declared_path']}"
            else:
                source_ref = f"facsimile:{doc['declared_path']}"
            for f in found:
                entries.append(
                    {
                        **f,
                        "located": True,
                        "source_ref": source_ref,
                        "ocr_boundary": bool(doc.get("ocr_boundary")) or bool(rec.get("ocr")),
                        "facsimile_corroborated": False,
                        "corroborating_files": [],
                        "status": "located",
                    }
                )
            break
    gap = ""
    if not entries and doc_kind(rec, scoring) != "non_patent":
        # 技术文献（期刊/会议论文）本来就没有权利要求，不算缺口；只有专利类记录才挂 R_CLAIM_SOURCE_UNAVAILABLE。
        linked = [materials_by_id[mid]["declared_path"] for mid in rec.get("fac_refs") or [] if mid in materials_by_id]
        if linked:
            gap = "R_CLAIM_SOURCE_UNAVAILABLE: 记录无内联权项，挂接的影印件/侧车也未能解析出文本权项"
        else:
            gap = "R_CLAIM_SOURCE_UNAVAILABLE: 记录无内联权项，也没有挂接任何影印件/侧车文本"
    summary = {
        "count": len(entries),
        "located": sum(1 for e in entries if e["located"]),
        "versions": sorted({e["version"] for e in entries}),
        "ocr_boundary": bool(rec.get("ocr")) or any(e["ocr_boundary"] for e in entries),
        "source_refs": sorted({e["source_ref"] for e in entries}),
        "gap": gap,
    }
    return entries, summary


def scan_materials(query: dict, materials: list, scoring: dict) -> list:
    """主题型/申请人型查询对材料正文的命中：关键词（+申请人）大小写不敏感查找，
    quote 为来源文本的精确切片，char_span 在该材料文本内逐字节可复核。"""
    mcfg = scoring.get("material_hits") or {}
    quote_chars = max(1, int(mcfg.get("quote_chars", 160)))
    max_hits = max(1, int(mcfg.get("max_hits_per_material", 5)))
    terms = [str(t).strip() for t in (query.get("keywords") or []) if str(t).strip()]
    assignee = str(query.get("assignee") or "").strip()
    if assignee:
        terms = [assignee] + terms
    if not terms:
        return []
    out = []
    for doc in materials:
        if not doc.get("text_available"):
            continue
        text = doc["text"]
        low = text.lower()
        hits, missing = [], []
        for t in terms:
            tl = t.lower()
            count = low.count(tl) if tl else 0
            if count <= 0:
                missing.append(t)
                continue
            pos = low.find(tl)
            quote = text[pos : pos + quote_chars]
            hits.append({"term": t, "count": count, "char_span": [pos, pos + len(quote)], "quote": quote})
            if len(hits) >= max_hits:
                break
        if not hits:
            continue
        out.append(
            {
                "material_id": doc["material_id"],
                "file": doc["declared_path"],
                "kind": doc["kind"],
                "sha256": doc["sha256"],
                "extraction_method": doc["extraction_method"],
                "ocr_boundary": doc["ocr_boundary"],
                "hits": hits,
                "hit_terms": len(hits),
                "terms_without_hit": missing,
            }
        )
    return out


# --------------------------------------------------------------------------------------
# 检索式与打分
# --------------------------------------------------------------------------------------
def build_queries(case: dict, topics_cfg: dict) -> list:
    raw = first_present(case, "queries", "search_queries", "requests", default=[])
    templates = topics_cfg.get("query_templates") or {}
    topics = topics_cfg.get("topics") or {}
    plan = []
    for i, q in enumerate(as_list(raw)):
        if not isinstance(q, dict):
            q = {"value": str(q)}
        qid = str(first_present(q, "query_id", "id", default=f"Q{i + 1}"))
        qtype = norm(first_present(q, "type", "query_type", default="number"))
        if qtype not in ("number", "topic", "assignee_topic"):
            qtype = "number" if first_present(q, "value", "number", default=None) else "topic"
        if qtype == "number":
            value = str(first_present(q, "value", "number", "query", default=""))
            expr = (templates.get("number") or "{value}").format(field="PN", value=value)
            plan.append({"query_id": qid, "type": qtype, "value": value, "expression": expr, "topics": []})
            continue
        topic_ids = [str(t) for t in as_list(first_present(q, "topics", "topic", default=[]))]
        keywords = [str(k) for k in as_list(first_present(q, "keywords", "terms", default=[]))]
        ipc = [str(k) for k in as_list(first_present(q, "ipc", "cpc", default=[]))]
        for t in topic_ids:
            tc = topics.get(t) or {}
            keywords.extend([str(x) for x in as_list(tc.get("keywords"))])
            ipc.extend([str(x) for x in as_list(tc.get("ipc_prefixes"))])
        keywords = list(dict.fromkeys([k for k in keywords if k]))
        ipc = list(dict.fromkeys([k for k in ipc if k]))
        assignee = str(first_present(q, "assignee", "applicant", default=""))
        if assignee:
            expr = (templates.get("assignee_topic") or "{assignee} {kw}").format(
                assignee=assignee, kw=" OR ".join(keywords[:4])
            )
        else:
            expr = (templates.get("topic") or "{ipc} {kw}").format(ipc=" OR ".join(ipc[:3]), kw=" OR ".join(keywords[:4]))
        plan.append(
            {
                "query_id": qid,
                "type": qtype,
                "topics": topic_ids,
                "keywords": keywords,
                "ipc": ipc,
                "assignee": assignee,
                "expression": expr,
            }
        )
    return plan


def key_hits(record_text: str, terms: list) -> list:
    nt = norm(record_text)
    return [t for t in terms if norm(t) and norm(t) in nt]


def ipc_hits(record_ipc: list, ipc_terms: list) -> list:
    out = []
    for want in ipc_terms:
        w = norm(want)
        for have in record_ipc:
            h = norm(have)
            if not w or not h:
                continue
            if h == w or h.startswith(w) or w.startswith(h):
                out.append({"want": want, "record_value": have})
                break
    return out


def doc_kind(rec: dict, scoring: dict) -> str:
    cfg = scoring.get("metadata_fields") or {}
    indicators = [norm(x) for x in as_list(cfg.get("non_patent_indicators"))]
    blob = norm(" ".join([rec.get("doc_type", ""), rec.get("source_db", "")]))
    if not (rec.get("publication_raw") or "").strip() and any(i and i in blob for i in indicators):
        return "non_patent"
    return "patent"


def score_record(rec: dict, num_norm: dict, query: dict, scoring: dict, materials_by_id: dict | None = None) -> dict:
    w = scoring.get("relevance_weights") or {}
    cfg_fields = scoring.get("metadata_fields") or {}
    kind = doc_kind(rec, scoring)
    meta_fields = as_list(cfg_fields.get(kind) or cfg_fields.get("patent") or [])
    present = [f for f in meta_fields if rec.get(f) not in (None, "", [], 0)]
    metadata = clamp(len(present) / max(1, len(meta_fields)))
    number_match = 0.0
    number_mode = ""
    if query.get("type") == "number":
        q = normalize_number(query.get("value", ""), {})
        cands = {
            "publication": rec.get("pub_norm", {}),
            "application": rec.get("app_norm", {}),
            "priority": rec.get("prio_norm", {}),
        }
        for field, n in cands.items():
            if not n or not n.get("digits"):
                continue
            if n.get("canonical") and n.get("canonical") == q.get("canonical"):
                number_match = 1.0
                number_mode = f"canonical:{field}"
                break
            if q.get("digits") and n.get("digits") == q.get("digits") and n.get("country") == q.get("country"):
                number_match = 0.85
                number_mode = f"digits:{field}"
                break
    # 文献记录的正文（挂接的对比文献全文）参与该记录自身的主题打分：
    # 期刊论文的元数据很薄，只看 title+abstract 会把真实相关的文献压到阈值之下。
    # 影印件（facsimile）正文不参与打分——它的权项只用于定位与佐证。
    body_parts = []
    for mid in rec.get("fac_refs") or []:
        doc = materials_by_id.get(mid) or {}
        if doc.get("kind") == "literature" and doc.get("text_available"):
            body_parts.append(doc["text"])
    body_text = "\n".join(body_parts)
    text = " ".join([rec.get("title", ""), rec.get("abstract", ""), body_text])
    norm_cfg = scoring.get("normalization") or {}
    kw_cap = max(1, int(norm_cfg.get("keyword_cap", 3)))
    ipc_cap = max(1, int(norm_cfg.get("ipc_cap", 2)))
    kw = key_hits(text, query.get("keywords") or [])
    kw_ratio = clamp(min(len(kw), kw_cap) / kw_cap) if query.get("keywords") else 0.0
    ipc = ipc_hits(rec.get("ipc", []) + rec.get("cpc", []), query.get("ipc") or [])
    ipc_ratio = clamp(min(len(ipc), ipc_cap) / ipc_cap) if query.get("ipc") else 0.0
    relevance = clamp(
        float(w.get("number_match", 0.5)) * number_match
        + float(w.get("keyword_match", 0.2)) * kw_ratio
        + float(w.get("ipc_match", 0.2)) * ipc_ratio
        + float(w.get("metadata_completeness", 0.1)) * metadata
    )
    return {
        "relevance": rounded(relevance),
        "score_breakdown": {
            "number_match": rounded(number_match),
            "number_match_mode": number_mode,
            "keyword_match": rounded(kw_ratio),
            "ipc_match": rounded(ipc_ratio),
            "metadata_completeness": rounded(metadata),
            "weights": {k: float(v) for k, v in w.items()},
        },
        "keyword_hits": kw,
        "ipc_hits": ipc,
    }


# --------------------------------------------------------------------------------------
# 族与法律状态
# --------------------------------------------------------------------------------------
def build_families(records: list, scoring: dict) -> tuple:
    thr = scoring.get("thresholds") or {}
    jmin = float(thr.get("title_jaccard_min", 0.7))
    ywin = int(thr.get("family_year_window", 2))
    groups, assigned = [], {}

    def prio_key(r):
        n = r.get("prio_norm") or {}
        return n.get("canonical") or ""

    by_prio = {}
    for r in records:
        k = prio_key(r)
        if k:
            by_prio.setdefault(k, []).append(r)
    for k in sorted(by_prio):
        groups.append({"members": by_prio[k], "basis": f"priority:{k}"})
        for m in by_prio[k]:
            assigned[m["record_id"]] = True
    rest = [r for r in records if not assigned.get(r["record_id"])]
    for r in rest:
        if assigned.get(r["record_id"]):
            continue
        group = [r]
        for o in rest:
            if o["record_id"] == r["record_id"] or assigned.get(o["record_id"]):
                continue
            same_assignee = norm(r["assignee"]) and norm(r["assignee"]) == norm(o["assignee"])
            tsim = jaccard(tokens(r["title"]), tokens(o["title"]))
            tkey = 1.0 if (norm_title(r["title"]) and norm_title(r["title"]) == norm_title(o["title"])) else tsim
            years = [x for x in (r.get("priority_date", "")[:4], o.get("priority_date", "")[:4]) if str(x).isdigit()]
            close = len(years) == 2 and abs(int(years[0]) - int(years[1])) <= ywin
            if same_assignee and tkey >= jmin and (close or len(years) < 2):
                group.append(o)
        for m in group:
            assigned[m["record_id"]] = True
        groups.append({"members": group, "basis": "assignee+title+priority_date" if len(group) > 1 else "single_record"})
    out = []
    for g in groups:
        fid = "F-" + stable_id(*sorted(m["record_id"] for m in g["members"]), n=8)
        out.append(
            {
                "family_id": fid,
                "basis": g["basis"],
                "members": [
                    {
                        "record_id": m["record_id"],
                        "publication_number": (m.get("pub_norm") or {}).get("canonical", ""),
                        "application_number": (m.get("app_norm") or {}).get("canonical", ""),
                        "priority_number": (m.get("prio_norm") or {}).get("canonical", ""),
                        "priority_date": m.get("priority_date", ""),
                        "title": m.get("title", ""),
                    }
                    for m in g["members"]
                ],
                "member_count": len(g["members"]),
                "scope": "observed_not_exhaustive",
                "scope_note": "只声明本轮观察到的同族关系，不声称穷尽简单族/扩展族，也不声称权项范围等同",
            }
        )
    return out, {m["record_id"]: f["family_id"] for f in out for m in f["members"]}


def resolve_legal_status(rec: dict, cfg: dict) -> dict:
    legal = cfg.get("legal_status") or {}
    observed = rec.get("legal_status_observed")
    tier = rec.get("source_tier", "unknown")
    requires = legal.get("verified_requires_tier", "official_registry")
    value, verified, basis = legal.get("default", "unverified"), False, ""
    obs_value, obs_source, obs_date = "", "", ""
    if isinstance(observed, dict):
        obs_value = str(first_present(observed, "value", "status", "state", default=""))
        obs_source = str(first_present(observed, "source", "db", default=""))
        obs_date = str(first_present(observed, "date", "queried_at", default=""))
    elif observed:
        obs_value = str(observed)
    if obs_value and tier == requires and rec.get("queried_at"):
        value, verified, basis = obs_value, True, f"官方登记库 {rec.get('source_db')}（查询日期 {rec.get('queried_at')}）"
    elif obs_value:
        value = legal.get("default", "unverified")
        basis = f"观测值来自 {obs_source or rec.get('source_db') or '未标注来源'}，来源分级 {tier}，不作为登记依据"
    else:
        basis = "材料未提供官方登记状态"
    return {
        "value": value,
        "verified": verified,
        "observed_value": obs_value,
        "observed_source": obs_source or rec.get("source_db", ""),
        "observed_date": obs_date or rec.get("queried_at", ""),
        "source_tier": tier,
        "basis": basis,
        "recheck_path": str(legal.get("recheck_template", "")).format(
            canonical=(rec.get("pub_norm") or {}).get("canonical", rec.get("publication_raw", "")),
            country=(rec.get("pub_norm") or {}).get("country", ""),
        ),
    }


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    cfg, topics_cfg, scoring = rules["fields"], rules["topics"], rules["scoring"]
    fac_cfg = rules["facsimile"]
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res.contract.keys()))

    work = Path(getattr(args, "work", ".") or ".")
    case_base = Path(getattr(args, "case", "") or ".").parent
    records = collect_records(case, work, cfg)
    queries = build_queries(case, topics_cfg)
    res.log("materials", records=len(records), queries=len(queries), task_chars=len(task_text or ""))

    for r in records:
        r["pub_norm"] = normalize_number(r["publication_raw"], cfg)
        r["app_norm"] = normalize_number(r["application_raw"], cfg)
        r["prio_norm"] = normalize_number(r["priority_raw"], cfg)

    materials = gather_materials(case, records, case_base, fac_cfg)
    materials_by_id = {d["material_id"]: d for d in materials}

    if not records:
        res.set("candidates", [])
        res.set("field_table", [])
        res.set("legal_status", [])
        res.set("claims_table", [])
        res.bind(
            {"evidence_map": {"search_expressions": queries, "materials_read": len(materials), "note": "空语料：无检索记录，仅登记检索式"}},
            aliases={"检索结果": "candidates", "候选专利": "candidates", "字段表": "field_table", "证据记录": "evidence_map"},
        )
        res.status["overall"] = "empty"
        res.unmet("检索结果", "case.json 未提供专利/技术文献记录（records/patents 为空）")
        res.unsure("法律状态", "无记录时不产出任何状态结论，也不填充‘有效/失效’")
        res.note("空结果不等于该号码或主题不存在；补齐记录或提供授权数据源后重跑")
        res.done("材料读取")
        return res

    thr = scoring.get("thresholds") or {}
    min_rel = float(thr.get("min_relevance", 0.2))
    limit = int(thr.get("max_results", 10))

    scored, excluded, query_scores = {}, [], {}
    for q in queries:
        for r in records:
            s = score_record(r, r["pub_norm"], q, scoring, materials_by_id)
            query_scores.setdefault(r["record_id"], {})[q["query_id"]] = s["relevance"]
            prev = scored.get(r["record_id"])
            if prev is None or s["relevance"] > prev[1]["relevance"]:
                scored[r["record_id"]] = (q["query_id"], s)
            elif s["relevance"] == prev[1]["relevance"] and q["query_id"] < prev[0]:
                scored[r["record_id"]] = (q["query_id"], s)
    for r in records:
        if r["record_id"] not in scored or scored[r["record_id"]][1]["relevance"] < min_rel:
            excluded.append(
                {
                    "record_id": r["record_id"],
                    "title": r["title"],
                    "publication_number": r["pub_norm"].get("canonical", ""),
                    "code": "R_LOW_REL" if r["record_id"] in scored else "R_NO_QUERY_HIT",
                    "relevance": (scored.get(r["record_id"]) or ("", {"relevance": 0.0}))[1]["relevance"],
                }
            )

    families, fam_of = build_families(records, scoring)
    literature_hits = []
    for q in queries:
        if q.get("type") in ("topic", "assignee_topic"):
            hits = scan_materials(q, materials, scoring)
            if hits:
                literature_hits.append(
                    {"query_id": q["query_id"], "query_type": q["type"], "expression": q["expression"], "materials": hits}
                )
    candidates = []
    for r in records:
        if r["record_id"] not in scored:
            continue
        qid, s = scored[r["record_id"]]
        if s["relevance"] < min_rel:
            continue
        located, claims_summary = build_claim_view(r, materials_by_id, scoring)
        status = resolve_legal_status(r, cfg)
        fac_links = [materials_by_id[mid] for mid in r.get("fac_refs") or [] if mid in materials_by_id]
        candidates.append(
            {
                "record_id": r["record_id"],
                "title": r["title"],
                "assignee": r["assignee"],
                "inventors": r["inventors"],
                "publication_number": r["pub_norm"],
                "application_number": r["app_norm"],
                "priority_number": r["prio_norm"],
                "priority_date": r["priority_date"],
                "filing_date": r["filing_date"],
                "publication_date": r["publication_date"],
                "ipc": r["ipc"],
                "cpc": r["cpc"],
                "family_id": fam_of.get(r["record_id"], ""),
                "claims": located,
                "claims_summary": claims_summary,
                "facsimiles": [material_public_view(d) for d in fac_links],
                "legal_status": status,
                "provenance": {
                    "db": r["source_db"],
                    "queried_at": r["queried_at"],
                    "source_tier": r["source_tier"],
                    "restricted": r["restricted"],
                },
                "relevance": s["relevance"],
                "score_breakdown": s["score_breakdown"],
                "matched_query_id": qid,
                "query_scores": query_scores.get(r["record_id"], {}),
                "keyword_hits": s["keyword_hits"],
                "ipc_hits": s["ipc_hits"],
            }
        )
    candidates.sort(key=lambda x: (-float(x["relevance"]), x["record_id"]))
    for i, c in enumerate(candidates[:limit], start=1):
        c["rank"] = i
    candidates = candidates[:limit]

    field_table = [
        {
            "record_id": r["record_id"],
            "publication_number_raw": r["publication_raw"],
            "publication_number_canonical": r["pub_norm"]["canonical"],
            "publication_number_kind": r["pub_norm"]["number_kind"],
            "publication_kind_code": r["pub_norm"]["kind_code"],
            "application_number_canonical": r["app_norm"]["canonical"],
            "application_number_kind": r["app_norm"]["number_kind"],
            "priority_number_canonical": r["prio_norm"]["canonical"],
            "priority_date": r["priority_date"],
            "filing_date": r["filing_date"],
            "publication_date": r["publication_date"],
            "assignee": r["assignee"],
            "ipc": r["ipc"],
            "family_id": fam_of.get(r["record_id"], ""),
            "source_db": r["source_db"],
            "queried_at": r["queried_at"],
            "source_tier": r["source_tier"],
            "ocr": r["ocr"],
            "facsimile_files": [
                {"material_id": mid, "file": materials_by_id[mid]["declared_path"], "linkage": lk}
                for mid in r.get("fac_refs") or []
                if mid in materials_by_id
                for lk in [
                    next((x["how"] for x in materials_by_id[mid]["linkage"] if x["record_id"] == r["record_id"]), "declared")
                ]
            ],
            "wo_ocr_merged": bool(r["pub_norm"]["wo_merged"]),
            "number_ambiguous": bool(r["pub_norm"]["ambiguous"] or r["app_norm"]["ambiguous"]),
            "alternatives": r["pub_norm"]["alternatives"],
        }
        for r in records
    ]

    legal_table = [
        {"record_id": c["record_id"], "publication_number": c["publication_number"]["canonical"], **c["legal_status"]}
        for c in candidates
    ]
    claims_table = [
        {
            "record_id": c["record_id"],
            "publication_number": c["publication_number"]["canonical"],
            "versions": [{"version": v} for v in c["claims_summary"]["versions"]],
            "source_refs": c["claims_summary"]["source_refs"],
            "gap": c["claims_summary"]["gap"],
            "claims": c["claims"],
            "ocr_boundary": c["claims_summary"]["ocr_boundary"],
        }
        for c in candidates
    ]

    unverified = [x for x in legal_table if not x["verified"]]
    overscoped = [
        x
        for x in legal_table
        if x["observed_value"] and not x["verified"] and norm(x["observed_value"]) in ("granted", "expired", "有效", "已授权", "失效")
    ]
    ocr_records = [r["record_id"] for r in records if r["ocr"]]
    ambiguous_numbers = [r["record_id"] for r in records if r["pub_norm"]["ambiguous"]]
    wo_merged_ids = [r["record_id"] for r in records if r["pub_norm"]["wo_merged"]]
    ext_counts = {}
    for d in materials:
        ext_counts[d["extraction_method"]] = ext_counts.get(d["extraction_method"], 0) + 1
    failed_materials = [
        {"file": d["declared_path"], "method": d["extraction_method"], "note": d["extraction_note"]}
        for d in materials
        if d["extraction_method"] in ("failed", "missing")
    ]
    fac_backed_claims = sorted(
        {
            c["record_id"]
            for c in claims_table
            if any(e.get("source_ref") not in ("inline_field", None) for e in c["claims"])
        }
    )
    claim_gap_ids = [c["record_id"] for c in claims_table if c["gap"]]

    evidence_map = {
        "search_expressions": queries,
        "number_normalization": [
            {
                "record_id": r["record_id"],
                "publication": r["pub_norm"],
                "application": r["app_norm"],
                "priority": r["prio_norm"],
                "rule": "rules/field_dictionary.json.number_kinds + rules/facsimile.json.wo_normalization + 代码中的号码解析；ambiguous/合并 WO 时给拆解候选",
            }
            for r in records
        ],
        "facsimiles": [material_public_view(d) for d in materials],
        "literature_hits": literature_hits,
        "family_basis": families,
        "claims_lookup": claims_table,
        "legal_status_lookup": legal_table,
        "provenance_rules": scoring.get("provenance_rules") or {},
        "recompute": "relevance = w_num*number_match + w_kw*keyword_match + w_ipc*ipc_match + w_meta*metadata（权重见 rules/scoring.json）；"
        "主题/申请人查询另对材料正文做关键词命中（rules/scoring.json.material_hits），quote 为来源文本精确切片；"
        "claims quote/char_span 在其 source_ref 指向的来源文本内逐字节可复核",
        "excluded": excluded,
    }

    payload = {
        "candidates": candidates,
        "field_table": field_table,
        "legal_status": legal_table,
        "claims_table": claims_table,
        "evidence_map": evidence_map,
    }
    res.set("claims_table", payload.pop("claims_table"))
    res.set("patent_families", families)
    res.set("excluded", excluded)
    res.bind(payload, aliases={"检索结果": "candidates", "候选专利": "candidates", "字段表": "field_table", "证据记录": "evidence_map"})
    res.evidence["rules_applied"] = {
        "field_dictionary": "rules/field_dictionary.json",
        "ipc_topics": "rules/ipc_topics.json",
        "scoring": "rules/scoring.json",
        "facsimile": "rules/facsimile.json",
    }

    if not candidates:
        res.status["overall"] = "empty"
        res.unmet("检索引擎", "记录存在但全部未命中查询或低于阈值，见 evidence_map.excluded")
    else:
        res.status["overall"] = "complete"
    if unverified:
        res.unmet(
            "法律状态核验",
            f"{len(unverified)}/{len(legal_table)} 条记录的状态为 unverified（来源分级不满足 official_registry 或缺查询日期）；"
            "本轮未获官方登记库访问，未把二手来源写成确定状态，逐条复核路径见 legal_status[].recheck_path",
        )
    if overscoped:
        res.note(f"{len(overscoped)} 条记录的观测状态来自非官方来源，已按 R_OVERSCOPED_CLAIM 处置，未写成确定状态")
    if ocr_records:
        res.note(f"{ocr_records} 为 OCR/影印件来源，权项引用已标 ocr_boundary，残句不作为完整权项范围")
    if ambiguous_numbers:
        res.note(f"{ambiguous_numbers} 的号码缺种类码或位数不足，已给候选解释并标 ambiguous，需回官方登记确认")
    if wo_merged_ids:
        res.note(f"{wo_merged_ids} 的 WO 号疑似 OCR 合并（丢斜杠/空格），已按「年份/序号」给拆解候选并标 ambiguous，需回 WIPO/受理局登记确认")
    if materials:
        readable = sum(1 for d in materials if d["text_available"])
        res.note(
            f"材料正文 {readable}/{len(materials)} 份可读（方法分布 {dict(sorted(ext_counts.items()))}）；"
            "SHA-256/字符数/提取方法见 evidence_map.facsimiles"
        )
    if failed_materials:
        res.unmet(
            "材料文本提取",
            f"{len(failed_materials)} 份材料未取得文本（{[(f['file'], f['method']) for f in failed_materials]}）："
            "提取失败如实记缺口，未编造正文",
        )
    if literature_hits:
        res.note(
            f"主题/申请人查询对材料正文另做关键词命中 {sum(len(x['materials']) for x in literature_hits)} 份"
            "（quote 为来源文本精确切片，见 evidence_map.literature_hits），与 records 打分分开呈现"
        )
    if fac_backed_claims:
        res.note(f"{fac_backed_claims} 的权项直接定位到影印件/侧车文本（source_ref 带文件与页码，字符区间可复核）")
    if claim_gap_ids:
        res.unmet("权利要求定位", f"{claim_gap_ids} 无可定位的权项文本（见 claims_table[].gap），未摘引、未改写、未编造")
    res.note("专利族只声明本轮观察到的关系（observed_not_exhaustive），不声称穷尽简单族/扩展族")
    res.note("本 skill 不做侵权判定、FTO 结论与专利价值评估，不绕过付费墙、不外显受限库原文")
    res.unsure("字段准确性", "号码、日期、权项均按记录原值规范化，未联网回官方库核对")
    res.unsure("族完整性", "族成员仅限本轮候选记录，未引入库外成员")
    res.unsure("法律状态", "题面给定当前官方状态未知；所有状态值均为 unverified 并附复核路径")

    res.done(
        "材料读取", "传真件文本读取", "号码规范化", "检索式构造", "相关性打分", "材料正文命中",
        "专利族归并", "权利要求定位", "法律状态处置",
    )
    return res


def _write_input_error(args, exc: Exception) -> int:
    payload = {
        "case_id": "UNKNOWN-CASE",
        "skill": {"name": SKILL, "version": "0.1.0"},
        "candidates": [],
        "field_table": [],
        "legal_status": [],
        "claims_table": [],
        "evidence_map": {"error": f"{type(exc).__name__}: {exc}"},
        "status": {
            "overall": "input_error",
            "completed": [],
            "unmet": [{"item": "case.json", "reason": str(exc)}],
            "notes": ["输入不可读时立即失败并保留错误原文，不产出伪造结果"],
        },
        "uncertainty": [],
    }
    out = Path(args.out) / "result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": False, "result": str(out), "error": str(exc)}, ensure_ascii=False))
    return 2


def main(argv=None) -> int:
    from kit import self_check

    args = parse_args(argv)
    if args.self_check:
        return self_check()
    try:
        case = load_case(args.case)
    except Exception as exc:  # noqa: BLE001
        return _write_input_error(args, exc)

    task_text = load_task_text(args, case)
    res = run(case, task_text, args)
    payload = res.build()
    if args.dry_run:
        print(json.dumps({"case_id": payload["case_id"], "status": payload["status"]["overall"]}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
