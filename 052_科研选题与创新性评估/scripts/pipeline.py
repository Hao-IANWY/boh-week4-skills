#!/usr/bin/env python3
"""052 科研选题与创新性评估 —— 主流程（纯标准库，离线、确定性、全流程留痕）。

输入：case.json（候选选题方向 1-3 + 来源/记录双结构 + 前瞻筛选协议 + 约束 + 可选建议场景）+ task.md
输出：out/result.json + work/plan.json（协议与筛选顺序先于执行落盘）

七阶段（摄入、方向化、协议、取证、新颖性、前提、建议全部留痕，任何人可按记录复算）：
  0. 摄入（复用 048 已验证机器）：sources[]（来源文件清单）与 records[]（记录）双结构建模；
      记录经 source_path/source_id 指到来源文件；正文按 内联 text → 内联 abstract → 侧车
      .txt/.ocr.txt → 自研 stdlib PDF 提取（scripts/pdf_min.py）→ DOCX zip+xml → 文本直读
      → 诚实 missing 取数，逐条记 extraction_method + SHA-256 + 字符数。
  A. 候选方向字段化：case.candidate_directions[]（1-3 个候选方向）优先，缺省回退 idea 兜底成单
      方向；每方向 = 可证伪问题陈述（对象/变量/基线/预期/可证伪）+ claims（创新主张）+ 检索键。
  B. 前瞻协议：纳排标准/检索顺序/停止条件先写入 work/plan.json，再逐条筛选（criterion_hits）；
      协议缺失记 input_gap（R_PROTOCOL_MISSING）并声明回退口径，不冒充「已按协议筛选」。
      仅 included 记录进入检索、新颖性比对与前提取证。
  C. 取证：逐方向 BM25 检索（键 token 展开，CJK 二元组不退化）→ 证据表
      （evidence_id + direction_id + record_id + source_file + char_span + 逐字 quote，管线内
      精确子串校验，失败标 quote_mismatch；题名不进引文；方向词典标注 ±1/0/mixed）。
  D. 新颖性评估（novelty_scope=pool_observed）：每条主张按 rules/novelty_types.json 四分类
      → 池内逐条覆盖比对（claim_coverage，阈值外置 scoring.json）→ 最近邻记录 + 五维逐维
      重合点 + 逐字重合引文（回链证据表）→ not_found_in_scope 只写「池内未见
      （pool_observed），不构成全球首创判定」+ 池外核验建议（检索式 + 数据库建议，
      executed=false 声明未执行全局检索）。
  E. 前提清单与条件式可行性：每前提三分源 task_given（case 约束字段）| record_evidence
      （池内证据，必须带 evidence_id 回链）| explicit_assumption（无来源）；可行性结论随前提
      强度降级（task_given > record_evidence > explicit_assumption）：有假设前提写
      「若 P 成立则…」；constraints 缺失判 unconstrained，不给「可行」结论。
  F. 冲突与风险：方向词典成对冲突双列（side_a/side_b 各带逐字引文）；骑在冲突证据上的候选
      方向，风险条目如实标注两侧引文，不仲裁；价值/风险并置，不合成单一总分。
  G. 有界建议与评分：建议逐条引用 constraints 且 not_claimed（不声称已完成实验、不越范围）；
      constraints 缺失只给方向级建议并声明「无约束可依」；四维评分全部按 rules/scoring.json
      外置参数线性组合，可复算。
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
    SKILL_VERSION,
    as_list,
    bm25_score,
    clamp,
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
    tokens,
    write_json,
)
import pdf_min  # noqa: E402
from normalize import normalize_case  # noqa: E402

SKILL = "research-idea-novelty-assessment"
RULES = Path(__file__).resolve().parent.parent / "rules"


def load_rules() -> dict:
    return {
        "scoring": load_json(RULES / "scoring.json", default={}) or {},
        "novelty": load_json(RULES / "novelty_types.json", default={}) or {},
        "premise": load_json(RULES / "premise_checklist.json", default={}) or {},
        "codes": (load_json(RULES / "reason_codes.json", default={}) or {}).get("codes", {}),
        "routing": load_json(RULES / "contract_routing.json", default={}) or {},
        "protocol": load_json(RULES / "protocol_rules.json", default={}) or {},
        "direction": load_json(RULES / "direction_lexicon.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 0. 来源/记录双结构：路径解析与正文获取（与 048 同构，已验证机器）
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
        text, side, _err = _sidecar_read(resolved)
        if text:
            return _body_info(text, "sidecar_txt", side)
        text, side, _err = _sidecar_read(resolved, (".ocr.txt",))
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
    raw = first_present(case, "records", "prior_art", "candidates", "papers", "literature",
                        "pool", "materials", "corpus", default=[])
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
            "task": str(first_present(item, "task", "problem", "setting", default="")),
            "method": str(first_present(item, "method", "approach", default="")),
            "data": str(first_present(item, "data", "dataset", "corpus", default="")),
            "evaluation": str(first_present(item, "evaluation", "metric", "benchmark", default="")),
            "conclusion": str(first_present(item, "conclusion", "finding", "result", default="")),
            "version": str(first_present(item, "version", default="") or ""),
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
                "abstract": "", "text": info["text"], "body": info,
                "source_ref": ref, "source_id": str(src.get("source_id") or ""),
                "source_file": info["source_file"], "pages": [], "year": None, "venue": "", "doi": "",
                "type": str(src.get("type") or ""), "source": "", "task": "", "method": "", "data": "",
                "evaluation": "", "conclusion": "", "version": "",
            })
    return out


def record_text(rec: dict) -> str:
    return "\n".join([rec["title"], rec["abstract"], rec["text"]]).strip()


def dedupe(records: list, dropped: list, codes: dict) -> list:
    seen, kept = {}, []
    for r in records:
        key = norm(r["doi"]).replace("https://doi.org/", "") or norm_title(r["title"])
        if key and key in seen:
            dropped.append({"record_id": r["record_id"], "title": r["title"], "code": "R_DUP_TITLE",
                            "reason": codes.get("R_DUP_TITLE", ""), "kept_record_id": seen[key]})
            continue
        if key:
            seen[key] = r["record_id"]
        kept.append(r)
    return kept


# --------------------------------------------------------------------------------------
# A. 候选方向字段化（052 特有：多候选选题）
# --------------------------------------------------------------------------------------
FIELD_ALIASES = {
    "object": ["object", "研究对象", "subject", "target"],
    "variables": ["variables", "关键变量", "outcome", "predictors", "factors"],
    "baseline": ["baseline", "comparison", "基线", "control", "对照"],
    "expected": ["expected", "expected_finding", "预期结论", "expectation", "hypothesis"],
    "falsification": ["falsification", "falsifiable", "可证伪", "refutation", "disproof"],
    "method": ["method", "approach", "方法", "技术路线"],
    "data": ["data", "dataset", "数据"],
    "evaluation": ["evaluation", "metric", "评价指标", "评估方式"],
}

REQUIRED_FIELDS = ("object", "variables", "baseline", "expected", "falsification")


def _grab_field(patterns, text: str):
    for p in patterns:
        m = re.search(p, text, flags=re.I)
        if m:
            seg = m.group(0)
            seg = re.split(r"[。；;\n]", seg)[0]
            if len(seg) > 4:
                return seg.strip(" ：:，,")
    return None


def direction_fields(item: dict, text: str) -> dict:
    """显式字段优先（case 给什么用什么），缺的字段按句式规则从方向文本抽取；缺失不猜。"""
    fields = {}
    if isinstance(item, dict):
        for canon, keys in FIELD_ALIASES.items():
            v = first_present(item, *keys, default=None)
            if v not in (None, "", []):
                fields[canon] = v if isinstance(v, list) else str(v)
    lower_ok = text.strip()
    if lower_ok:
        if "object" not in fields:
            v = _grab_field([r"(?:研究对象|object(?:ive)?|target(?:s)?)[:：]\s*[^\n。;；]{4,80}",
                             r"(?:研究|分析|评估)([^\n。;；]{4,40})(?:的|在)"], text)
            if v:
                fields["object"] = v
        if "falsification" not in fields:
            v = _grab_field([r"(?:可证伪|falsifiab\w*)[^\n。;；]{0,80}",
                             r"若[^\n。;；]{2,40}则(?:该|本)?(?:假设|结论)[^\n。;；]{0,30}不成立"], text)
            if v:
                fields["falsification"] = v
        if "baseline" not in fields:
            v = _grab_field([r"(?:基线|对照|baseline)[^\n。;；]{0,60}"], text)
            if v:
                fields["baseline"] = v
        if "expected" not in fields:
            v = _grab_field([r"(?:预期|假设|hypothes[ie]s?)[^\n。;；]{0,80}"], text)
            if v:
                fields["expected"] = v
        if "variables" not in fields:
            vs = re.findall(r"(?:变量|variable)[^\n。;；]{0,60}", text, flags=re.I)
            if vs:
                fields["variables"] = vs[:3]
    return fields


def parse_directions(case: dict, task_text: str) -> list:
    """候选方向解析：case.candidate_directions/candidates/topics/directions 优先，
    缺省回退 idea（单方向），再回退 task.md 文本（单方向，source=task.md）。缺失不猜。"""
    raw = first_present(case, "candidate_directions", "candidates", "topics", "directions",
                        "选题", "topic_proposals", default=None)
    items = [x for x in as_list(raw) if isinstance(x, dict) or (isinstance(x, str) and x.strip())]
    source = "case.candidate_directions"
    if not items:
        idea = first_present(case, "idea", "proposal", "research_idea", default=None)
        if isinstance(idea, dict):
            items = [idea]
            source = "case.idea"
        elif isinstance(idea, str) and idea.strip():
            items = [idea]
            source = "case.idea"
        else:
            body = "\n".join(l for l in task_text.splitlines()
                             if l.strip() and not l.strip().startswith("#"))[:2000]
            if body.strip():
                items = [body]
                source = "task.md"
    out = []
    for idx, item in enumerate(items, start=1):
        if isinstance(item, str):
            item = {"title": "", "text": item}
        title = str(first_present(item, "title", "name", "方向", default="") or "").strip()
        text_parts = [str(first_present(item, "text", "description", "summary", "description_md",
                                        "background", "motivation", default="") or "")]
        if not text_parts[0].strip() and title:
            text_parts = [title]
        text = "\n".join(t for t in text_parts if t).strip()
        fields = direction_fields(item, text)
        keys = [str(k).strip() for k in as_list(first_present(item, "keys", "query_keys",
                                                              "keywords", default=[])) if str(k).strip()]
        if not keys:
            derived = [k for k in (title, fields.get("object")) if k]
            keys = [str(k)[:40] for k in derived]
        did = str(first_present(item, "direction_id", "id", default=f"D{idx}") or f"D{idx}")
        cons = item.get("constraints") if isinstance(item.get("constraints"), dict) else {}
        claims_raw = first_present(item, "claims", "innovations", "contributions", "创新点", default=None)
        out.append({
            "direction_id": did, "title": title, "text": text[:2000],
            "fields": fields, "keys": keys, "claims_raw": claims_raw,
            "constraints": cons,
            "application": first_present(item, "application", "use_case", "应用场景", default=None),
            "stakeholders": first_present(item, "stakeholders", "users", "决策方", default=None),
            "missing_fields": [k for k in REQUIRED_FIELDS if k not in fields or not str(fields[k]).strip()],
            "source": source,
        })
    return out


def extract_claims(direction: dict, codes: dict) -> list:
    """方向 claims：显式 claims 优先；否则按线索句从方向文本抽取；两者皆无回退方向标题。"""
    claims = []
    raw = direction.get("claims_raw")
    for i, c in enumerate(as_list(raw)):
        if isinstance(c, dict):
            text = str(first_present(c, "claim", "text", "statement", "描述", default="")).strip()
            cid = str(first_present(c, "id", "claim_id", default="") or f"{direction['direction_id']}-C{i + 1}")
        else:
            text = str(c)
            cid = f"{direction['direction_id']}-C{i + 1}"
        if text:
            claims.append({"claim_id": cid, "claim": text, "origin": "case.claims"})
    if claims:
        return claims
    cues = ("we propose", "we introduce", "this work", "贡献", "创新", "提出", "首次", "novel", "contribution")
    for line in re.split(r"[。\n；;]", direction.get("text", "")):
        s = line.strip()
        if len(s) < 8:
            continue
        if any(c in s.lower() for c in cues):
            claims.append({"claim_id": f"{direction['direction_id']}-C{len(claims) + 1}",
                           "claim": s[:200], "origin": "direction_text_cue"})
        if len(claims) >= 6:
            break
    if not claims:
        claims.append({"claim_id": f"{direction['direction_id']}-C1",
                       "claim": direction.get("title", "") or direction.get("text", "")[:120],
                       "origin": "fallback_title", "note": codes.get("R_NO_IDEA", "")})
    return claims


# --------------------------------------------------------------------------------------
# B. 前瞻协议（先落盘，后逐条筛选；复用 048 口径）
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
            "fallback": {"declared_in": "rules/protocol_rules.json#fallback_screening", "note": str(fb.get("note", ""))}}


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
            reason = "记录正文不可读（内联/侧车/原生提取均失败），按口径不参与纳排与比对，诚实降级"
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
# C. 逐方向检索执行（复用 048 修复过的 CJK 键展开口径）
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


def build_plan(directions: list) -> list:
    plan = []
    for d in directions:
        plan.append({"plan_id": f"PL-{d['direction_id']}", "direction_id": d["direction_id"],
                     "subquestion_id": d["direction_id"], "query_keys": d["keys"],
                     "query_expression": " OR ".join(d["keys"]), "sources": "included-records"})
    return plan


def execute_plan(plan: list, records: list, cfg: dict, trace: list) -> dict:
    """按 plan 顺序检索本地语料；逐次记录参数/命中/新增证据/停止判定。"""
    sc = cfg.get("retrieval", {})
    th = cfg.get("thresholds", {})
    top_k = int(th.get("top_records_per_direction", th.get("top_records_per_subquestion", 6)))
    min_score = float(th.get("min_record_score", 0.3))
    min_cover = float(th.get("min_evidence_key_coverage", 0.3))
    max_rounds = int(th.get("max_rounds", 2))
    evidence_by_sub, covered = {}, set()
    if not records:
        for p in plan:
            evidence_by_sub[p["direction_id"]] = []
            trace.append({"seq": 0, "tool": "local_retrieve", "params": {"plan_id": p["plan_id"], "keys": p["query_keys"]},
                          "result": {"hits": 0, "stopped_by": "empty_corpus", "note": "纳入记录为空，检索未执行"}})
        return evidence_by_sub
    doc_tokens = {r["record_id"]: tokens(record_text(r), unique=False) for r in records}
    corpus = list(doc_tokens.values())
    n = len(records) or 1
    for p in plan:
        keys = p["query_keys"]
        sub = p["direction_id"]
        if not keys:
            trace.append({"seq": 0, "tool": "local_retrieve", "params": {"plan_id": p["plan_id"], "keys": []},
                          "result": {"hits": 0, "stopped_by": "no_query_terms",
                                     "note": "候选方向缺检索键（R_NO_KEYS），未执行检索；新颖性仍做全池覆盖比对"}})
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
                                 "corpus_records": n},
                      "result": {"hits": len(got), "new_evidence": new, "rounds": rounds, "stopped_by": stopped_by,
                                 "top": [{"record_id": s["record_id"], "score": s["score"],
                                          "coverage": s["coverage"], "bm25": rounded(s["bm25"])} for s in got]}})
        evidence_by_sub[sub] = got
    return evidence_by_sub


# --------------------------------------------------------------------------------------
# D. 证据表（精确来源：direction_id + record_id + source_file + char_span + 逐字 quote + 校验）
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


def _scan_sentences(record: dict, terminators: list, split_period: bool):
    """yield (field, sentence)：题名互含行不进引文。"""
    for fld in ("text", "abstract"):
        src_text = record.get(fld) or ""
        if not src_text:
            continue
        for sent in split_sentences(src_text, terminators, split_period):
            sn, tn = norm(sent), norm(record.get("title", ""))
            if tn and (sn == tn or sn in tn or tn in sn):
                continue
            yield fld, sent


def _append_quote_row(rows: list, trace: list, *, use: str, direction_id: str, record: dict,
                      fld: str, sent: str, matched: list, cov: float, strength: str,
                      direction_rules: dict) -> dict:
    eid = f"E{len(rows) + 1:03d}"
    field_text = record[fld]
    start = field_text.find(sent)
    end = start + len(sent) if start >= 0 else -1
    exact = start >= 0 and field_text[start:end] == sent
    rows.append({
        "evidence_id": eid, "use": use, "direction_id": direction_id,
        "record_id": record["record_id"], "title": record["title"], "year": record["year"],
        "venue": record["venue"], "doi": record["doi"], "type": record["type"],
        "source": record["source"] or "local-corpus",
        "source_file": record["source_file"],
        "extraction_method": record["body"]["method"],
        "quote_field": fld, "quote": sent,
        "char_span": {"start": start, "end": end},
        "quote_verified": bool(exact),
        "body_sha256": record["body"]["sha256"],
        "location": {"field": fld, "page": None,
                     "record_pointer": f"{record['record_id']}#{fld}",
                     "char_span": {"start": start, "end": end}},
        "matched_terms": sorted(set(matched)),
        "key_coverage": rounded(cov), "strength": strength,
        "direction": detect_direction(sent, direction_rules),
        "sha256_quote": sha256_text(norm(sent)),
    })
    if not exact:
        rows[-1]["quote_mismatch"] = "quote 不是该字段原文的精确子串（span 定位失败），已显式标记，不静默"
    trace.append({"seq": 0, "tool": "evidence_extract",
                  "params": {"use": use, "direction_id": direction_id, "record_id": record["record_id"],
                             "field": fld, "matched_terms": sorted(set(matched))},
                  "result": {"evidence_id": eid, "char_span": {"start": start, "end": end},
                             "quote_chars": len(sent), "source_file": record["source_file"]}})
    trace.append({"seq": 0, "tool": "quote_verify",
                  "params": {"evidence_id": eid, "record_id": record["record_id"],
                             "source_file": record["source_file"], "field": fld},
                  "result": {"exact_substring": bool(exact), "sha256": record["body"]["sha256"],
                             "verdict": "verified" if exact else "mismatch_marked"}})
    return rows[-1]


def build_retrieval_evidence(plan: list, evidence_by_sub: dict, records: dict, cfg: dict, trace: list,
                             direction_rules: dict) -> dict:
    """逐方向取证：与 048 同构——按检索命中取每条记录的最佳证据句。"""
    term = cfg.get("sentence_terminators", ["。", "！", "？", ";", "；"])
    split_period = bool(cfg.get("split_ascii_period", True))
    th = cfg.get("thresholds", {})
    min_cover = float(th.get("min_evidence_key_coverage", 0.3))
    direct = float(th.get("direct_coverage", 0.6))
    indirect = float(th.get("indirect_coverage", 0.35))
    rows_by_dir, rows = {}, []
    for p in plan:
        sub = p["direction_id"]
        keys = p["query_keys"]
        rows_by_dir[sub] = []
        if not keys:
            continue
        n = len(records) or 1
        df = df_map(keys, records)
        live = [k for k in keys if df.get(k, 0) > 0]
        total_idf = sum(idf(df.get(k, 0), n) for k in live) or 1.0
        for s in evidence_by_sub.get(sub, []):
            rec = records.get(s["record_id"])
            if rec is None:
                continue
            best = None
            for fld, sent in _scan_sentences(rec, term, split_period):
                sn = norm(sent)
                hit = [k for k in live if k in sn]
                if not hit:
                    continue
                cov = sum(idf(df.get(k, 0), n) for k in hit) / total_idf
                if best is None or cov > best[0]:
                    best = (cov, fld, sent, hit)
            if best is None or best[0] < min_cover:
                continue
            cov, fld, sent, hit = best
            strength = "direct" if cov >= direct else ("indirect" if cov >= indirect else "background")
            row = _append_quote_row(rows, trace, use="retrieval", direction_id=sub, record=rec,
                                    fld=fld, sent=sent, matched=hit, cov=cov, strength=strength,
                                    direction_rules=direction_rules)
            rows_by_dir[sub].append(row["evidence_id"])
    return {"by_direction": rows_by_dir, "rows": rows}


def build_novelty_overlap_quote(rows: list, trace: list, *, direction_id: str,
                                record: dict, shared_terms: list, cfg: dict, novelty_cfg: dict,
                                direction_rules: dict):
    """最近邻重合引文：取共享词项最多的整句（每主张至多 1 条），写入证据表并回链；
    共享词项不足阈值时不造引文，标 no_overlap_quote。"""
    min_shared = int(novelty_cfg.get("nearest_neighbor", {}).get("min_shared_terms", 2))
    if len(shared_terms) < min_shared:
        return None
    term = cfg.get("sentence_terminators", ["。", "！", "？", ";", "；"])
    split_period = bool(cfg.get("split_ascii_period", True))
    best = None
    for fld, sent in _scan_sentences(record, term, split_period):
        sn = norm(sent)
        hit = [t for t in shared_terms if t in sn]
        if len(hit) < min_shared:
            continue
        if best is None or len(hit) > len(best[2]):
            best = (fld, sent, hit)
    if best is None:
        return None
    fld, sent, hit = best
    row = _append_quote_row(rows, trace, use="novelty_overlap", direction_id=direction_id, record=record,
                            fld=fld, sent=sent, matched=hit, cov=rounded(len(hit) / max(1, len(shared_terms))),
                            strength="overlap", direction_rules=direction_rules)
    return row


def build_premise_evidence(rows: list, trace: list, *, direction_id: str, premise: dict,
                           records: list, cfg: dict, direction_rules: dict):
    """前提的池内证据：按 evidence_cues 扫描纳入记录正文，取 cue 命中最多的整句。"""
    cues = [norm(c) for c in as_list(premise.get("evidence_cues", [])) if norm(c)]
    if not cues:
        return None
    term = cfg.get("sentence_terminators", ["。", "！", "？", ";", "；"])
    split_period = bool(cfg.get("split_ascii_period", True))
    best = None
    for rec in records:
        if rec["body"]["method"] == "missing" or rec["body"]["n_chars"] == 0:
            continue
        for fld, sent in _scan_sentences(rec, term, split_period):
            sn = norm(sent)
            hit = [c for c in cues if c in sn]
            if not hit:
                continue
            if best is None or len(hit) > len(best[2]):
                best = (rec, fld, sent, hit)
    if best is None:
        return None
    rec, fld, sent, hit = best
    row = _append_quote_row(rows, trace, use="premise_evidence", direction_id=direction_id, record=rec,
                            fld=fld, sent=sent, matched=hit, cov=rounded(len(hit) / max(1, len(cues))),
                            strength="premise", direction_rules=direction_rules)
    return row


# --------------------------------------------------------------------------------------
# E. 冲突（方向词典成对双列，方向域内共享检索键才比对）
# --------------------------------------------------------------------------------------
def _conflict_side(row: dict) -> dict:
    d = row.get("direction") or {}
    return {"evidence_id": row["evidence_id"], "record_id": row["record_id"],
            "source_file": row["source_file"], "quote": row["quote"],
            "quote_verified": row["quote_verified"],
            "direction": d.get("value"), "direction_terms": d.get("terms", []),
            "strength_terms": d.get("strength_terms", [])}


def detect_conflicts(rows: list, cfg: dict) -> list:
    """同候选方向 + 共享检索键（同主题）才比对：方向词典成对判定；数值冲突保留原口径。"""
    vocab = cfg.get("context_vocabulary", {})
    num = re.compile(r"(\d+(?:\.\d+)?)\s*(%|个百分点|ms|mM|mV|mAh/g|mg/dm2|MPa|kN/m)")
    raws = []
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i], rows[j]
            if a["direction_id"] != b["direction_id"]:
                continue
            shared = sorted(set(a["matched_terms"]) & set(b["matched_terms"]))
            if not shared:
                continue
            va = (a.get("direction") or {}).get("value")
            vb = (b.get("direction") or {}).get("value")
            polar = {"+1", "-1"}
            if va in polar and vb in polar and va != vb:
                raws.append({"type": "direction_conflict", "direction_id": a["direction_id"],
                             "shared_terms": shared, "a": a, "b": b,
                             "detail": f"同主题证据方向相反：{va} 与 {vb}（共享词 {shared}）",
                             "resolution": "并列双列呈现（side_a/side_b 各带逐字引文、方向与强度词），不择一、不合并、不以「尚存争议」替代双列；骑在该证据上的候选方向按 evidence_conflict 记风险并标注两侧引文"})
                continue
            if {va, vb} in ({"0", "+1"}, {"0", "-1"}):
                raws.append({"type": "zero_vs_direction", "direction_id": a["direction_id"],
                             "shared_terms": shared, "a": a, "b": b,
                             "detail": f"同一主题下一条为零效应（0）、另一条为方向效应（{vb if va == '0' else va}），口径差异待核",
                             "resolution": "零效应与方向效应并列双列，各自保留原文引文；差异写进选题风险评估，不择一"})
                continue
            if va == "mixed" or vb == "mixed":
                continue
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
                raws.append({"type": "numeric_conflict", "direction_id": a["direction_id"],
                             "shared_terms": shared, "a": a, "b": b,
                             "detail": f"同一口径下指标不一致：{hit[0][0]}{hit[0][1]} vs {hit[1][0]}{hit[1][1]}",
                             "resolution": "并列呈现，不择一写入结论；差异写进选题风险评估"})
    conflicts = []
    for c in raws:
        conflicts.append({"conflict_id": f"C{len(conflicts) + 1:02d}", "type": c["type"],
                          "direction_id": c["direction_id"], "shared_terms": c["shared_terms"],
                          "side_a": _conflict_side(c["a"]), "side_b": _conflict_side(c["b"]),
                          "detail": c["detail"], "resolution": c["resolution"]})
    return conflicts


# --------------------------------------------------------------------------------------
# F. 新颖性评估（novelty_scope=pool_observed，052 核心机器）
# --------------------------------------------------------------------------------------
def classify_claim(claim: str, novelty_cfg: dict) -> dict:
    text = claim.lower()
    scores, hits = {}, {}
    for tname, cfg in novelty_cfg.get("types", {}).items():
        cue_hits = [c for c in cfg.get("cues", []) if c.lower() in text]
        anti_hits = [c for c in cfg.get("anti_cues", []) if c.lower() in text]
        scores[tname] = len(cue_hits) - len(anti_hits)
        hits[tname] = {"cues": cue_hits, "anti_cues": anti_hits}
    best = max(scores.values()) if scores else 0
    winners = [k for k, v in scores.items() if v == best and v > 0]
    if len(winners) == 1:
        return {"type": winners[0], "type_label": novelty_cfg["types"][winners[0]].get("label", winners[0]),
                "type_confidence": "determined", "type_basis": hits[winners[0]]}
    return {"type": "undetermined", "type_label": "无法判定（触发词并列或全不命中）",
            "type_confidence": "undetermined", "type_basis": {"scores": scores, "hits": hits},
            "needs_review": True}


DIM_PATTERNS = {
    "method": r"(?:we (?:propose|apply|use|introduce|develop|train)|using|via|by)\s+([^.;]{6,80})",
    "data": r"([^.;]{0,50}(?:dataset|datasets|images|micrographs|trajectories|entries|cells|corpus|benchmark)[^.;]{0,50})",
    "evaluation": r"([^.;]{0,40}(?:RMSE|MAE|IoU|Dice|precision|recall|accuracy|AUC|calibration|hit rate|F1|leaderboard|efficiency|保持率)[^.;]{0,50})",
    "conclusion": r"(?:we (?:show|find|demonstrate|conclude)|results? show|here we show)\s+([^.;]{6,110})",
}


def infer_dims(record: dict) -> dict:
    """题录没给结构化维度时，用可复现的句式规则从摘要/正文抽取，并标注来源。"""
    text = " ".join(x for x in (record.get("abstract", ""), record.get("text", ""), record.get("title", "")) if x)
    out = {}
    for dim, pat in DIM_PATTERNS.items():
        m = re.search(pat, text, flags=re.I)
        if m:
            out[dim] = m.group(1).strip()[:160]
    out["task"] = record.get("title", "")[:160]
    return out


def _dim_side(direction: dict, dim: str) -> str:
    if dim == "task":
        return str(direction["fields"].get("object") or direction.get("title") or "")
    if dim == "method":
        return str(direction["fields"].get("method") or "")
    if dim == "data":
        return str(direction["fields"].get("data") or "")
    if dim == "evaluation":
        return str(direction["fields"].get("evaluation") or "")
    if dim == "conclusion":
        return str(direction["fields"].get("expected") or "")
    return ""


def _shared_side_terms(direction_side: str, prior_side: str) -> list:
    """共享词项：token 交集 + CJK 二元组在对方文本中的字面子串命中（跨分词边界不丢重合）。"""
    td, tp = set(tokens(direction_side)), set(tokens(prior_side))
    shared = set(td & tp)
    pn = norm(prior_side)
    for t in td:
        if len(t) >= 2 and re.search(r"[\u4e00-\u9fff]", t) and t in pn:
            shared.add(t)
    return sorted(shared)


def dim_overlap_points(direction: dict, record: dict) -> list:
    """五维逐维重合点：每维给状态 + 双方内容摘要 + 共享词，逐维给依据，不合并成一个数。"""
    prior_dims = {}
    inferred = infer_dims(record)
    for dim in ("task", "method", "data", "evaluation", "conclusion"):
        if record.get(dim):
            prior_dims[dim] = (record[dim], "explicit")
        elif inferred.get(dim):
            prior_dims[dim] = (inferred[dim], "title_as_task" if dim == "task" else "abstract_inferred")
    pts = []
    for dim in ("task", "method", "data", "evaluation", "conclusion"):
        dside = _dim_side(direction, dim)
        if dim not in prior_dims:
            pts.append({"dim": dim, "status": "prior_not_reported",
                        "direction_side": dside[:120] or None, "prior_side": None,
                        "shared_terms": []})
            continue
        pside, psrc = prior_dims[dim]
        if not dside.strip():
            pts.append({"dim": dim, "status": "direction_not_specified",
                        "direction_side": None, "prior_side": pside[:120],
                        "prior_dim_source": psrc, "shared_terms": []})
            continue
        shared = _shared_side_terms(dside, pside)
        pts.append({"dim": dim,
                    "status": "overlap" if shared else "no_overlap",
                    "direction_side": dside[:120], "prior_side": pside[:120],
                    "prior_dim_source": psrc, "shared_terms": shared[:8]})
    return pts


def pool_external_check(claim: str, direction: dict, novelty_cfg: dict) -> dict:
    """池外核验建议：检索式（主张 token 与方向键生成）+ 数据库建议；executed=false。"""
    cfg = novelty_cfg.get("pool_external_check", {})
    tt = sorted(set(tokens(claim)), key=lambda t: (-len(t), t))
    expr_terms = tt[:6] + [norm(k) for k in direction.get("keys", [])][:2]
    seen, exprs = set(), []
    for t in expr_terms:
        if t and t not in seen:
            seen.add(t)
            exprs.append(t)
    return {"executed": False,
            "search_expressions": [" OR ".join(exprs[:8])] if exprs else [],
            "suggested_databases": as_list(cfg.get("suggested_databases", [])),
            "declaration": cfg.get("declaration", "本轮未执行全局检索；池外是否存在同题工作未核验")}


def assess_claim_novelty(rows: list, trace: list, *, direction: dict, claim: dict, pool: list,
                         sim_cfg: dict, novelty_cfg: dict, cfg: dict, codes: dict,
                         direction_rules: dict) -> dict:
    """单条主张的池内新颖性判定：分类 → 覆盖比对 → 最近邻 + 逐维重合点 + 重合引文 → 池外建议。"""
    ct = set(tokens(claim["claim"]))
    min_tok = int(sim_cfg.get("min_claim_tokens", 5))
    partial = float(sim_cfg.get("partial_threshold", 0.25))
    covered = float(sim_cfg.get("covered_threshold", 0.5))
    pool_size = len(pool)
    base = {
        "claim_id": claim["claim_id"], "direction_id": direction["direction_id"],
        "claim": claim["claim"], "origin": claim.get("origin", ""),
        "scope": "pool_observed",
        "not_found_scope": "仅在本 case 记录池内比对；未联网检索，不能推断『无人研究』",
    }
    base.update(classify_claim(claim["claim"], novelty_cfg))
    if len(ct) < min_tok:
        base.update({"verdict": "undetermined", "max_similarity": 0.0, "nearest_prior": None,
                     "overlap_points": [], "pool_external_check": None,
                     "code": "R_SHORT_CLAIM",
                     "basis": f"主张 token 数 {len(ct)} < {min_tok}，无法稳定比对"})
        trace.append({"seq": 0, "tool": "novelty_compare",
                      "params": {"direction_id": direction["direction_id"], "claim_id": claim["claim_id"],
                                 "pool_size": pool_size, "claim_tokens": len(ct)},
                      "result": {"verdict": "undetermined", "code": "R_SHORT_CLAIM"}})
        return base
    if not pool:
        base.update({"verdict": "undetermined", "max_similarity": 0.0, "nearest_prior": None,
                     "overlap_points": [], "pool_external_check": None,
                     "code": "R_NO_POOL",
                     "basis": "无纳入记录，创新性判定无对照；不得据此声称新颖",
                     "not_found_scope": "无对比池；不得据此声称新颖"})
        trace.append({"seq": 0, "tool": "novelty_compare",
                      "params": {"direction_id": direction["direction_id"], "claim_id": claim["claim_id"],
                                 "pool_size": 0, "claim_tokens": len(ct)},
                      "result": {"verdict": "undetermined", "code": "R_NO_POOL"}})
        return base
    sims = []
    for r in pool:
        rt = set(tokens(record_text(r)))
        if not rt:
            continue
        sims.append((len(ct & rt) / len(ct), r))
    sims.sort(key=lambda x: (-x[0], x[1]["record_id"]))
    best_sim, nearest = (sims[0] if sims else (0.0, None))
    overlap_terms_all = sorted(ct & (set(tokens(record_text(nearest))) if nearest else set()))
    quote_ids = []
    if nearest is not None and best_sim > 0:
        row = build_novelty_overlap_quote(rows, trace, direction_id=direction["direction_id"],
                                          record=nearest,
                                          shared_terms=overlap_terms_all, cfg=cfg, novelty_cfg=novelty_cfg,
                                          direction_rules=direction_rules)
        if row is not None:
            quote_ids.append(row["evidence_id"])
    if claim.get("type") == "conclusion_correction":
        verdict, code = "undetermined", "R_DIRECTION"
        basis = ("结论修正类主张需判断与既有结论的方向关系（支持/反驳），词面相似不足以判定，"
                 "判 undetermined 并转人工复核")
    elif best_sim >= covered:
        verdict, code = "covered", "R_COVERED"
        basis = (f"与最近邻 {nearest['record_id']}《{nearest['title'][:40]}》覆盖度 {rounded(best_sim)} ≥ {covered}："
                 f"主张已被池内工作覆盖（scope=pool_observed）；重合词 {overlap_terms_all[:8]}")
    elif best_sim >= partial:
        verdict, code = "partially_covered", "R_PARTIAL"
        basis = (f"与最近邻 {nearest['record_id']}《{nearest['title'][:40]}》覆盖度 {rounded(best_sim)} "
                 f"∈ [{partial}, {covered})：主张被池内工作部分覆盖（scope=pool_observed）；"
                 f"需在比较矩阵中写明差异维度")
    else:
        verdict, code = "not_found_in_scope", "R_NOT_IN_SCOPE"
        tmpl = (novelty_cfg.get("novelty_scope", {}).get("not_found_template")
                or "{claim_id} 池内未见")
        basis = (tmpl.replace("{claim_id}", claim["claim_id"]).replace("{pool_size}", str(pool_size))
                 .replace("{sim}", str(rounded(best_sim))).replace("{threshold}", str(partial)))
    nearest_prior = None
    if nearest is not None and best_sim > 0:
        nearest_prior = {"record_id": nearest["record_id"], "title": nearest["title"],
                         "year": nearest["year"], "venue": nearest["venue"],
                         "similarity": rounded(best_sim),
                         "overlap_terms": overlap_terms_all[:8],
                         "quote_evidence_ids": quote_ids,
                         "no_overlap_quote": not quote_ids}
    trace.append({"seq": 0, "tool": "novelty_compare",
                  "params": {"direction_id": direction["direction_id"], "claim_id": claim["claim_id"],
                             "pool_size": pool_size, "claim_tokens": len(ct),
                             "thresholds": {"covered": covered, "partial": partial}},
                  "result": {"verdict": verdict, "code": code, "max_similarity": rounded(best_sim),
                             "nearest_record_id": nearest["record_id"] if nearest_prior else None,
                             "overlap_quote_evidence_ids": quote_ids}})
    base.update({
        "verdict": verdict, "code": code,
        "max_similarity": rounded(best_sim),
        "nearest_prior": nearest_prior,
        "overlap_points": (dim_overlap_points(direction, nearest) if nearest is not None and best_sim > 0
                           else []),
        "pool_external_check": (pool_external_check(claim["claim"], direction, novelty_cfg)
                                if verdict == "not_found_in_scope" else None),
        "basis": basis,
    })
    return base


# --------------------------------------------------------------------------------------
# G. 前提清单与条件式可行性（三分源 + 强度降级）
# --------------------------------------------------------------------------------------
def case_constraints(case: dict) -> dict:
    cons = {}
    for key in ("constraints", "resources", "conditions", "environment"):
        val = case.get(key)
        if isinstance(val, dict):
            cons.update(val)
    return cons


def premise_rows_for_direction(rows: list, trace: list, *, direction: dict, case_cons: dict,
                               premise_cfg: dict, scoring: dict, included_records: list,
                               cfg: dict, direction_rules: dict) -> list:
    """每前提三分源：task_given（case 约束）> record_evidence（池内证据，带 evidence_id）
    > explicit_assumption（无来源）。record_evidence 证据行写入证据表并可复核。"""
    cons = dict(case_cons)
    cons.update({k: v for k, v in direction.get("constraints", {}).items()})
    strength = scoring.get("premise_strength", {})
    out = []
    for p in premise_cfg.get("premises", []):
        row = {"direction_id": direction["direction_id"], "premise_id": p["id"], "label": p["label"],
               "statement": p["statement"], "source": None, "evidence_id": None,
               "basis": None, "supported": None,
               "risk_if_false": p["risk_if_false"], "risk_code": p.get("risk_code", "premise_unsupported")}
        hit_key, hit_val = None, None
        for k in p.get("check_keys", []):
            if k in cons and cons[k] not in (None, "", [], {}):
                hit_key, hit_val = k, cons[k]
                break
        if hit_key is not None:
            row.update({"source": "task_given", "basis": f"case.constraints.{hit_key} = {str(hit_val)[:120]}",
                        "supported": True,
                        "strength": float(strength.get("task_given", 1.0)),
                        "code": "R_TASK_GIVEN"})
            trace.append({"seq": 0, "tool": "premise_check",
                          "params": {"direction_id": direction["direction_id"], "premise_id": p["id"],
                                     "check_keys": p.get("check_keys", []), "evidence_cues": p.get("evidence_cues", [])},
                          "result": {"source": "task_given", "basis": row["basis"], "evidence_id": None}})
            out.append(row)
            continue
        ev = build_premise_evidence(rows, trace, direction_id=direction["direction_id"],
                                    premise=p, records=included_records, cfg=cfg,
                                    direction_rules=direction_rules)
        if ev is not None:
            row.update({"source": "record_evidence", "evidence_id": ev["evidence_id"],
                        "basis": f"池内证据 {ev['evidence_id']}（{ev['record_id']}）：{ev['quote'][:100]}；"
                                 f"命中 cue {ev['matched_terms']}（强度受限：池内证据≠任务承诺）",
                        "supported": True,
                        "strength": float(strength.get("record_evidence", 0.7)),
                        "code": "R_RECORD_EVIDENCE"})
            trace.append({"seq": 0, "tool": "premise_check",
                          "params": {"direction_id": direction["direction_id"], "premise_id": p["id"],
                                     "check_keys": p.get("check_keys", []), "evidence_cues": p.get("evidence_cues", [])},
                          "result": {"source": "record_evidence", "evidence_id": ev["evidence_id"],
                                     "record_id": ev["record_id"]}})
            out.append(row)
            continue
        row.update({"source": "explicit_assumption",
                    "basis": f"case 未给 {p.get('check_keys')} 中任何字段，池内证据也未命中 cue {p.get('evidence_cues', [])}",
                    "supported": False,
                    "strength": float(strength.get("explicit_assumption", 0.0)),
                    "code": "R_ASSUMPTION",
                    "needs_review": p["id"] in ("P_ETHICS", "P_DATA")})
        trace.append({"seq": 0, "tool": "premise_check",
                      "params": {"direction_id": direction["direction_id"], "premise_id": p["id"],
                                 "check_keys": p.get("check_keys", []), "evidence_cues": p.get("evidence_cues", [])},
                      "result": {"source": "explicit_assumption", "evidence_id": None}})
        out.append(row)
    return out


def feasibility_conclusion(premises: list, constraints_present: bool, premise_cfg: dict) -> dict:
    """条件式可行性结论：随前提强度降级；模板外置 premise_checklist.conditional_rule。"""
    labels = [p["label"] for p in premises]
    supported = [p for p in premises if p["source"] in ("task_given", "record_evidence")]
    assumptions = [p for p in premises if p["source"] == "explicit_assumption"]
    rules = premise_cfg.get("conditional_rule", {}).get("degradation", [])
    tmpl_by_level = {r.get("level"): r.get("template", "") for r in rules}
    if not constraints_present:
        level = "unconstrained"
        conclusion = (tmpl_by_level.get(level, "constraints 缺失")
                      .replace("{labels}", "、".join(labels) or "（无前提可核查）"))
    elif assumptions:
        level = "conditional"
        conclusion = (tmpl_by_level.get(level, "若 {assumption_ids} 成立则可行")
                      .replace("{assumption_ids}", "、".join(p["premise_id"] for p in assumptions))
                      .replace("{assumption_labels}", "、".join(p["label"] for p in assumptions))
                      .replace("{next_actions}", "；".join(p["risk_if_false"] for p in assumptions))
                      .replace("{labels}", "、".join(labels)))
        evid = [p for p in supported if p["source"] == "record_evidence"]
        if evid:
            conclusion += (f"；其中 {'、'.join(p['premise_id'] for p in evid)} 来自池内证据"
                           f"（{', '.join(p['evidence_id'] for p in evid)}），强度低于任务给定前提")
    else:
        level = "supported"
        conclusion = (tmpl_by_level.get(level, "在 {labels} 均有来源的前提下可行")
                      .replace("{labels}", "、".join(p["label"] for p in supported) or "（无前提）"))
        evid = [p for p in supported if p["source"] == "record_evidence"]
        if evid:
            conclusion += (f"；其中 {'、'.join(p['premise_id'] for p in evid)} 来自池内证据"
                           f"（{', '.join(p['evidence_id'] for p in evid)}），强度低于任务给定前提")
    return {"level": level, "conditional_conclusion": conclusion,
            "assumption_ids": [p["premise_id"] for p in assumptions],
            "supported_ids": [p["premise_id"] for p in supported]}


# --------------------------------------------------------------------------------------
# H. 价值 / 风险（冲突骑乘如实标注两侧引文）
# --------------------------------------------------------------------------------------
def assess_value(case: dict, direction: dict, claim_rows: list, cfg: dict) -> dict:
    verdicts = [c.get("verdict") for c in claim_rows]
    if any(v == "not_found_in_scope" for v in verdicts):
        gap_signal = 1.0
    elif verdicts and all(v == "covered" for v in verdicts):
        gap_signal = 0.0
    elif verdicts:
        gap_signal = 0.5
    else:
        gap_signal = 0.0
    app = direction.get("application")
    stakeholders = direction.get("stakeholders")
    if app is None:
        for key in ("constraints", "resources", "context"):
            if isinstance(case.get(key), dict):
                app = first_present(case[key], "application", "use_case", "应用场景", default=None)
                stakeholders = stakeholders or first_present(case[key], "stakeholders", "users", "决策方", default=None)
                if app is not None:
                    break
    if stakeholders or (isinstance(app, (list, dict)) and app):
        app_signal = 1.0
    elif isinstance(app, str) and app.strip():
        app_signal = 0.5
    else:
        app_signal = 0.0
    types = {c.get("type") for c in claim_rows}
    if types & {"method", "data"}:
        reuse = 1.0
    elif types & {"scenario_transfer", "conclusion_correction"}:
        reuse = 0.5
    else:
        reuse = 0.0
    value = clamp(0.5 * gap_signal + 0.3 * app_signal + 0.2 * reuse)
    return {
        "score": rounded(value),
        "signals": {"gap_signal": gap_signal, "application_signal": app_signal, "method_reuse_signal": reuse},
        "formula": cfg.get("dimensions", {}).get("value", {}).get("formula", ""),
        "basis": {"claim_verdicts": verdicts, "application_field_present": app is not None,
                  "claim_types": sorted(t for t in types if t)},
        "note": "价值分只反映『与池内工作的差距 + 是否有明确应用对象』，不代表真实社会或经济价值",
    }


def assess_risk(premises: list, claim_rows: list, conflicts: list, direction: dict, scoring: dict,
                codes: dict) -> dict:
    w = scoring.get("risk_weights", {})
    max_total = float(scoring.get("risk_max_total", 4.0))
    risks = []
    for row in premises:
        if row["source"] == "explicit_assumption":
            risks.append({"direction_id": direction["direction_id"],
                          "risk": f"{row['label']} 无来源（显式假设）",
                          "code": row.get("risk_code", "premise_unsupported"),
                          "weight": float(w.get(row.get("risk_code", "premise_unsupported"), 1.0)),
                          "impact": row["risk_if_false"],
                          "mitigation": f"在启动前补 {row['premise_id']} 的可得性证据（小样本试跑/书面确认）"})
    for c in claim_rows:
        if c.get("verdict") == "covered":
            risks.append({"direction_id": direction["direction_id"],
                          "risk": f"主张 {c['claim_id']} 已被池内工作覆盖（{c['nearest_prior']['record_id']}）",
                          "code": "claim_covered_by_prior_art",
                          "weight": float(w.get("claim_covered_by_prior_art", 1.0)),
                          "impact": f"最接近工作：{(c.get('nearest_prior') or {}).get('title', '')}",
                          "mitigation": "改述贡献边界（缩到未被覆盖的子问题）或补差异化实验"})
        if c.get("verdict") == "partially_covered":
            risks.append({"direction_id": direction["direction_id"],
                          "risk": f"主张 {c['claim_id']} 被池内工作部分覆盖",
                          "code": "scope_mismatch", "weight": float(w.get("scope_mismatch", 0.5)),
                          "impact": "需要明确与最近工作的差异维度（任务/方法/数据/评价）",
                          "mitigation": "在比较矩阵里补一列差异说明，并写进引言"})
    for c in conflicts:
        if c["direction_id"] != direction["direction_id"]:
            continue
        risks.append({"direction_id": direction["direction_id"],
                      "risk": f"候选方向骑在冲突证据上（{c['conflict_id']}，{c['type']}）",
                      "code": "evidence_conflict",
                      "weight": float(w.get("evidence_conflict", 0.8)),
                      "impact": c["detail"],
                      "side_a_quote": c["side_a"]["quote"],
                      "side_b_quote": c["side_b"]["quote"],
                      "evidence_ids": [c["side_a"]["evidence_id"], c["side_b"]["evidence_id"]],
                      "mitigation": "复核双方测试口径（材料/剂量/终点）后再作取舍；本轮已双列呈现，不仲裁"})
    total = sum(r["weight"] for r in risks)
    return {
        "score": rounded(clamp(total / max_total)),
        "items": risks,
        "weight_sum": rounded(total),
        "max_total": max_total,
        "note": "风险分高=失败风险大，与可行性分并置阅读，不并入单一总分；冲突条目两侧引文逐字保留",
    }


# --------------------------------------------------------------------------------------
# I. 有界建议（逐条引用 constraints；constraints 缺失只给方向级 + 无约束声明）
# --------------------------------------------------------------------------------------
def build_proposals(case: dict, directions: list, premise_rows: list, claim_rows: list,
                    conflicts: list) -> list:
    req = first_present(case, "proposal_request", "proposal", "bounded_proposal", default=None)
    cons = case_constraints(case)
    for d in directions:
        cons.update({k: v for k, v in d.get("constraints", {}).items()})
    constraints_present = bool(cons)
    if constraints_present:
        refs = [{"ref": k, "value": cons[k]} for k in sorted(cons)]
    else:
        refs = [{"ref": "unspecified",
                 "value": "case.json 未提供 constraints，建议不得假定任何额外预算/范围（无约束可依，只给方向级建议）"}]
    boundaries = ["不声称已完成实验", "不声称覆盖全局完备性", "不越过给定来源与约束范围"]
    boundaries += [str(b) for b in as_list((req or {}).get("boundaries", []))] if isinstance(req, dict) else []
    by_dir_claims = {}
    for c in claim_rows:
        by_dir_claims.setdefault(c["direction_id"], []).append(c)
    by_dir_prem = {}
    for p in premise_rows:
        by_dir_prem.setdefault(p["direction_id"], []).append(p)
    items = []
    for d in directions:
        did = d["direction_id"]
        scope_note = ("方向级建议（constraints 缺失，无约束可依，不得假定预算/范围）"
                      if not constraints_present else "约束内建议")
        for p in by_dir_prem.get(did, []):
            if p["source"] == "explicit_assumption":
                items.append({"proposal_id": f"PR{len(items) + 1:02d}", "direction_id": did,
                              "kind": "strengthen_premise",
                              "statement": f"{scope_note}：前提 {p['premise_id']}（{p['label']}）无来源，"
                                           f"启动前先补可得性证据（{p['risk_if_false']}）；"
                                           f"在证据补齐前该方向按条件式结论处理。",
                              "basis": {"premise_id": p["premise_id"], "source": p["source"],
                                        "evidence_ids": [], "conflict_ids": []},
                              "constraint_refs": refs, "not_claimed": boundaries})
        for c in by_dir_claims.get(did, []):
            if c.get("verdict") == "not_found_in_scope":
                pc = c.get("pool_external_check") or {}
                items.append({"proposal_id": f"PR{len(items) + 1:02d}", "direction_id": did,
                              "kind": "pool_external_verification",
                              "statement": f"{scope_note}：主张 {c['claim_id']} 为池内未见（pool_observed）；"
                                           f"建议按检索式「{(pc.get('search_expressions') or [''])[0]}」在 "
                                           f"{'、'.join(pc.get('suggested_databases', [])[:3])} 复核池外是否已有同题工作；"
                                           f"{pc.get('declaration', '')}",
                              "basis": {"claim_id": c["claim_id"], "verdict": c["verdict"],
                                        "evidence_ids": [], "conflict_ids": []},
                              "constraint_refs": refs, "not_claimed": boundaries})
            elif c.get("verdict") == "covered":
                items.append({"proposal_id": f"PR{len(items) + 1:02d}", "direction_id": did,
                              "kind": "revise_claim_boundary",
                              "statement": f"{scope_note}：主张 {c['claim_id']} 已被池内工作 "
                                           f"{(c.get('nearest_prior') or {}).get('record_id', '')} 覆盖，"
                                           f"建议改述贡献边界或补差异化实验后再启动。",
                              "basis": {"claim_id": c["claim_id"], "verdict": c["verdict"],
                                        "evidence_ids": (c.get("nearest_prior") or {}).get("quote_evidence_ids", []),
                                        "conflict_ids": []},
                              "constraint_refs": refs, "not_claimed": boundaries})
        for cf in conflicts:
            if cf["direction_id"] != did:
                continue
            items.append({"proposal_id": f"PR{len(items) + 1:02d}", "direction_id": did,
                          "kind": "recheck_conflict",
                          "statement": f"{scope_note}：证据 {cf['side_a']['evidence_id']} 与 "
                                       f"{cf['side_b']['evidence_id']} 方向不一致（{cf['conflict_id']}）；"
                                       f"该方向骑在冲突证据上，启动前先复核双方测试口径，本轮已双列呈现、不择一。",
                          "basis": {"claim_id": "", "verdict": "",
                                    "evidence_ids": [cf["side_a"]["evidence_id"], cf["side_b"]["evidence_id"]],
                                    "conflict_ids": [cf["conflict_id"]]},
                          "constraint_refs": refs, "not_claimed": boundaries})
    return items[:12]


# --------------------------------------------------------------------------------------
# J. 先验工作五维比较矩阵（显式维度优先，缺失标不可比）
# --------------------------------------------------------------------------------------
def prior_art_matrix(pool: list, claim_rows: list, limit: int = 12) -> dict:
    claim_tokens = [set(tokens(c["claim"])) for c in claim_rows]
    rows = []
    for r in pool:
        inferred = infer_dims(r)
        dims_row, dim_src = {}, {}
        for dim in ("task", "method", "data", "evaluation", "conclusion"):
            if r.get(dim):
                dims_row[dim], dim_src[dim] = r[dim], "explicit"
            elif inferred.get(dim):
                dims_row[dim], dim_src[dim] = inferred[dim], ("title_as_task" if dim == "task" else "abstract_inferred")
            else:
                dims_row[dim], dim_src[dim] = "未报告", "missing"
        base = {"record_id": r["record_id"], "title": r["title"], "year": r["year"], "venue": r["venue"],
                **dims_row, "dimension_sources": dim_src}
        rt = set(tokens(record_text(r)))
        best = 0.0
        for ct in claim_tokens:
            if ct:
                best = max(best, len(rt & ct) / len(ct))
        missing_dims = [k for k in ("method", "data", "evaluation", "conclusion") if base[k] == "未报告"]
        if missing_dims:
            base["directly_comparable"] = False
            base["not_comparable_reason"] = f"题录未提供 {missing_dims}，无法逐维对齐"
        elif best >= 0.25:
            base["directly_comparable"] = True
            base["not_comparable_reason"] = None
        else:
            base["directly_comparable"] = False
            base["not_comparable_reason"] = f"与任一创新主张 claim_coverage {rounded(best)} < 0.25，不构成同类工作"
        base["similarity_to_best_claim"] = rounded(best)
        rows.append(base)
    rows.sort(key=lambda x: (-x["similarity_to_best_claim"], x["record_id"]))
    kept = rows[:limit]
    return {
        "dimensions": ["task", "method", "data", "evaluation", "conclusion"],
        "rows": kept,
        "directly_comparable_count": sum(1 for r in kept if r["directly_comparable"]),
        "truncated": max(0, len(rows) - limit),
        "note": "维度来源逐格标注：explicit=题录直接给出；abstract_inferred=按句式规则抽取（可能不完整）；"
                "title_as_task=题名近似任务；missing=未报告。不可直接比较的原因逐条给出，不推测内容。",
    }


# --------------------------------------------------------------------------------------
# 契约绑定与输入表述自检
# --------------------------------------------------------------------------------------
def bind_contract(res: Result, payload: dict, routing: dict):
    rules = routing.get("rules", [])
    virtual = {"evidence": res.evidence_view, "status": lambda: res.status, "uncertainty": lambda: res.uncertainty}
    for key in list(res.contract.keys()):
        if key in res.fields:
            continue
        kn = re.sub(r"[_\s]", "", norm(key))
        target = None
        for rule in rules:
            if any(re.sub(r"[_\s]", "", norm(kw)) in kn for kw in rule.get("match", [])):
                target = rule.get("target")
                break
        if target in payload:
            res.set(key, payload[target])
            res.note(f"契约字段 {key} 绑定自产物分区 {target}")
        elif target in virtual:
            res.set(key, virtual[target]())
            res.note(f"契约字段 {key} 绑定自三段式 {target}")
        else:
            res.unmet(key, "该合同字段名未命中 rules/contract_routing.json，未以推测值填充")


def check_input_phrasing(directions: list, novelty_cfg: dict) -> list:
    """只检查用户输入的方向文本与主张是否含全局首创类表述；命中的是输入事实，登记 uncertainty。"""
    phrases = [norm(p) for p in (novelty_cfg.get("novelty_scope", {}).get("global_claim_phrases", [])
                                 + novelty_cfg.get("coverage_check", {}).get("forbid_phrases", [])) if norm(p)]
    hits = []
    for d in directions:
        blob = norm(d.get("title", "") + " " + d.get("text", ""))
        for c in as_list(d.get("claims_raw")):
            blob += " " + norm(c.get("claim", "") if isinstance(c, dict) else str(c))
        for p in phrases:
            if p in blob:
                hits.append({"direction_id": d["direction_id"], "phrase": p})
    return hits


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    case = normalize_case(case)
    rules = load_rules()
    scoring, novelty_cfg, premise_cfg = rules["scoring"], rules["novelty"], rules["premise"]
    codes, cfg = rules["codes"], rules["protocol"]
    direction_rules = rules["direction"]
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res.contract))
    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).parent
    bases = path_bases(work, case_dir)

    sources = collect_sources(case, bases)
    records = collect_records(case, bases, sources)
    dropped = []
    records = dedupe(records, dropped, codes)
    directions = parse_directions(case, task_text)
    res.log("intake", sources=len(sources), records=len(records), directions=len(directions))

    if not directions and not records:
        res.set("candidate_assessments", [])
        res.set("evidence_table", [])
        res.status["overall"] = "empty"
        res.unmet("候选方向与先验池", "case.json 与 task.md 均未给出候选选题方向或记录池")
        res.note("补齐 candidate_directions 与 records 后重跑；未做任何推断")
        res.done("盘点")
        return res

    sim_cfg = scoring.get("similarity", {})
    protocol_block = build_protocol_block(case, cfg)
    constraints = case_constraints(case)

    # ---- B. 前瞻协议：先落盘计划文件，再逐条筛选 ----
    plan = build_plan(directions)
    plan_doc = {"case_id": res.case_id, "skill": SKILL,
                "directions": [{"direction_id": d["direction_id"], "title": d["title"],
                                "keys": d["keys"], "claims": len(as_list(d.get("claims_raw") or []))}
                               for d in directions],
                "plan": plan, "protocol": protocol_block, "constraints": constraints,
                "screening_order": [r["record_id"] for r in records],
                "prospective_note": "本文件先于筛选、检索、新颖性比对与打分落盘（prospective）；"
                                    "执行明细见 result.json 的 evidence.execution"}
    artifacts = []
    for _label, base in (("work", work), ("out", Path(args.out))):
        try:
            write_json(base / "plan.json", plan_doc)
            artifacts = ["plan.json"]
            break
        except OSError:
            continue  # work 不可写时静默回退到 out；位置差异不写入结果，保证跨环境字节一致
    if not artifacts:
        res.note("plan.json 落盘失败，计划仍完整保存在 result.json 的 evidence.protocol 中")
    res.log("plan_written", artifacts=artifacts)

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
    pool = [r for r in records if r["record_id"] in included_ids]
    rec_by_id = {r["record_id"]: r for r in pool}
    res.log("screen", protocol=protocol_block["protocol_status"], **screen_counts)

    # ---- C. 逐方向检索与证据表（冲突只在 retrieval 行内成对判定） ----
    evidence_by_dir = execute_plan(plan, pool, cfg, trace)
    ret = build_retrieval_evidence(plan, evidence_by_dir, rec_by_id, cfg, trace, direction_rules)
    rows = ret["rows"]
    conflicts = detect_conflicts([r for r in rows if r["use"] == "retrieval"], cfg)

    # ---- D/E/F. 逐方向评估（新颖性 / 前提 / 价值 / 风险 / 建议） ----
    all_claim_rows, all_premise_rows, candidate_assessments = [], [], []
    evidence_ids_by_dir = {d["direction_id"]: list(ret["by_direction"].get(d["direction_id"], []))
                           for d in directions}
    for d in directions:
        did = d["direction_id"]
        d_claims = extract_claims(d, codes)
        claim_rows = [assess_claim_novelty(rows, trace, direction=d, claim=c, pool=pool,
                                           sim_cfg=sim_cfg, novelty_cfg=novelty_cfg, cfg=cfg, codes=codes,
                                           direction_rules=direction_rules)
                      for c in d_claims]
        premise_rows = premise_rows_for_direction(rows, trace, direction=d, case_cons=constraints,
                                                  premise_cfg=premise_cfg, scoring=scoring,
                                                  included_records=pool, cfg=cfg,
                                                  direction_rules=direction_rules)
        feas = feasibility_conclusion(premise_rows, bool(constraints) or bool(d.get("constraints")), premise_cfg)
        strength_sum = rounded(sum(p["strength"] for p in premise_rows))
        feasibility = {
            "direction_id": did,
            "premises": premise_rows,
            "level": feas["level"],
            "conditional_conclusion": feas["conditional_conclusion"],
            "assumption_ids": feas["assumption_ids"],
            "supported_ids": feas["supported_ids"],
            "supported_ratio": rounded(len([p for p in premise_rows if p["source"] != "explicit_assumption"])
                                       / max(1, len(premise_rows))),
            "score": rounded(strength_sum / max(1, len(premise_rows))),
            "score_basis": {"strength_sum": strength_sum, "total": len(premise_rows),
                            "rule": scoring.get("dimensions", {}).get("feasibility", {}).get("formula", "")},
            "constraints_present": bool(constraints) or bool(d.get("constraints")),
            "note": premise_cfg.get("conditional_rule", {}).get("note",
                   premise_cfg.get("reporting", {}).get("note", "")),
        }
        det = [c for c in claim_rows if c["verdict"] != "undetermined"]
        mean_sim = rounded(sum(c["max_similarity"] for c in det) / len(det)) if det else None
        novelty_score = rounded(clamp(1.0 - mean_sim)) if mean_sim is not None else None
        band = scoring.get("verdict_bands", {})
        verdict_counts = {v: sum(1 for c in claim_rows if c["verdict"] == v)
                          for v in ("covered", "partially_covered", "not_found_in_scope", "undetermined")}
        novelty = {
            "direction_id": did, "scope": "pool_observed",
            "scope_statement": novelty_cfg.get("novelty_scope", {}).get(
                "scope_statement", "新颖性判定只在 case 记录池内（pool_observed）"),
            "claims": claim_rows,
            "verdict_counts": verdict_counts,
            "mean_max_similarity": mean_sim,
            "score": novelty_score,
            "band": (None if novelty_score is None else
                     ("强" if novelty_score >= band.get("strong", 0.7)
                      else "中" if novelty_score >= band.get("moderate", 0.45) else "弱")),
            "pool_size": len(pool),
            "formula": scoring.get("dimensions", {}).get("novelty", {}).get("formula", ""),
        }
        value = assess_value(case, d, claim_rows, scoring)
        risk = assess_risk(premise_rows, claim_rows, conflicts, d, scoring, codes)
        all_claim_rows.extend(claim_rows)
        all_premise_rows.extend(premise_rows)
        evidence_ids_by_dir[did] = evidence_ids_by_dir[did] + [
            r["evidence_id"] for r in rows
            if r["direction_id"] == did and r["use"] in ("novelty_overlap", "premise_evidence")]
        candidate_assessments.append({
            "direction_id": did, "title": d["title"], "source": d["source"],
            "problem_statement": {
                "title": d["title"], "object": d["fields"].get("object"),
                "key_variables": d["fields"].get("variables"),
                "baseline": d["fields"].get("baseline"),
                "expected_conclusion": d["fields"].get("expected"),
                "falsification_condition": d["fields"].get("falsification"),
                "method": d["fields"].get("method"), "data": d["fields"].get("data"),
                "evaluation": d["fields"].get("evaluation"),
                "missing_fields": d["missing_fields"],
                "completeness": rounded((len(REQUIRED_FIELDS) - len(d["missing_fields"])) / len(REQUIRED_FIELDS)),
                "source": d["source"],
                "note": "缺失字段不猜：由 Agent 在 report 中向用户回问，或由用户补 case 字段后重跑",
            },
            "novelty": novelty, "feasibility": feasibility, "value": value, "risk": risk,
            "four_dimension": None,  # 装配于下
        })
        res.log("assess_direction", direction_id=did, claims=len(claim_rows), premises=len(premise_rows))

    proposals = build_proposals(case, directions, all_premise_rows, all_claim_rows, conflicts)

    # 四维并置（不合成单一总分）+ 冲突入风险后重算 risk.score 已在 assess_risk 内完成
    dims_cfg = scoring.get("dimensions", {})
    for ca in candidate_assessments:
        ca["four_dimension"] = {
            "novelty": {"score": ca["novelty"]["score"], "band": ca["novelty"]["band"],
                        "verdict_counts": ca["novelty"]["verdict_counts"],
                        "basis": f"mean_max_similarity={ca['novelty']['mean_max_similarity']}（pool_observed）"},
            "feasibility": {"score": ca["feasibility"]["score"], "level": ca["feasibility"]["level"],
                            "supported_ratio": ca["feasibility"]["supported_ratio"],
                            "conclusion": ca["feasibility"]["conditional_conclusion"]},
            "value": {"score": ca["value"]["score"], "signals": ca["value"]["signals"]},
            "risk": {"score": ca["risk"]["score"], "weight_sum": ca["risk"]["weight_sum"]},
            "weights": {k: v.get("weight") for k, v in dims_cfg.items()},
            "note": "四维并置，不合成单一总分：新颖性与可行性的取舍必须由决策者看到原始计数后再定",
        }

    matrix = prior_art_matrix(pool, all_claim_rows)
    det_all = [c for c in all_claim_rows if c["verdict"] != "undetermined"]
    global_counts = {v: sum(1 for c in all_claim_rows if c["verdict"] == v)
                     for v in ("covered", "partially_covered", "not_found_in_scope", "undetermined")}

    problem_statement = {
        "directions": [{"direction_id": ca["direction_id"], **{k: v for k, v in ca["problem_statement"].items()
                                                               if k != "note"}} for ca in candidate_assessments],
        "note": candidate_assessments[0]["problem_statement"]["note"] if candidate_assessments else "",
    }
    novelty_assessment = {
        "scope": "pool_observed",
        "scope_statement": novelty_cfg.get("novelty_scope", {}).get("scope_statement", ""),
        "pool_size": len(pool),
        "claims": all_claim_rows,
        "verdict_counts": global_counts,
        "mean_max_similarity": rounded(sum(c["max_similarity"] for c in det_all) / len(det_all)) if det_all else None,
        "score": (rounded(clamp(1.0 - rounded(sum(c["max_similarity"] for c in det_all) / len(det_all))))
                  if det_all else None),
        "not_found_scope": "仅在本 case 记录池内比对；未联网检索，不能推断『无人研究』",
    }
    feasibility_assessment = {
        "premises": all_premise_rows,
        "per_direction": [{"direction_id": ca["direction_id"], "level": ca["feasibility"]["level"],
                           "conditional_conclusion": ca["feasibility"]["conditional_conclusion"],
                           "supported_ratio": ca["feasibility"]["supported_ratio"],
                           "score": ca["feasibility"]["score"],
                           "constraints_present": ca["feasibility"]["constraints_present"],
                           "assumption_ids": ca["feasibility"]["assumption_ids"]}
                          for ca in candidate_assessments],
        "rule": premise_cfg.get("conditional_rule", {}).get("note", ""),
    }
    value_assessment = {"per_direction": [{"direction_id": ca["direction_id"], "score": ca["value"]["score"],
                                           "signals": ca["value"]["signals"]} for ca in candidate_assessments],
                        "formula": dims_cfg.get("value", {}).get("formula", "")}
    risk_assessment = {"items": [r for ca in candidate_assessments for r in ca["risk"]["items"]],
                       "per_direction": [{"direction_id": ca["direction_id"], "score": ca["risk"]["score"],
                                          "weight_sum": ca["risk"]["weight_sum"]} for ca in candidate_assessments],
                       "rule": dims_cfg.get("risk", {}).get("formula", "")}
    four_dim = {"directions": [{"direction_id": ca["direction_id"], **ca["four_dimension"]}
                               for ca in candidate_assessments],
                "note": "四维并置，不合成单一总分：新颖性与可行性的取舍必须由决策者看到原始计数后再定"}
    decision_package = {
        "per_direction": [{
            "direction_id": ca["direction_id"],
            "可以启动的前提": [p["premise_id"] for p in ca["feasibility"]["premises"]
                          if p["source"] in ("task_given", "record_evidence")],
            "启动前必须补齐": [p["premise_id"] for p in ca["feasibility"]["premises"]
                          if p["source"] == "explicit_assumption"],
            "必须先改的表述": [c["claim_id"] for c in ca["novelty"]["claims"] if c["verdict"] == "covered"],
            "必须先复核的冲突": sorted({r.get("impact", "") for r in ca["risk"]["items"]
                                  if r.get("code") == "evidence_conflict"}),
        } for ca in candidate_assessments],
        "note": "决策包逐方向给出：可启动前提 / 启动前必须补齐 / 必须先改的表述 / 必须先复核的冲突",
    }

    # ---- G. 状态与不确定性 ----
    unscored = [r for r in screening_rows if r["outcome"] == "unscored_no_body"]
    mismatched = [r for r in rows if not r["quote_verified"]]
    if protocol_block["input_gap"]:
        res.unmet("筛选协议", f"{protocol_block['input_gap']['code']} — {protocol_block['input_gap']['detail']}；"
                             f"已按声明回退口径筛选（fallback_screening），不冒充已按协议筛选")
    if unscored:
        res.unmet("记录正文", f"{len(unscored)} 条记录正文不可读（{','.join(r['record_id'] for r in unscored)}），"
                             f"已按 unscored_no_body 诚实降级，不参与纳排与比对，未编造其内容")
    if mismatched:
        res.unmet("证据引文校验", f"{len(mismatched)} 条证据 quote 未通过精确子串校验，已在行内标 quote_mismatch，不静默")
    if not pool:
        res.unmet("先验工作池", codes.get("R_NO_POOL", "无纳入记录，创新性判定无对照"))
        res.unsure("新颖性", "无池内对照时任何新颖性结论都不成立；不得据此声称新颖")
    undet = [c["claim_id"] for c in all_claim_rows if c["verdict"] == "undetermined"]
    if undet:
        res.unmet("创新点判定", f"{undet} 判 undetermined（主张过短或结论修正类需方向判断），需人工复核")
    covered = [c["claim_id"] for c in all_claim_rows if c["verdict"] == "covered"]
    if covered:
        res.unsure("创新性", f"{covered} 已被池内工作覆盖：需改述贡献边界，不能按原表述投稿")
    for ca in candidate_assessments:
        if ca["feasibility"]["assumption_ids"]:
            res.unsure("可行性", f"方向 {ca['direction_id']} 前提 {ca['feasibility']['assumption_ids']} 为显式假设，"
                                 f"结论只能条件式成立（若 P 成立则…）")
        if not ca["feasibility"]["constraints_present"]:
            res.unsure("可行性", f"方向 {ca['direction_id']} 无 constraints 可依：判 unconstrained，"
                                 f"建议只能给到方向级，不给『可行』结论")
        for p in ca["feasibility"]["premises"]:
            if p["source"] == "record_evidence":
                res.note(f"方向 {ca['direction_id']} 前提 {p['premise_id']} 来自池内证据（{p['evidence_id']}），"
                         f"强度低于任务给定前提")
    for c in conflicts:
        res.note(f"方向 {c['direction_id']} 存在{c['type']}，已双列呈现（{c['side_a']['evidence_id']} × "
                 f"{c['side_b']['evidence_id']}，conflict_id={c['conflict_id']}），风险条目标注两侧引文")
        res.unsure(f"冲突:{c['conflict_id']}", f"{c['detail']}；双方口径差异未仲裁，以双列并列交付")
    phrase_hits = check_input_phrasing(directions, novelty_cfg)
    if phrase_hits:
        res.unsure("表述越界", f"输入方向文本含全局首创/无人研究类表述 {phrase_hits}；"
                              f"输出已统一改写为「池内未见（pool_observed），不构成全球首创判定」")
    res.unsure("文献覆盖", "未联网：新颖性只在 case 记录池内判定（pool_observed），池外同题工作可能已存在；"
                           "not_found_in_scope 已附池外核验建议（未执行）")
    res.unsure("相似度口径", "token 覆盖率对同义改写不敏感；跨语言工作可能被低估相似度")

    if not directions:
        res.status["overall"] = "empty"
    elif not pool:
        res.status["overall"] = "partial"
    else:
        res.status["overall"] = "complete"
    res.done("来源/记录双结构摄入", "候选方向字段化", "前瞻协议落盘", "逐条筛选留痕", "逐方向检索取证",
             "证据表与引文校验", "池内新颖性评估（pool_observed）", "前提三分源与条件式可行性",
             "冲突双列与风险登记", "价值与风险并置", "四维评分与决策包", "有界研究建议")

    seq = 0
    for call in trace:
        seq += 1
        call["seq"] = seq
    res.log("execute", tool_calls=len(trace), evidence=len(rows), conflicts=len(conflicts),
            proposals=len(proposals))

    evidence_ids_flat = sorted({e for ids in evidence_ids_by_dir.values() for e in ids})
    resolved_ids = {r["evidence_id"] for r in rows}
    premise_refs = sorted({p["evidence_id"] for p in all_premise_rows if p["evidence_id"]})
    novelty_refs = sorted({eid for c in all_claim_rows for eid in
                           ((c.get("nearest_prior") or {}).get("quote_evidence_ids") or [])})
    payload = {
        "candidate_assessments": candidate_assessments,
        "problem_statement": problem_statement,
        "prior_art_matrix": matrix,
        "novelty_assessment": novelty_assessment,
        "feasibility_assessment": feasibility_assessment,
        "value_assessment": value_assessment,
        "risk_assessment": risk_assessment,
        "four_dimension_assessment": four_dim,
        "decision_package": decision_package,
        "evidence_table": rows,
        "screening": {"protocol_status": protocol_block["protocol_status"],
                      "protocol_id": protocol_block["protocol_id"],
                      "prospective": protocol_block["prospective"],
                      "order_note": "先落盘协议与计划（plan.json），后对每条记录逐条筛选并记 criterion_hits；"
                                    "仅 included 记录进入检索、新颖性比对与前提取证",
                      "counts": screen_counts, "records": screening_rows},
        "conflicts": {"count": len(conflicts), "items": conflicts,
                      "resolution": rules["direction"].get("pair_rules", {}).get("resolution",
                                 cfg.get("conflict_rule", "")),
                      "note": "count=0 表示本轮同方向证据无方向/数值冲突，非未检查"},
        "proposals": {"requested": bool(first_present(case, "proposal_request", "proposal",
                                                      "bounded_proposal", default=None)),
                      "count": len(proposals), "items": proposals,
                      "rule": cfg.get("proposal_rule", ""),
                      "note": "逐条引用 constraints 且 not_claimed；constraints 缺失时只给方向级建议并声明无约束可依"},
    }
    for key, value in payload.items():
        res.set(key, value)

    # ---- 证据记录（三段式） ----
    res.evidence["sources_manifest"] = {"sources": sources, "count": len(sources),
                                        "note": "来源文件清单与解析结果；记录通过 source_path/source_id 指到来源文件"}
    res.evidence["protocol"] = protocol_block
    res.evidence["screening"] = {"counts": screen_counts, "rule": cfg.get("screening_rule", ""),
                                 "fallback": protocol_block["fallback"],
                                 "input_gap": protocol_block["input_gap"]}
    res.evidence["execution"] = {
        "tool_calls": trace,
        "corpus": [{"record_id": r["record_id"], "title": r["title"], "year": r["year"], "type": r["type"],
                    "source": r["source"] or "local-corpus", "extraction_method": r["body"]["method"],
                    "included": r["record_id"] in included_ids} for r in records],
        "recompute": "record_score = 0.5·min(bm25,1) + 0.5·idf_coverage(keys)；claim_coverage = "
                     "|claim_tokens ∩ record_tokens| / |claim_tokens|；每条 tool_call 的 params 可原样重放",
    }
    res.evidence["evidence_table_map"] = {
        "rows": len(rows),
        "per_direction": {k: v for k, v in sorted(evidence_ids_by_dir.items())},
        "use_counts": {u: sum(1 for r in rows if r["use"] == u)
                       for u in sorted({r["use"] for r in rows})},
        "quote_rule": "证据句必须是记录正文的逐字整句；source_file + char_span（按 quote_field 字段内字符区间）"
                      "+ 精确子串校验；binary 来源（PDF/DOCX）以 body_sha256 锚定提取文本",
    }
    res.evidence["novelty"] = {
        "scope": novelty_assessment["scope"], "pool_size": len(pool),
        "rule": cfg.get("novelty_rule", ""),
        "nearest_neighbor_rule": novelty_cfg.get("nearest_neighbor", {}).get("rule", ""),
        "referenced_quote_ids": novelty_refs,
        "unresolved_quote_ids": [e for e in novelty_refs if e not in resolved_ids],
    }
    res.evidence["premises"] = {
        "rule": cfg.get("premise_rule", ""),
        "source_priority": premise_cfg.get("source_priority", []),
        "strength": scoring.get("premise_strength", {}),
        "referenced_evidence_ids": premise_refs,
        "unresolved_evidence_ids": [e for e in premise_refs if e not in resolved_ids],
    }
    res.evidence["backlink"] = {
        "rule": "回链：前提 record_evidence 与新颖性重合引文引用的 evidence_id 必须能在证据表解析且 quote 校验通过",
        "referenced_ids": sorted(set(premise_refs) | set(novelty_refs)),
        "unresolved_ids": sorted({e for e in premise_refs + novelty_refs if e not in resolved_ids}),
        "quote_verified_rows": len([r for r in rows if r["quote_verified"]]),
        "quote_rows_total": len(rows),
    }
    res.evidence["conflicts"] = {"rule": cfg.get("conflict_rule", ""), "items": conflicts}
    res.evidence["proposals"] = {"requested": payload["proposals"]["requested"],
                                 "rule": cfg.get("proposal_rule", ""), "items": proposals}
    res.evidence["exclusions"] = dropped
    res.evidence["artifacts"] = artifacts
    res.evidence["plan_file"] = {"file": "plan.json", "prospective": True,
                                 "note": "协议/方向/筛选顺序先于筛选与打分落盘（优先 work 目录，不可写时回退 out 目录）"}
    res.evidence["scope"] = {"sources": ["local-corpus"], "novelty_scope": "pool_observed",
                             "note": "本轮只在给定记录池内检索与比对；不联网、不虚构工具调用；"
                                     "novelty_scope=pool_observed：池内未见≠全球首创"
                                     + (f"；{case['synthetic_notice']}" if case.get("synthetic_notice") else "")}
    res.evidence["similarity"] = sim_cfg
    res.evidence["rules"] = {"dimensions": dims_cfg, "risk_weights": scoring.get("risk_weights", {}),
                             "premise_ids": [p["id"] for p in premise_cfg.get("premises", [])],
                             "premise_strength": scoring.get("premise_strength", {}),
                             "verdict_bands": scoring.get("verdict_bands", {})}

    # 契约绑定统一走 bind_contract（keyword 路由 + 虚拟三段式）；不走 res.bind，
    # 避免 bind 的 leftover-逻辑把整个 payload 重复挂到 result 字段。
    bind_contract(res, payload, rules["routing"])
    return res


def _write_input_error(args, exc: Exception) -> int:
    payload = {
        "case_id": "UNKNOWN-CASE",
        "skill": {"name": SKILL, "version": SKILL_VERSION},
        "candidate_assessments": [],
        "研究产物": {"status": "not_produced", "reason": str(exc)},
        "evidence": {"error": f"{type(exc).__name__}: {exc}"},
        "status": {"overall": "input_error", "completed": [],
                   "unmet": [{"item": "case.json", "reason": str(exc)}],
                   "notes": ["输入不可读时立即失败并保留错误原文"]},
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
        print(json.dumps({"case_id": payload["case_id"], "status": payload["status"]["overall"]},
                         ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    try:
        from render_report import render_file
        render_file(path, Path(args.out) / "report.md")
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"report_warning": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
    novelty = payload.get("novelty_assessment", {})
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "directions": len(payload.get("candidate_assessments", [])),
                      "screened": payload.get("screening", {}).get("counts", {}).get("screened", 0),
                      "evidence": len(payload.get("evidence_table", [])),
                      "conflicts": payload.get("conflicts", {}).get("count", 0),
                      "proposals": payload.get("proposals", {}).get("count", 0),
                      "novelty_scope": novelty.get("scope")}, ensure_ascii=False))
    try:
        _extra = _synthesis_hooks(args, path, payload)
        if _extra:
            print(json.dumps({"synthesis": _extra}, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
