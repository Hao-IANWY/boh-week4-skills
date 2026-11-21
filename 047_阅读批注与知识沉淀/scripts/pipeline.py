#!/usr/bin/env python3
"""047 阅读批注与知识沉淀 —— 主流程（纯标准库，离线、确定性、可复核）。

输入：case.json（两种材料形态皆支持）+ task.md
  A. 文件库模式：case 给出 materials_root / library_path 等库目录（或带路径的文件清单）
     → 扫描目录 → 逐文件 SHA-256+大小+类型 → 同哈希不同名识别别名/完全重复 →
     同归一化标题提示近重复 → 按管理性版本说明（version_notes/admin_notes/versions
     或 metadata.version / 文件名模式）分版本组。
  B. 内联模式：materials/files/documents 直接给正文（pages[]/text），兼容旧版行为。

文本获取链（逐文件记录 extraction_method，全部失败则诚实 gap，绝不猜）：
  内联正文 → 侧车 .txt/.ocr.txt → pdf_min 提取 PDF 文本层 → zip+xml 读 DOCX 段落
  → 纯文本直读 → gap（加密 /ENCRYPT、损坏、无提取器、无文本层分别记码）。
  侧车可带 .ocr.json（engine/mean_confidence/low_confidence_spans）驱动 OCR 降级。

六步闭环：
  1. 文件库盘点：哈希清单、别名/重复、近重复提示、版本组、输入只读完整性核验
  2. 批注抽取：类型归一（高亮/划线/页边/手写分开）、颜色语义按表解释（未知色不猜）
  3. 锚点校正：引文逐字定位（verified/page_mismatch/ambiguous/not_found/unverifiable
     /fuzzy_matched 六态）+ char_span 自检 + 双栏阅读顺序提示 + OCR 低置信降级；
     找不到先模糊匹配（如实标 fuzzy，不冒充逐字），仍无则 not_found 不硬挂
  4. 笔记结构化：维度线索词归维（可多维度），保留 annotation_id、原文片段、
     来源文件哈希/提取方式/版本；版本串味给出「旧版本批注」提示
  5. 知识关联：共享 ≥N 非平凡术语或同引文（跨版本/重复文件）才连边，抑制过度连接
  6. 检索闭环：BM25 召回 + 逐问题摘录式回答 + 原文回链 + 召回不足说明；
     恢复操作只出建议（ops_log），全程不改写输入文件，前后哈希对照留证；
     产出 handoff_manifest.json/.docx 交接清单到输出目录。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import unescape as _xml_unescape

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
    norm_title,
    parse_args,
    read_text,
    rounded,
    sha256_text,
    stable_id,
    tokens,
)
import office_min  # noqa: E402
import pdf_min  # noqa: E402

SKILL = "annotation-knowledge-distiller"
RULES = Path(__file__).resolve().parent.parent / "rules"
TEXT_EXTS = {"md", "txt", "markdown", "json", "csv", "tex", "log", "html", "htm"}
DOC_EXTS = {"docx"}
LIBRARY_KEYS = ("materials_root", "library_path", "library", "library_dir", "library_root", "material_root")
VERSION_NOTE_KEYS = ("version_notes", "admin_notes", "versions")
_VERSION_TOKEN = re.compile(r"(draft|final|v(?:er|ersion)?[._ ]?(\d+)|rev[._ ]?(\d+)|latest|copy|backup)", re.I)
_FALLBACK_DOC = re.compile(r"(draft|final|v(?:er|ersion)?[._ ]?\d+|rev[._ ]?\d+|latest|copy|backup)", re.I)


def load_rules() -> dict:
    return {
        "taxonomy": load_json(RULES / "annotation_taxonomy.json", default={}) or {},
        "distill": load_json(RULES / "distill_rules.json", default={}) or {},
        "projection": load_json(RULES / "contract_projection.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 文件库盘点：扫描、哈希、别名/重复、版本组
# --------------------------------------------------------------------------------------
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_library_root(case: dict, case_dir: Path, work: Path) -> tuple:
    """解析库目录引用；相对路径按 case 目录 → 祖先 input/ → work → cwd → /input 回退。"""
    ref = first_present(case, *LIBRARY_KEYS)
    if ref is None or not isinstance(ref, str) or not ref.strip():
        return None, [], "case 未声明文件库目录（materials_root/library_path 等字符串路径），按内联材料处理"
    raw = Path(str(ref))
    if raw.is_absolute():
        return (raw if raw.is_dir() else None), [str(raw)], ""
    bases = [case_dir, case_dir.parent, work or Path("."), Path.cwd(), Path("/input")]
    tried, hit = [], None
    for base in bases:
        cand = base / raw
        tried.append(str(cand))
        if hit is None and cand.is_dir():
            hit = cand
    return hit, tried, "" if hit else "库目录不存在（已尝试：" + "；".join(tried) + "）"


def scan_library_files(root: Path) -> list:
    """递归扫描库目录（跳过隐藏文件与侧车），逐文件 SHA-256+大小；输出确定性排序。

    侧车文件（<f>.ocr.txt / <f>.ocr.json / 二进制文件的 <f>.txt 伴生件）是提取辅助，
    不作为库文档本身建档，否则同一正文会以「文档+侧车」双份入库。
    """
    raw = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        raw.append({"path": rel, "name": p.name, "ext": p.suffix.lstrip(".").lower(),
                    "size": p.stat().st_size, "sha256": file_sha256(p), "_abs": p})
    paths = {f["path"] for f in raw}
    binary_ext = {"pdf", "docx", "pptx", "xlsx", "png", "jpg", "jpeg", "tif", "tiff"}

    def is_sidecar(f):
        name = f["name"]
        if name.endswith(".ocr.txt") or name.endswith(".ocr.json"):
            return True
        if f["ext"] in ("txt", "json") and name[: -(len(f["ext"]) + 1)] in paths:
            base_ext = Path(name[: -(len(f["ext"]) + 1)]).suffix.lstrip(".").lower()
            return base_ext in binary_ext
        return False

    return [f for f in raw if not is_sidecar(f)]


def version_from_text(value: str) -> str:
    m = _VERSION_TOKEN.search(str(value or ""))
    if not m:
        return ""
    tok = m.group(0).lower().replace(" ", "")
    if tok.startswith("draft"):
        return "draft"
    if tok.startswith("final"):
        return "final"
    return tok


def version_rank(version: str) -> tuple:
    """版本排序键：final > v<n> > draft；同前缀按数字大者优先。可完全确定性比较。"""
    v = str(version or "").lower()
    num = ""
    m = re.search(r"(\d+)", v)
    if m:
        num = m.group(1).zfill(4)
    order = 3 if "final" in v else (2 if re.search(r"v|rev", v) else (1 if "draft" in v else 0))
    return (order, num, v)


def doc_key_of(rec: dict, explicit: str = "") -> str:
    """文档同一性键：管理性说明显式给出 > 元数据 doc/title > 文件名去版本词后的强归一。"""
    if explicit:
        return norm_title(explicit)
    meta = rec.get("metadata") or {}
    for key in ("doc_id", "document_id", "doc"):
        if norm_title(str(meta.get(key) or "")):
            return norm_title(str(meta[key]))
    title = rec.get("title") or meta.get("title") or rec["name"].rsplit(".", 1)[0]
    stripped = _FALLBACK_DOC.sub(" ", str(title))
    return norm_title(stripped) or norm_title(str(title))


def build_version_groups(recs: list, notes_by_path: dict) -> list:
    """版本组：同一 doc_key 且带版本信息的文件归组；canonical=版本序最大者。"""
    groups = {}
    for rec in recs:
        note = notes_by_path.get(rec["path_norm"]) or notes_by_path.get(rec["path"])
        explicit_doc = str((note or {}).get("doc", "") or "")
        version = rec["version"] or ((note or {}).get("version", "") if note else "")
        basis = "version_note" if note else ("metadata" if (rec.get("metadata") or {}).get("version")
                                             else ("filename_pattern" if rec["version"] else ""))
        if not version:
            rec["version"], rec["version_basis"], rec["doc_key"] = "", "", doc_key_of(rec, explicit_doc)
            continue
        rec["version"], rec["version_basis"] = version, basis
        rec["doc_key"] = doc_key_of(rec, explicit_doc)
        if basis == "filename_pattern":
            rec["version_warnings"] = "版本取自文件名模式（启发式），管理性版本说明缺失"
        key = rec["doc_key"]
        groups.setdefault(key, []).append(rec)
    out = []
    for key in sorted(groups):
        members = groups[key]
        if len(members) < 2:
            continue
        # canonical = 版本序最大者；并列时优先非别名副本（alias_of 为空），再按路径稳定排序
        max_rank = max(version_rank(r["version"]) for r in members)
        tops = [r for r in members if version_rank(r["version"]) == max_rank]
        canonical = sorted(tops, key=lambda r: (r.get("alias_of") is not None, r["path"]))[0]
        ranked = sorted(members, key=lambda r: r["path"])
        out.append({
            "doc_key": key,
            "canonical": {"path": canonical["path"], "version": canonical["version"]},
            "members": [{"path": r["path"], "version": r["version"], "basis": r["version_basis"],
                         "note": (notes_by_path.get(r["path_norm"]) or notes_by_path.get(r["path"]) or {}).get("note", "")}
                        for r in ranked],
            "note_source": "version_note" if any(r["version_basis"] == "version_note" for r in members) else "mixed",
            "policy": "canonical=管理性版本序最大者（final>v(n)>draft，并列时非别名副本优先）；旧版只读归档，不删除",
        })
    return out


def parse_version_notes(case: dict) -> dict:
    """管理性版本说明：{match/path/file, doc, version, note} → 按路径后缀归一匹配。"""
    notes = {}
    for key in VERSION_NOTE_KEYS:
        raw = case.get(key)
        if raw is None and key == "versions":
            raw = (case.get("library") or {}).get("versions") if isinstance(case.get("library"), dict) else None
        for item in as_list(raw):
            if not isinstance(item, dict):
                continue
            match = str(first_present(item, "match", "path", "file", "filename", default="") or "")
            if not match:
                continue
            notes[norm(match)] = {"match": match,
                                  "doc": str(first_present(item, "doc", "document", "title", default="") or ""),
                                  "version": str(first_present(item, "version", "ver", default="") or ""),
                                  "note": str(first_present(item, "note", "admin_note", default="") or "")}
    return notes


def match_note_for(path_norm: str, notes: dict) -> dict | None:
    if path_norm in notes:
        return notes[path_norm]
    for k in sorted(notes, key=len, reverse=True):
        if path_norm.endswith(k):
            return notes[k]
    return None


# --------------------------------------------------------------------------------------
# 文本获取链
# --------------------------------------------------------------------------------------
def read_docx_paragraphs(path: Path) -> list:
    """纯标准库读 DOCX：zip + 正则取每个 <w:p> 的 <w:t> 合并为段落。"""
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8")
    except Exception:  # noqa: BLE001 —— 损坏 docx 走诚实 gap
        return []
    paras = []
    for pm in re.finditer(r"<w:p\b[^>]*>.*?</w:p>", xml, flags=re.S):
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", pm.group(0), flags=re.S)
        if texts:
            paras.append(_xml_unescape("".join(texts)))
    return paras


def read_sidecar(abs_path: Path) -> tuple:
    """侧车文本与 OCR 元数据：<f>.txt / <stem>.txt / <f>.ocr.txt / <stem>.ocr(.txt|.json)。"""
    cands = [Path(str(abs_path) + ".txt"), abs_path.with_suffix(".txt"),
             Path(str(abs_path) + ".ocr.txt"), abs_path.with_name(abs_path.stem + ".ocr.txt")]
    seen = []
    for cand in cands:
        # .txt 文件不能把自己的正文当成自己的「侧车」
        if cand in seen or cand.resolve() == abs_path.resolve():
            continue
        seen.append(cand)
        txt = read_text(cand)
        if txt.strip():
            ocr = {}
            for jcand in (Path(str(abs_path) + ".ocr.json"), abs_path.with_name(abs_path.stem + ".ocr.json")):
                data = load_json(jcand, default=None)
                if isinstance(data, dict):
                    ocr = data
                    break
            return txt, {"sidecar": str(cand.name), "ocr": ocr}
    return "", {}


def split_inline_pages(text: str) -> list:
    """把 [[page:N|label]] 标记的文本切成页序列（侧车/纯文本路径共用）。"""
    if "[[page:" not in text:
        return [{"page": 1, "page_label": "", "label_source": "absent", "text": text, "two_column": False}]
    pages = []
    for seg in text.split("[[page:")[1:]:
        m = re.match(r"(\d+)(?:\|([^\]]*))?\]\]", seg)
        if not m:
            continue
        pages.append({"page": int(m.group(1)), "page_label": (m.group(2) or "").strip(),
                      "label_source": "explicit" if (m.group(2) or "").strip() else "absent",
                      "text": seg[m.end():].lstrip("\n"), "two_column": False})
    return pages or [{"page": 1, "page_label": "", "label_source": "absent", "text": text, "two_column": False}]


def acquire_text(rec: dict, cfg: dict) -> dict:
    """按侧车→PDF→DOCX→纯文本→gap 链取正文；记录 extraction_method/字符数/文本哈希。"""
    info = {"extraction_method": "", "n_chars": 0, "text_sha256": "", "pages": [],
            "ocr_confidence": None, "ocr_low_spans": [], "warnings": [], "gap": None, "gap_code": ""}
    floor = float(cfg.get("thresholds", {}).get("ocr_confidence_floor", 0.85))

    def finish(pages, method, extra=None):
        text = "\n".join(p["text"] for p in pages)
        info.update({"extraction_method": method, "n_chars": len(text),
                     "text_sha256": sha256_text(text), "pages": pages})
        if extra:
            info["warnings"].extend(extra)
        return info

    if rec.get("pages_raw"):
        pages = []
        for i, p in enumerate(rec["pages_raw"], start=1):
            if isinstance(p, dict):
                body = first_present(p, "text", "content", "lines", default="")
                if isinstance(body, list):
                    body = "\n".join(str(x) for x in body)
                phys = p.get("page", i)
                try:
                    phys = int(phys)
                except (TypeError, ValueError):
                    phys = i
                label = str(p.get("page_label", p.get("printed_page", "")) or "").strip()
                pages.append({"page": phys, "page_label": label,
                              "label_source": "explicit" if label else "absent",
                              "text": str(body or ""), "two_column": bool(p.get("two_column"))})
            else:
                pages.append({"page": i, "page_label": "", "label_source": "absent",
                              "text": str(p), "two_column": False})
        return finish(pages, "inline_case_json")
    if str(rec.get("inline_text") or "").strip():
        return finish(split_inline_pages(str(rec["inline_text"])), "inline_case_json")

    local = rec.get("_abs")
    if local is None or not Path(local).is_file():
        info["gap"], info["gap_code"] = "内联无正文且文件路径不可读（或不存在），文本获取失败", "R_NO_TEXT_LAYER"
        return info

    sc_txt, sc_meta = read_sidecar(local)
    if sc_txt.strip():
        ocr = sc_meta.get("ocr") or {}
        conf = ocr.get("mean_confidence", ocr.get("confidence"))
        try:
            conf = round(float(conf), 4) if conf is not None else None
        except (TypeError, ValueError):
            conf = None
        info["ocr_confidence"] = conf
        info["ocr_low_spans"] = [str(s) for s in as_list(ocr.get("low_confidence_spans"))]
        if conf is not None and conf < floor:
            info["warnings"].append(f"OCR 平均置信度 {conf} 低于阈值 {floor}：该文件上的锚点降级标注")
        method = "sidecar_ocr_text" if ".ocr." in str(sc_meta.get("sidecar", "")) else "sidecar_text"
        return finish(split_inline_pages(sc_txt), method, [f"侧车文本来自 {sc_meta.get('sidecar')}"])

    ext = rec.get("ext", "")
    if ext == "pdf":
        got = pdf_min.read_pdf(local)
        if got["encrypted"]:
            info["gap"], info["gap_code"] = got["errors"][0], "R_FILE_ENCRYPTED"
            return info
        if got["errors"] and not got["ok"]:
            # 区分损坏（流解压失败/找不到流）与无文本层（流都完好但没有文本操作）
            corrupt = bool(got.get("streams_failed")) or not got.get("streams_total")
            code = "R_FILE_CORRUPT" if corrupt else "R_NO_TEXT_LAYER"
            info["gap"], info["gap_code"] = "；".join(got["errors"]), code
            return info
        pages = [{"page": pg["page"], "page_label": "", "label_source": "absent",
                  "text": pg["text"], "two_column": bool(pg["layout"].get("two_column")),
                  "layout": pg["layout"]} for pg in got["pages"]]
        warn = list(got["errors"]) + list(got["warnings"])
        if got.get("confidence", 1.0) < 1.0:
            warn.append(f"提取置信度 {got['confidence']}（流级降级已计入）")
        return finish(pages, "pdf_textlayer", warn)
    if ext in DOC_EXTS:
        paras = read_docx_paragraphs(local)
        if not paras:
            info["gap"], info["gap_code"] = "DOCX 无法解析出段落（可能损坏）", "R_FILE_CORRUPT"
            return info
        return finish([{"page": 1, "page_label": "", "label_source": "absent",
                        "text": "\n".join(paras), "two_column": False}], "docx_xml")
    if ext in TEXT_EXTS:
        txt = read_text(local)
        if txt.strip():
            return finish(split_inline_pages(txt), "plain_text")
    info["gap"], info["gap_code"] = f"无可用提取器（扩展名 .{ext or '未知'} 不在侧车/PDF/DOCX/纯文本链内）", "R_FILE_NO_EXTRACTOR"
    return info


# --------------------------------------------------------------------------------------
# 材料记录构建（扫描 + 内联合并）
# --------------------------------------------------------------------------------------
def build_library(case: dict, case_dir: Path, work: Path, cfg: dict) -> dict:
    root, tried, root_warn = resolve_library_root(case, case_dir, work)
    notes_by_path = parse_version_notes(case)
    recs, gaps, mode = [], [], "inline" if root is None else "library"

    def push(rec):
        rec["path_norm"] = norm(rec["path"])
        recs.append(rec)

    if root is not None:
        scanned = scan_library_files(root)
        for f in scanned:
            meta_notes = match_note_for(norm(f["path"]), notes_by_path) or {}
            stem_version = version_from_text(f["name"].rsplit(".", 1)[0])
            rec = {"file_id": "lib-" + stable_id("lib", f["path"], n=8), "source": "scan",
                   "path": f["path"], "name": f["name"], "ext": f["ext"], "size": f["size"],
                   "file_sha256": f["sha256"], "_abs": f["_abs"],
                   "title": str(meta_notes.get("doc", "") or ""),
                   "metadata": {"version_hint": meta_notes.get("version", ""),
                                "admin_note": meta_notes.get("note", "")},
                   "version": (meta_notes.get("version", "") or stem_version),
                   "alias_of": None, "doc_key": "", "version_basis": ""}
            push(rec)
    for idx, item in enumerate(as_list(first_present(case, "materials", "files", "documents", "readings", default=[])), start=1):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            continue
        path_str = str(first_present(item, "path", "file", "name", default="") or "").strip()
        name = str(first_present(item, "name", "filename", default="") or Path(path_str).name)
        fid = str(first_present(item, "file_id", "id", "doc_id", default="")
                  or stable_id("mat", path_str or name, idx, n=8))
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for key in ("version", "year", "project_id", "permission", "scanned", "source", "title"):
            if key in item and key not in meta:
                meta[key] = item[key]
        local = Path(path_str) if path_str else None
        if local is not None and not local.is_absolute():
            for base in (case_dir, case_dir.parent, work or Path("."), Path("/input")):
                if (base / local).is_file():
                    local = base / local
                    break
        pages_raw = first_present(item, "pages", "page_list")
        inline_text = first_present(item, "text", "content", "body", default="")
        if isinstance(inline_text, list):
            inline_text = "\n".join(str(x) for x in inline_text)
        inline_text = str(inline_text or "")
        ext = (Path(name or path_str).suffix.lstrip(".").lower() or "")
        scanned_match = None
        if root is not None and path_str:
            tail = norm(path_str)
            scanned_match = next((r for r in recs if r["source"] == "scan"
                                  and (r["path_norm"] == tail or r["path_norm"].endswith("/" + tail) or tail.endswith(r["path_norm"]))), None)
        if scanned_match is not None:
            scanned_match["file_id"] = scanned_match.get("file_id") or fid
            if item.get("title"):
                scanned_match["title"] = str(item["title"])
            scanned_match["declared_by_case"] = True
            if isinstance(item.get("metadata"), dict):
                scanned_match["metadata"].update({k: v for k, v in item["metadata"].items() if v not in (None, "")})
            continue
        rec = {"file_id": fid, "source": "inline", "path": path_str or name, "name": name, "ext": ext,
               "size": None, "file_sha256": (file_sha256(local) if local is not None and Path(local).is_file() else None),
               "_abs": local, "title": str(first_present(item, "title", default="") or ""),
               "metadata": meta, "pages_raw": pages_raw, "inline_text": inline_text,
               "version": str(meta.get("version", "") or ""), "alias_of": None,
               "doc_key": "", "version_basis": ""}
        push(rec)

    by_hash = {}
    for rec in recs:
        if rec.get("file_sha256"):
            by_hash.setdefault(rec["file_sha256"], []).append(rec)
    alias_groups = []
    for h in sorted(by_hash):
        members = by_hash[h]
        if len(members) < 2:
            continue
        # canonical：优先非副本词命名（copy/backup/latest 是整理候选，不宜做规范名），再按路径排序
        bait = re.compile(r"(copy|backup|latest)", re.I)
        members_sorted = sorted(members, key=lambda r: (bool(bait.search(r["name"])), r["path"]))
        canonical = members_sorted[0]
        for r in members_sorted[1:]:
            r["alias_of"] = canonical["path"]
        alias_groups.append({"sha256": h, "kind": "alias_or_duplicate",
                             "canonical": canonical["path"],
                             "members": [r["path"] for r in members_sorted],
                             "size": canonical.get("size"),
                             "note": "同 SHA-256 不同文件名：完全重复/别名文件（canonical 优先非 copy/backup/latest 命名；合并候选，未执行任何改名）"})

    version_groups = build_version_groups(recs, notes_by_path)

    seen_titles = {}
    near_dups = []
    for rec in sorted(recs, key=lambda r: r["path"]):
        if rec.get("alias_of"):
            continue
        note = match_note_for(rec["path_norm"], notes_by_path) or {}
        key = doc_key_of(rec, str(note.get("doc", "") or ""))
        rec["doc_key"] = rec["doc_key"] or key
        if key and key in seen_titles and seen_titles[key].get("file_sha256") != rec.get("file_sha256"):
            near_dups.append({"a": seen_titles[key]["path"], "b": rec["path"], "title_key": key,
                              "note": "同归一化标题但内容哈希不同：近重复（可能是不同版本/修订），建议人工确认"})
        elif key and key not in seen_titles:
            seen_titles[key] = rec

    return {"root": root, "root_tried": tried, "root_warn": root_warn, "mode": mode,
            "recs": recs, "gaps": gaps, "alias_groups": alias_groups,
            "near_duplicate_hints": near_dups, "version_groups": version_groups,
            "version_notes_source": ", ".join(VERSION_NOTE_KEYS)}


def hash_snapshot(lib: dict) -> dict:
    return {r["path"]: r.get("file_sha256") for r in lib["recs"] if r.get("file_sha256")}


def verify_untouched(before: dict, lib: dict) -> dict:
    """流程结束时逐文件重算 SHA-256，与扫描时对照，证明输入库未被改写。"""
    changed, rechecked = [], 0
    for r in lib["recs"]:
        abs_p = r.get("_abs")
        if before.get(r["path"]) is None or abs_p is None or not Path(abs_p).is_file():
            continue
        rechecked += 1
        if file_sha256(Path(abs_p)) != before[r["path"]]:
            changed.append(r["path"])
    return {"files": len(before), "rechecked": rechecked, "hashes_unchanged": not changed,
            "changed": changed, "input_modified": bool(changed),
            "policy": "输入库全程只读；本流程只在输出目录写 result.json 与交接清单"}


# --------------------------------------------------------------------------------------
# 批注装载与类型/颜色归一（沿用原设计）
# --------------------------------------------------------------------------------------
def collect_annotations(case: dict) -> list:
    raw = first_present(case, "annotations", "highlights", "marks", "notes", default=[])
    out = []
    for idx, item in enumerate(as_list(raw), start=1):
        if not isinstance(item, dict):
            continue
        aid = str(first_present(item, "annotation_id", "id", "aid", default="") or stable_id("ann", json.dumps(item, sort_keys=True, ensure_ascii=False), idx, n=8))
        quote = str(first_present(item, "quote", "text", "selected_text", "highlight", "excerpt", default="") or "").strip()
        comment = str(first_present(item, "comment", "note", "content", "margin_note", default="") or "").strip()
        page = first_present(item, "page", "page_index", default=None)
        try:
            page = int(page) if page not in (None, "") else None
        except (TypeError, ValueError):
            page = None
        label = first_present(item, "page_label", "printed_page", default=None)
        out.append({
            "annotation_id": aid,
            "file_id": str(first_present(item, "file_id", "doc_id", "material_id", default="") or ""),
            "path": str(first_present(item, "path", "file", "doc", default="") or ""),
            "page": page,
            "page_label": "" if label in (None, "") else str(label).strip(),
            "type_raw": str(first_present(item, "type", "kind", "annotation_type", default="") or ""),
            "color": str(first_present(item, "color", "colour", default="") or "").strip().lower(),
            "quote": quote,
            "comment": comment,
            "position": first_present(item, "position", "rects", "bbox", "coordinates", default=None),
            "tags": [str(t) for t in as_list(first_present(item, "tags", "labels", default=[]))],
            "created": str(first_present(item, "created", "date", "time", default="") or ""),
        })
    return out


def norm_type(raw: str, taxonomy: dict) -> tuple:
    key = norm(raw)
    for canon, spec in (taxonomy.get("types") or {}).items():
        for alias in [canon] + [str(a) for a in as_list(spec.get("aliases", []))]:
            if norm(alias) and norm(alias) == key:
                return canon, spec.get("label", canon)
    return "unknown_type", "未知类型"


def color_meaning(color: str, taxonomy: dict) -> tuple:
    spec = (taxonomy.get("colors") or {}).get(color)
    if spec:
        return spec.get("meaning", ""), spec.get("label", "")
    return "", ""


def aspect_of(annotation: dict, taxonomy: dict) -> list:
    """按线索词判定笔记维度；一条批注可命中多个维度（多标签），未命中则不臆测。"""
    cues = taxonomy.get("aspect_cues", {})
    basis = annotation["comment"] if annotation["comment"].strip() else annotation["quote"]
    text = norm(basis + " " + " ".join(annotation["tags"]))
    hits = []
    for aspect, spec in cues.items():
        matched = [str(c) for c in as_list(spec.get("cues", [])) if norm(c) and norm(c) in text]
        if matched:
            hits.append({"aspect": aspect, "label": spec.get("label", aspect), "matched_cues": matched,
                         "cue_source": "comment" if annotation["comment"].strip() else "quote"})
    return hits


# --------------------------------------------------------------------------------------
# 锚点校正（逐字 → 模糊降级 → 诚实标注）
# --------------------------------------------------------------------------------------
def locate_quote(pages: list, quote: str) -> dict:
    """逐字（大小写归一）定位引文；返回命中页与字符区间。"""
    if not quote:
        return {"found": [], "occurrences": 0}
    q = quote.strip()
    found = []
    for pg in pages:
        body = pg["text"]
        low, lq = body.lower(), q.lower()
        pos = low.find(lq)
        while pos != -1 and len(found) < 20:
            found.append({"page": pg["page"], "page_label": pg.get("page_label", ""),
                          "two_column": bool(pg.get("two_column")), "char_start": pos,
                          "char_end": pos + len(q),
                          "line_from": body.count("\n", 0, pos) + 1,
                          "line_to": body.count("\n", 0, pos + len(q)) + 1})
            pos = low.find(lq, pos + 1)
    return {"found": found, "occurrences": len(found)}


def fuzzy_locate(pages: list, quote: str) -> dict:
    """模糊匹配：只在逐字失败时使用，结果一律如实标 fuzzy，不冒充逐字锚点。

    相似度 = 引文与页面归一文本的最长公共块覆盖字符数 / 引文长度（difflib），
    对 OCR 单字符噪声（optimization→optimi2ation）给 ~0.98，对无关句子接近 0。
    """
    q = norm(quote)
    if len(q) < 6:
        return {"ratio": 0.0, "page": None, "window": "", "note": "引文过短，模糊匹配无意义"}
    best = {"ratio": 0.0, "page": None, "window": ""}
    for pg in pages:
        t = norm(pg["text"])
        if not t:
            continue
        blocks = difflib.SequenceMatcher(None, q, t).get_matching_blocks()
        matched = sum(b.size for b in blocks)
        ratio = matched / len(q)
        if ratio > best["ratio"]:
            first = next((b for b in blocks if b.size > 0), None)
            at = first.b if first else 0
            best = {"ratio": rounded(ratio, 4), "page": pg["page"],
                    "window": t[max(0, at - 20): at + len(q) + 40][:160],
                    "note": "最近似候选为归一化文本窗口（相似度=公共块覆盖率），仅供人工核对，不作字符级锚点"}
    return best


def anchor_check(ann: dict, mats: list, pages_by_file: dict, cfg: dict) -> dict:
    def find_mat():
        if ann["file_id"]:
            hit = next((m for m in mats if m["file_id"] == ann["file_id"]), None)
            if hit:
                return hit
        if ann["path"]:
            tail = norm(ann["path"])
            hit = next((m for m in mats if norm(m["path"]) == tail or m["name"] == Path(ann["path"]).name
                        or norm(m["path"]).endswith("/" + tail) or tail.endswith("/" + norm(m["path"]))), None)
            if hit:
                return hit
        return None

    mat = find_mat()
    if mat is None:
        return {"status": "unverifiable", "code": "R_ANCHOR_NO_MATERIAL",
                "reason": "批注指向的材料不在本 case 提供的材料/文件库内，无法校正锚点",
                "page_match": None, "found_on": []}
    fid = mat["file_id"]
    pages = pages_by_file.get(fid) or []
    if not pages or not any(p["text"].strip() for p in pages):
        return {"status": "unverifiable", "code": "R_ANCHOR_NO_TEXT_LAYER",
                "reason": "材料无可用文本层（扫描件无侧车/PDF 加密或损坏），引文无法逐字定位；坐标只能沿用批注自带值",
                "page_match": None, "found_on": [], "material_file_id": fid,
                "extraction_gap": mat.get("_acq", {}).get("gap_code") or "R_NO_TEXT_LAYER"}
    loc = locate_quote(pages, ann["quote"])
    declared_page, declared_label = ann["page"], ann["page_label"]
    fuzzy_min = float(cfg.get("thresholds", {}).get("fuzzy_min_ratio", 0.72))
    if not loc["found"]:
        fz = fuzzy_locate(pages, ann["quote"])
        if fz["ratio"] >= fuzzy_min:
            return {"status": "fuzzy_matched", "code": "R_ANCHOR_FUZZY",
                    "reason": (f"引文逐字匹配失败，模糊匹配最近似候选在第 {fz['page']} 页"
                               f"（相似度 {fz['ratio']}，阈值 {fuzzy_min}）：可能 OCR 漂移/标点差异，需人工确认，不作逐字锚点"),
                    "page_match": None, "found_on": [], "occurrences": 0,
                    "fuzzy": {"similarity": fz["ratio"], "page": fz["page"], "window": fz["window"],
                              "note": fz["note"]},
                    "material_file_id": fid}
        return {"status": "not_found", "code": "R_ANCHOR_NOT_FOUND",
                "reason": ("引文在材料正文中找不到（模糊匹配最高相似度 %.4f 低于阈值 %.2f；"
                           "可能 OCR 漂移、跨栏拼接或来自其它版本），不硬挂位置" % (fz["ratio"], fuzzy_min)),
                "page_match": False, "found_on": [], "occurrences": 0,
                "fuzzy": {"similarity": fz["ratio"], "page": fz["page"]},
                "material_file_id": fid}
    first = loc["found"][0]
    hit_pages = sorted({f["page"] for f in loc["found"]})
    label_match = bool(declared_label) and any(f["page_label"] == declared_label for f in loc["found"])
    page_match = (declared_page is None or declared_page in hit_pages) and (not declared_label or label_match)
    if len(loc["found"]) > 1:
        status = "ambiguous"
    elif page_match:
        status = "verified"
    else:
        status = "page_mismatch"
    hit_page = next(p for p in pages if p["page"] == first["page"])
    span_ok = hit_page["text"][first["char_start"]:first["char_end"]].lower() == ann["quote"].strip().lower()
    reason = ""
    if status == "page_mismatch":
        reason = (f"引文实际出现在第 {hit_pages} 页（物理页），与批注记录的页 {declared_page}/标签 "
                  f"{declared_label or 'absent'} 不一致")
    elif status == "ambiguous":
        reason = f"引文在同文件出现 {loc['occurrences']} 次，锚点不唯一，需人工确认"
    return {"status": status,
            "code": "" if status == "verified" else ("R_ANCHOR_PAGE_MISMATCH" if status == "page_mismatch" else "R_ANCHOR_AMBIGUOUS"),
            "reason": reason,
            "page_match": page_match,
            "found_on": loc["found"][:5],
            "occurrences": loc["occurrences"],
            "corrected_page": first["page"],
            "corrected_page_label": first["page_label"],
            "corrected_char_range": [first["char_start"], first["char_end"]],
            "corrected_lines": [first["line_from"], first["line_to"]],
            "char_span_checked": bool(span_ok),
            "two_column_hint": bool(first.get("two_column")),
            "material_file_id": fid}


# --------------------------------------------------------------------------------------
# 笔记 / 关联 / 索引 / 检索
# --------------------------------------------------------------------------------------
def build_notes(anns: list, taxonomy: dict, cfg: dict, anchor_by_id: dict, rec_by_id: dict) -> tuple:
    weights = cfg.get("anchor_weights", {}) or {"verified": 1.0, "ambiguous": 0.7, "fuzzy_matched": 0.5,
                                                "page_mismatch": 0.6, "not_found": 0.3, "unverifiable": 0.2}
    ocr_penalty = float(cfg.get("thresholds", {}).get("ocr_confidence_penalty", 0.8))
    notes, orphan = [], []
    for i, ann in enumerate(anns, start=1):
        aspects = aspect_of(ann, taxonomy)
        anchor = anchor_by_id[ann["annotation_id"]]
        rec = rec_by_id.get(anchor.get("material_file_id") or ann["file_id"]) or {}
        acq = rec.get("_acq") or {}
        ocr_degraded = bool(acq.get("ocr_confidence") is not None and ann.get("_ocr_low")) or bool(ann.get("_ocr_low"))
        if not aspects:
            orphan.append({"annotation_id": ann["annotation_id"], "code": "R_ASPECT_UNRESOLVED",
                           "reason": "批注内容未命中任何笔记维度线索词：按原文摘录保留，不臆测维度"})
            aspects_view = [{"aspect": "excerpt", "label": "原文摘录（未定维度）", "matched_cues": []}]
        else:
            aspects_view = aspects
        note = {
            "note_id": f"N{i:03d}",
            "annotation_ids": [ann["annotation_id"]],
            "aspects": [a["aspect"] for a in aspects_view],
            "aspect_details": aspects_view,
            "claim": ann["comment"] or ann["quote"],
            "support_quote": ann["quote"],
            "support_comment": ann["comment"],
            "source": {
                "file_id": anchor.get("material_file_id") or ann["file_id"],
                "path": rec.get("path") or ann["path"],
                "file_sha256": rec.get("file_sha256"),
                "alias_of": rec.get("alias_of"),
                "version": rec.get("version") or None,
                "version_basis": rec.get("version_basis") or None,
                "doc_key": rec.get("doc_key") or None,
                "extraction_method": acq.get("extraction_method") or None,
                "declared_page": ann["page"],
                "declared_page_label": ann["page_label"] or None,
                "anchor_status": anchor["status"],
                "corrected_page": anchor.get("corrected_page"),
                "corrected_page_label": anchor.get("corrected_page_label"),
                "char_range": anchor.get("corrected_char_range"),
                "lines": anchor.get("corrected_lines"),
            },
            "type": ann["type_label"],
            "type_canonical": ann["type_canonical"],
            "color": ann["color"] or None,
            "color_meaning": ann["color_meaning"] or None,
            "tags": ann["tags"],
            "ocr_degraded": ocr_degraded,
            "usable": anchor["status"] == "verified" and not ocr_degraded,
        }
        conf = ({"verified": 1.0, "ambiguous": 0.7, "fuzzy_matched": 0.5, "page_mismatch": 0.6,
                 "not_found": 0.3, "unverifiable": 0.2}.get(anchor["status"], 0.2)
                * (1.0 if ann["type_canonical"] != "unknown_type" else 0.85)
                * (1.0 if ann["quote"] else 0.7)
                * (ocr_penalty if ocr_degraded else 1.0))
        note["confidence"] = rounded(clamp(conf))
        notes.append(note)
    return notes, orphan


def build_links(notes: list, cfg: dict, taxonomy: dict, rec_by_id: dict) -> tuple:
    trivial = set(norm(t) for t in cfg.get("trivial_terms", []))
    min_shared = int(cfg.get("thresholds", {}).get("link_min_shared_terms", 2))
    max_links = int(cfg.get("thresholds", {}).get("link_max_per_note", 3))
    links, skipped = [], []
    terms = {}
    for n in notes:
        ts = [t for t in tokens(n["support_quote"] + " " + n["claim"] + " " + " ".join(n["tags"]))
              if t not in trivial and len(t) > 1]
        terms[n["note_id"]] = sorted(set(ts))
    for i in range(len(notes)):
        for j in range(i + 1, len(notes)):
            a, b = notes[i], notes[j]
            shared = sorted(set(terms[a["note_id"]]) & set(terms[b["note_id"]]))
            same_quote = norm(a["support_quote"]) == norm(b["support_quote"]) and a["support_quote"]
            if same_quote:
                ra, rb = rec_by_id.get(a["source"]["file_id"]) or {}, rec_by_id.get(b["source"]["file_id"]) or {}
                if ra.get("alias_of") or rb.get("alias_of"):
                    links.append({"from": a["note_id"], "to": b["note_id"], "basis": "same_quote_duplicate_file",
                                  "shared_terms": [], "strength": "strong",
                                  "note": "同一引文出现在同哈希重复/别名文件上：合并知识条目，避免重复计数"})
                else:
                    links.append({"from": a["note_id"], "to": b["note_id"], "basis": "same_quote_across_versions",
                                  "shared_terms": [], "strength": "strong",
                                  "note": "同一引文出现在不同文件/版本：按跨版本副本合并，不重复计知识条目"})
                continue
            if len(shared) >= min_shared:
                links.append({"from": a["note_id"], "to": b["note_id"], "basis": "shared_terms",
                              "shared_terms": shared[:8], "strength": "medium" if len(shared) < min_shared + 2 else "strong",
                              "note": f"共享 {len(shared)} 个非平凡术语，达连边阈值 {min_shared}"})
            elif shared:
                skipped.append({"from": a["note_id"], "to": b["note_id"],
                                "shared_terms": shared[:5],
                                "code": "R_LINK_SUPPRESSED",
                                "reason": f"仅共享 {len(shared)} 个术语，低于阈值 {min_shared}：抑制过度连接"})
    def priority(l):
        return (0 if str(l["basis"]).startswith("same_quote") else 1,
                -len(l.get("shared_terms") or []), l["from"], l["to"])

    kept, per_note = [], {}
    for l in sorted(links, key=priority):
        if per_note.get(l["from"], 0) < max_links and per_note.get(l["to"], 0) < max_links:
            kept.append(l)
            per_note[l["from"]] = per_note.get(l["from"], 0) + 1
            per_note[l["to"]] = per_note.get(l["to"], 0) + 1
        else:
            skipped.append({"from": l["from"], "to": l["to"], "shared_terms": l["shared_terms"],
                            "basis": l["basis"],
                            "code": "R_LINK_CAPPED",
                            "reason": f"单条笔记连边已达上限 {max_links}，按强度（跨版本同引文 > 共享术语数）贪心保留其余截断"})
    kept.sort(key=lambda l: (l["from"], l["to"]))
    skipped.sort(key=lambda x: (x["from"], x["to"], x["code"]))
    return kept, skipped, terms


def build_index(notes: list, links: list, rec_by_id: dict) -> dict:
    by_aspect, by_tag, by_file, by_page, by_version, term_index = {}, {}, {}, {}, {}, {}
    for n in notes:
        for a in n["aspects"]:
            by_aspect.setdefault(a, []).append(n["note_id"])
        for t in n["tags"]:
            by_tag.setdefault(t, []).append(n["note_id"])
        rec = rec_by_id.get(n["source"]["file_id"]) or {}
        canonical_path = rec.get("alias_of") or n["source"]["path"]
        by_file.setdefault(canonical_path, []).append(n["note_id"])
        if rec.get("doc_key") and rec.get("version"):
            by_version.setdefault(rec["doc_key"], []).append(n["note_id"])
        page = n["source"]["corrected_page"] if n["source"]["corrected_page"] is not None else n["source"]["declared_page"]
        by_page.setdefault(f"{canonical_path}#p{page}", []).append(n["note_id"])
        for t in tokens(n["support_quote"] + " " + n["claim"]):
            term_index.setdefault(t, []).append(n["note_id"])
    adjacency = {}
    for l in links:
        adjacency.setdefault(l["from"], []).append(l["to"])
        adjacency.setdefault(l["to"], []).append(l["from"])
    return {
        "by_aspect": {k: sorted(v) for k, v in sorted(by_aspect.items())},
        "by_tag": {k: sorted(v) for k, v in sorted(by_tag.items())},
        "by_file": {k: sorted(v) for k, v in sorted(by_file.items())},
        "by_page": {k: sorted(v) for k, v in sorted(by_page.items())},
        "by_version_group": {k: sorted(v) for k, v in sorted(by_version.items())},
        "term_index_size": len(term_index),
        "adjacency": {k: sorted(v) for k, v in sorted(adjacency.items())},
        "entry_points": ["按维度（by_aspect）", "按标签（by_tag）", "按来源文件（by_file，别名归一到规范文件）",
                         "按页（by_page）", "按版本组（by_version_group）",
                         "按术语（term_index，见 evidence.index.term_index_sample）"],
        "term_index_sample": {k: sorted(set(term_index[k])) for k in sorted(term_index)[:60]},
    }


def lookup(queries: list, notes: list, cfg: dict) -> list:
    """检索闭环：BM25 召回 + 摘录式回答 + 原文回链 + 召回不足说明（不调外部模型）。"""
    out = []
    if not notes:
        for i, q in enumerate(as_list(queries), start=1):
            text = str(first_present(q, "text", "question", "query", default="") if isinstance(q, dict) else q).strip()
            qid = str(first_present(q, "query_id", "id", default=f"LQ{i}") if isinstance(q, dict) else f"LQ{i}")
            out.append({"query_id": qid, "text": text, "hits": [], "hit_count": 0,
                        "answer": "", "recall": "insufficient",
                        "reason_if_empty": "笔记库为空（无批注沉淀）：无条目可检索，属召回不足"})
        return out
    min_score = float(cfg.get("thresholds", {}).get("lookup_min_bm25_score", 0.5))
    top_k = int(cfg.get("thresholds", {}).get("lookup_top_k", 5))
    docs = [tokens(n["support_quote"] + " " + n["claim"] + " " + " ".join(n["tags"]), unique=False) for n in notes]
    corpus_terms = set(t for d in docs for t in d)
    for i, q in enumerate(as_list(queries), start=1):
        if isinstance(q, dict):
            text = str(first_present(q, "text", "question", "query", default="")).strip()
            qid = str(first_present(q, "query_id", "id", default=f"LQ{i}"))
        else:
            text, qid = str(q), f"LQ{i}"
        if not text.strip():
            continue
        qs = [t for t in tokens(text) if len(t) > 1]
        scored = []
        for n, d in zip(notes, docs):
            score = bm25_score(qs, d, docs)
            if score > 0:
                matched = sorted(set(qs) & set(d))
                scored.append({"note_id": n["note_id"], "score": score,
                               "matched_terms": matched[:8], "aspect": n["aspects"][0],
                               "usable": n["usable"]})
        scored.sort(key=lambda x: (-x["score"], x["note_id"]))
        hits = [s for s in scored if s["score"] >= min_score]
        weak = bool(scored) and not hits
        hits_out = []
        for s in hits[:top_k]:
            n = next(x for x in notes if x["note_id"] == s["note_id"])
            src = n["source"]
            hits_out.append({**s, "source": {"path": src["path"], "page": src.get("corrected_page") or src.get("declared_page"),
                                             "char_range": src.get("char_range")},
                             "claim": n["claim"], "quote": n["support_quote"]})
        answer = ""
        if hits_out:
            parts = []
            for h in hits_out[:2]:
                loc = "%s 第%s页" % (h["source"]["path"], h["source"]["page"]) if h["source"]["page"] else h["source"]["path"]
                parts.append(f"{h['note_id']}「{h['claim']}」——引文：“{h['quote']}”（{loc}）")
            answer = "库内证据摘录（不外加结论）：" + "；".join(parts)
            if all(not h["usable"] for h in hits_out):
                answer += "（命中条目锚点未全部核实，仅供参考）"
        missing = [t for t in qs if t not in corpus_terms]
        recall = "ok" if hits_out else "insufficient"
        reason = "" if hits_out else ("BM25 最高分低于阈值 %.2f（%s）；属召回不足，需扩词或补笔记"
                                      % (min_score, "查询词与笔记库无重合，缺词：" + "、".join(missing[:8]) if missing
                                         else "重合词 tf-idf 权重过低"))
        out.append({"query_id": qid, "text": text, "hits": hits_out, "hit_count": len(hits_out),
                    "answer": answer, "recall": recall,
                    "missing_terms": missing[:10], "min_score": min_score,
                    "reason_if_empty": reason})
    return out


# --------------------------------------------------------------------------------------
# 恢复操作记录（只建议，不执行）与交接清单
# --------------------------------------------------------------------------------------
def build_ops_log(lib: dict, gaps: list) -> list:
    ops = []
    for g in lib["alias_groups"]:
        ops.append({"op": "merge_duplicate_candidates",
                    "targets": g["members"],
                    "basis": f"SHA-256 完全一致（{g['sha256'][:16]}…）：内容相同、仅文件名不同（别名/完全重复）",
                    "action": f"建议保留规范名 {g['canonical']}，其余移入重复归档目录；本流程未执行任何改名/删除"})
    for nd in lib["near_duplicate_hints"]:
        ops.append({"op": "review_near_duplicate",
                    "targets": [nd["a"], nd["b"]],
                    "basis": f"归一化标题相同（{nd['title_key']}）但内容哈希不同",
                    "action": "建议人工确认是否同一文献的不同修订；未执行"})
    for vg in lib["version_groups"]:
        ops.append({"op": "consolidate_versions",
                    "targets": [m["path"] for m in vg["members"]],
                    "basis": f"管理性版本说明/元数据归组 {vg['doc_key']}；canonical={vg['canonical']['version']}（{vg['canonical']['path']}）",
                    "action": "建议旧版只读归档、批注迁移前先对照版本说明；未执行"})
    for gap in gaps:
        ops.append({"op": "repair_file_gap",
                    "targets": [gap["path"]],
                    "basis": gap["reason"],
                    "action": f"建议人工修复（代码 {gap['code']}：加密件需口令、损坏件需换源、无文本层扫描件需补 OCR）；本流程诚实跳过，不伪造文本"})
    ops.append({"op": "rebuild_index",
                "targets": ["canonical_files"],
                "basis": "哈希清单/别名归一/版本组已重建",
                "action": "建议检索索引以 canonical 文件为键重建；未执行"})
    for op in ops:
        op["input_readonly"] = True
    return ops


def handoff_manifest(case_id: str, lib: dict, ops: list, ann_summary: dict, notes_count: int, lookups: list) -> dict:
    return {
        "case_id": case_id,
        "producer": SKILL,
        "policy": "交接建议清单：输入库只读，本清单不改写源文件；所有哈希为 SHA-256",
        "library_mode": lib["mode"],
        "files": [{"path": r["path"], "ext": r["ext"], "size": r.get("size"),
                   "sha256": r.get("file_sha256"), "version": r.get("version") or None,
                   "doc_key": r.get("doc_key") or None, "alias_of": r.get("alias_of"),
                   "extraction_method": (r.get("_acq") or {}).get("extraction_method") or None,
                   "n_chars": (r.get("_acq") or {}).get("n_chars"),
                   "status": "gap:" + (r.get("_acq") or {}).get("gap_code", "") if (r.get("_acq") or {}).get("gap_code") else "ok"}
                  for r in sorted(lib["recs"], key=lambda r: r["path"])],
        "alias_groups": lib["alias_groups"],
        "near_duplicate_hints": lib["near_duplicate_hints"],
        "version_groups": lib["version_groups"],
        "ops_log": ops,
        "anchor_summary": ann_summary,
        "notes": notes_count,
        "queries": [{"query_id": q["query_id"], "recall": q["recall"], "hit_count": q["hit_count"]} for q in lookups],
    }


def normalize_zip_timestamps(path: Path) -> Path:
    """把 zip 容器（DOCX）内条目的时间戳固定为 1980-01-01，保证交接清单逐字节可复现。

    office_min 负责生成内容（确定性的 XML），但 zipfile.writestr 默认写入当前时间；
    本函数只改容器头不改条目字节——与 060 题 normalize_pdf 固定 CreationDate 同思路。
    """
    import io

    src = Path(path)
    with zipfile.ZipFile(src) as zf:
        entries = [(info.filename, zf.read(info.filename)) for info in zf.infolist()]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            out.writestr(info, data)
    src.write_bytes(buf.getvalue())
    return src


def write_handoff(out_dir: Path, manifest: dict) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "handoff_manifest.json"
    json_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    rows = [["文件", "类型", "版本", "提取方式", "状态", "SHA-256(前16)"]]
    for f in manifest["files"]:
        rows.append([f["path"], f["ext"] or "-", f["version"] or "-", f["extraction_method"] or "-",
                     f["status"], (f["sha256"] or "-")[:16]])
    blocks = [{"type": "h1", "text": "阅读批注沉淀 · 文件库交接清单（%s）" % manifest["case_id"]},
              {"type": "p", "text": manifest["policy"]},
              {"type": "p", "text": "锚点统计：" + json.dumps(manifest["anchor_summary"], ensure_ascii=False)},
              {"type": "table", "rows": rows}]
    for vg in manifest["version_groups"]:
        blocks.append({"type": "h2", "text": "版本组：%s（canonical=%s）" % (vg["doc_key"], vg["canonical"]["version"])})
        blocks.append({"type": "bullet", "text": "；".join("%s=%s（%s）" % (m["path"], m["version"], m["basis"]) for m in vg["members"])})
    if manifest["alias_groups"]:
        blocks.append({"type": "h2", "text": "别名/完全重复组（合并候选）"})
        for g in manifest["alias_groups"]:
            blocks.append({"type": "bullet", "text": " = ".join(g["members"])})
    blocks.append({"type": "h2", "text": "恢复操作建议（未执行）"})
    for op in manifest["ops_log"]:
        blocks.append({"type": "bullet", "text": f"{op['op']}: {op['action']}"})
    docx_path = normalize_zip_timestamps(office_min.write_docx(out_dir / "handoff_manifest.docx", blocks, title="047 交接清单"))
    return {"manifest_json": json_path.name, "manifest_docx": docx_path.name,
            "docx_sha256": file_sha256(docx_path),
            "note": "交接清单写在输出目录（/app/output），绝不写回输入库"}


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    taxonomy, cfg = rules["taxonomy"], rules["distill"]
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res._contract_orig.keys()))

    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).resolve().parent
    hash_before = {}
    lib = build_library(case, case_dir, work, cfg)
    for rec in lib["recs"]:
        acq = acquire_text(rec, cfg)
        rec["_acq"] = acq
        rec["pages"] = acq["pages"]
    hash_before = hash_snapshot(lib)
    gaps = []
    for rec in lib["recs"]:
        acq = rec["_acq"]
        if acq["gap_code"]:
            gaps.append({"file_id": rec["file_id"], "path": rec["path"], "code": acq["gap_code"],
                         "reason": acq["gap"]})
    res.log("library_intake", mode=lib["mode"], files=len(lib["recs"]), gaps=len(gaps),
            alias_groups=len(lib["alias_groups"]), version_groups=len(lib["version_groups"]))

    mats = lib["recs"]
    mats_by_id = {m["file_id"]: m for m in mats}
    pages_by_file = {m["file_id"]: m["pages"] for m in mats}
    anns = collect_annotations(case)
    res.log("intake", materials=len(mats), annotations=len(anns), text_gaps=len(gaps))

    anchor_by_id, ann_rows, version_hints = {}, [], []
    ocr_floor = float(cfg.get("thresholds", {}).get("ocr_confidence_floor", 0.85))
    vg_by_path = {}
    for vg in lib["version_groups"]:
        for m in vg["members"]:
            vg_by_path[m["path"]] = vg

    for ann in anns:
        canon_type, type_label = norm_type(ann["type_raw"], taxonomy)
        meaning, color_label = color_meaning(ann["color"], taxonomy)
        anchor = anchor_check(ann, mats, pages_by_file, cfg)
        anchor_by_id[ann["annotation_id"]] = anchor
        rec = mats_by_id.get(anchor.get("material_file_id") or ann["file_id"]) or {}
        acq = rec.get("_acq") or {}
        low_spans = [norm(s) for s in acq.get("ocr_low_spans", [])]
        ocr_low = bool(low_spans and ann["quote"] and
                       (any(s and (s in norm(ann["quote"]) or norm(ann["quote"]) in s) for s in low_spans)))
        ocr_low = ocr_low or bool(acq.get("ocr_confidence") is not None and acq["ocr_confidence"] < ocr_floor)
        ann["_ocr_low"] = ocr_low
        ann.update({"type_canonical": canon_type, "type_label": type_label, "color_meaning": meaning})
        ann_rows.append({
            "annotation_id": ann["annotation_id"],
            "file_id": anchor.get("material_file_id") or ann["file_id"],
            "path": ann["path"] or rec.get("path", ""),
            "page": ann["page"],
            "page_label": ann["page_label"] or None,
            "type_raw": ann["type_raw"],
            "type_canonical": canon_type,
            "type_label": type_label,
            "color": ann["color"] or None,
            "color_label": color_label or None,
            "color_meaning": meaning or None,
            "quote": ann["quote"],
            "comment": ann["comment"],
            "tags": ann["tags"],
            "position": ann["position"],
            "position_source": "provided_bbox" if ann["position"] else "char_range_fallback",
            "anchor": anchor,
        })
        # 版本串味检查：批注挂在非 canonical 版本/副本上时给出迁移提示（只提示，不迁移）
        vg = vg_by_path.get(rec.get("path", ""))
        if vg and anchor["status"] in ("verified", "page_mismatch", "fuzzy_matched") \
                and rec["path"] != vg["canonical"]["path"]:
            canon_rec = next((m for m in mats if m["path"] == vg["canonical"]["path"]), None)
            in_canon = bool(canon_rec and norm(ann["quote"]) and
                            norm(ann["quote"]) in norm("\n".join(p["text"] for p in (canon_rec.get("pages") or []))))
            duplicate = bool(rec.get("alias_of"))
            if duplicate and in_canon:
                kind = "duplicate_copy"
                note = (f"批注挂在同哈希重复副本 {rec['path']} 上：同一引文在规范文件同样存在，"
                        f"建议把批注归一到 canonical {vg['canonical']['path']} 再沉淀")
            elif duplicate:
                kind = "stale_version"
                note = (f"批注挂在误命名/重复副本 {rec['path']} 上，引文只存在于其内容版本 {rec.get('version')}；"
                        f"canonical {vg['canonical']['version']}（{vg['canonical']['path']}）已无此内容：必须保留版本口径，防止版本串味")
            elif in_canon:
                kind = "stale_version"
                note = (f"批注挂在旧版本 {rec.get('version')} 上；同一引文也出现在 canonical "
                        f"{vg['canonical']['version']}，沉淀条目需确认是否迁移")
            else:
                kind = "stale_version"
                note = (f"批注引文只存在于旧版本 {rec.get('version')}，canonical {vg['canonical']['version']} "
                        f"已改写/删除：条目必须保留版本口径，防止版本串味")
            version_hints.append({
                "annotation_id": ann["annotation_id"], "file_path": rec["path"],
                "anchored_version": rec.get("version"), "canonical_path": vg["canonical"]["path"],
                "canonical_version": vg["canonical"]["version"], "quote_in_canonical": in_canon,
                "kind": kind, "note": note})

    rec_by_id = mats_by_id
    notes, orphan = build_notes(anns, taxonomy, cfg, anchor_by_id, rec_by_id)
    links, skipped_links, terms = build_links(notes, cfg, taxonomy, rec_by_id)
    index = build_index(notes, links, rec_by_id)
    queries = first_present(case, "queries", "lookup_queries", "questions", default=[])
    lookups = lookup(as_list(queries), notes, cfg)

    counts = {}
    for r in ann_rows:
        counts[r["anchor"]["status"]] = counts.get(r["anchor"]["status"], 0) + 1
    unknown_color = [r["annotation_id"] for r in ann_rows if r["color"] and not r["color_meaning"]]
    unknown_type = [r["annotation_id"] for r in ann_rows if r["type_canonical"] == "unknown_type"]
    no_quote = [r["annotation_id"] for r in ann_rows if not r["quote"]]
    ocr_degraded = [r["annotation_id"] for r in ann_rows if r.get("anchor") and
                    next((n for n in notes if n["annotation_ids"] == [r["annotation_id"]]), {}).get("ocr_degraded")]

    bad_anchor = counts.get("page_mismatch", 0) + counts.get("not_found", 0) + counts.get("unverifiable", 0) \
        + counts.get("ambiguous", 0) + counts.get("fuzzy_matched", 0)
    integrity = verify_untouched(hash_before, lib)

    # ---- 状态与缺口（诚实口径）----
    if not notes:
        res.status["overall"] = "empty"
        res.unmet("批注清单", "case.json 未提供任何批注（annotations/highlights/marks 均为空）；文件库盘点与恢复建议照常交付")
    elif bad_anchor:
        res.status["overall"] = "partial"
        if counts.get("page_mismatch"):
            res.unmet("锚点校正", f"{counts['page_mismatch']} 条批注的页与引文实际所在页不一致，已给校正页与字符区间")
        if counts.get("not_found"):
            res.unmet("锚点校正", f"{counts['not_found']} 条批注的引文逐字与模糊匹配均未命中，标 not_found 不硬挂位置")
        if counts.get("unverifiable"):
            res.unmet("锚点校正", f"{counts['unverifiable']} 条批注的材料无可用文本层，锚点不可校正")
        if counts.get("ambiguous"):
            res.unmet("锚点唯一性", f"{counts['ambiguous']} 条批注的引文在同文件出现多次，锚点不唯一")
        if counts.get("fuzzy_matched"):
            res.unmet("锚点校正", f"{counts['fuzzy_matched']} 条批注逐字失败、模糊匹配命中（相似度见 evidence）：如实标 fuzzy，需人工确认")
    else:
        res.status["overall"] = "complete"
    if gaps:
        by_code = {}
        for g in gaps:
            by_code[g["code"]] = by_code.get(g["code"], 0) + 1
        res.unmet("文件库提取", "；".join(f"{v} 份 {k}" for k, v in sorted(by_code.items())) +
                  "——诚实跳过，不伪造文本层（详见 evidence.library.gaps）")
    if not integrity["hashes_unchanged"]:
        res.unmet("输入完整性", f"输入文件哈希发生变化：{integrity['changed'][:5]}")
    if version_hints:
        res.unsure("版本串味", f"{len(version_hints)} 条批注挂在非 canonical 版本文件上，已给出迁移/保留版本口径提示")
    if ocr_degraded:
        res.unsure("OCR 置信度", f"{len(ocr_degraded)} 条批注落在低置信 OCR 文件/低置信片段上，已降级并降低置信度")
    if unknown_color:
        res.unsure("颜色语义", f"{len(unknown_color)} 条批注使用了词表外的颜色，含义留空未猜")
    if unknown_type:
        res.unsure("批注类型", f"{len(unknown_type)} 条批注的类型不在类型表内，标 unknown_type 未并入高亮")
    if orphan:
        res.note(f"{len(orphan)} 条批注未命中任何维度线索，按原文摘录保留（不臆测维度）")
    if skipped_links:
        res.note(f"{len(skipped_links)} 对候选关联被抑制（共享术语不足或超过单条笔记连边上限）")
    if lib["near_duplicate_hints"]:
        res.note(f"{len(lib['near_duplicate_hints'])} 对近重复（同归一化标题、内容哈希不同）仅提示，不自动合并")
    if lib["root_warn"]:
        res.unmet("文件库目录", lib["root_warn"])

    # ---- 交接清单（写输出目录，不写输入库）----
    handoff_info = {}
    if not getattr(args, "dry_run", False):
        manifest = handoff_manifest(res.case_id, lib, build_ops_log(lib, gaps),
                                    {k: v for k, v in sorted(counts.items())},
                                    len(notes), lookups)
        handoff_info = write_handoff(Path(args.out), manifest)
        res.log("handoff_written", files=2)

    ops = build_ops_log(lib, gaps)
    payload = {
        "annotations": ann_rows,
        "notes": notes,
        "links": links,
        "suppressed_links": skipped_links,
        "index": index,
        "lookups": lookups,
        "orphan_annotations": orphan,
        "library_report": {
            "mode": lib["mode"], "root": (lib["root"].name if lib["root"] is not None else None),
            "root_resolution": lib["root_tried"] or ["inline（无库目录，使用 case 内联材料）"],
            "files_scanned": sum(1 for r in lib["recs"] if r["source"] == "scan"),
            "inline_entries": sum(1 for r in lib["recs"] if r["source"] == "inline"),
            "alias_groups": lib["alias_groups"],
            "near_duplicate_hints": lib["near_duplicate_hints"],
            "version_groups": lib["version_groups"],
            "version_confusion_hints": version_hints,
            "gaps": gaps,
            "version_notes_fields": lib["version_notes_source"],
        },
        "ops_log": ops,
    }
    for key, value in payload.items():
        res.set(key, value)

    res.evidence["materials"] = {
        "files": [{"file_id": m["file_id"], "path": m["path"],
                   "source": m["source"], "ext": m["ext"],
                   "size": m.get("size"), "file_sha256": m.get("file_sha256"),
                   "extraction_method": (m.get("_acq") or {}).get("extraction_method") or None,
                   "n_chars": (m.get("_acq") or {}).get("n_chars") or 0,
                   "ocr_confidence": (m.get("_acq") or {}).get("ocr_confidence"),
                   "pages": len(m["pages"]),
                   "has_text_layer": bool(m["pages"] and any(p["text"].strip() for p in m["pages"])),
                   "sha256": sha256_text(norm("\f".join(p["text"] for p in m["pages"]))),
                   "alias_of": m.get("alias_of"),
                   "version": m.get("version") or None, "doc_key": m.get("doc_key") or None,
                   "year": (m.get("metadata") or {}).get("year")} for m in mats],
        "unreadable": gaps,
        "page_label_policy": "物理页=page，印刷页=page_label；缺标签记 label_source=absent，不用物理页冒充印刷页",
        "extraction_chain": "内联正文 → 侧车 .txt/.ocr.txt（可带 .ocr.json 置信度）→ pdf_min 文本层 → DOCX zip+xml → 纯文本 → 诚实 gap",
    }
    res.evidence["library"] = {
        **payload["library_report"],
        "integrity": integrity,
        "hash_policy": "逐文件 SHA-256（字节级）；扫描时与流程结束时各取一次，全部一致才记 hashes_unchanged=true",
        "two_column_policy": "PDF 页按 x 间距聚类列（启发式），two_column=true 时 found_on 附阅读顺序提示；引文跨栏会逐字失败，走模糊匹配并如实标注",
    }
    res.evidence["handoff"] = handoff_info or {"written": False, "note": "dry-run 不落盘"}
    res.evidence["type_color"] = {
        "type_table_version": taxonomy.get("version"),
        "types": taxonomy.get("types", {}),
        "colors": taxonomy.get("colors", {}),
        "unknown_color_annotations": unknown_color,
        "unknown_type_annotations": unknown_type,
        "note": "类型先分高亮/划线/页边/手写，再谈语义；未知颜色不猜含义",
    }
    res.evidence["anchors"] = {
        "summary": counts,
        "per_annotation": [{"annotation_id": r["annotation_id"], "status": r["anchor"]["status"],
                            "declared_page": r["page"], "declared_page_label": r["page_label"],
                            "corrected_page": r["anchor"].get("corrected_page"),
                            "corrected_char_range": r["anchor"].get("corrected_char_range"),
                            "char_span_checked": r["anchor"].get("char_span_checked"),
                            "two_column_hint": r["anchor"].get("two_column_hint"),
                            "fuzzy": r["anchor"].get("fuzzy"),
                            "reason": r["anchor"].get("reason", "")} for r in ann_rows],
        "method": ("引文先在提取文本中做大小写归一逐字定位（命中后自检 char_span 切片与引文一致）；"
                   "逐字失败降级 difflib 模糊匹配，达阈值如实标 fuzzy_matched（不作字符级锚点），"
                   "低于阈值记 not_found；页/标签双口径比对；双栏页给阅读顺序提示；OCR 低置信降级"),
    }
    res.evidence["notes"] = {
        "schema": ["note_id", "annotation_ids", "aspects", "claim", "support_quote", "source", "type",
                   "color", "tags", "ocr_degraded", "usable", "confidence"],
        "confidence_formula": "confidence = anchor_status 权重 × 类型系数(未知类型 0.85) × 引文系数(无引文 0.7) × OCR 降级系数(0.8)",
        "annotation_note_map": {n["note_id"]: n["annotation_ids"] for n in notes},
        "orphan": orphan,
    }
    res.evidence["links"] = {
        "rule": cfg.get("link_rule", ""),
        "min_shared_terms": cfg.get("thresholds", {}).get("link_min_shared_terms"),
        "max_per_note": cfg.get("thresholds", {}).get("link_max_per_note"),
        "kept": len(links), "suppressed": len(skipped_links),
        "suppressed_detail": skipped_links[:50],
    }
    res.evidence["retrieval"] = {
        "entry_points": index["entry_points"],
        "method": "BM25（kit.bm25_score，文档侧保留词频）+ 摘录式回答 + 原文回链；召回不足给缺词清单",
        "bm25_params": {"k1": 1.2, "b": 0.75},
        "lookup_threshold": cfg.get("thresholds", {}).get("lookup_min_bm25_score", 0.5),
        "lookups": lookups,
    }
    res.evidence["ops_log"] = {"entries": ops, "executed": False,
                               "note": "全部为建议；输入库前后哈希对照见 evidence.library.integrity"}

    res.bind(payload, aliases={"批注": "annotations", "注释": "annotations", "笔记": "notes", "沉淀": "notes",
                               "主结果": "notes", "证据记录": "evidence", "状态说明": "status",
                               "文件库": "library_report", "盘点": "library_report",
                               "操作": "ops_log", "恢复": "ops_log"})
    bind_contract(res, payload, rules["projection"])
    res.done("文件库盘点", "批注抽取与类型/颜色归一", "锚点校正", "笔记结构化", "知识关联与抑制",
             "检索闭环（BM25+回链）", "恢复建议与交接清单")
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
        "annotations": [],
        "notes": [{"code": "R_INPUT_ERROR", "reason": str(exc)}],
        "links": [],
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
                          "notes": len(payload.get("notes", []))}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "annotations": len(payload.get("annotations", [])),
                      "notes": len(payload.get("notes", [])),
                      "links": len(payload.get("links", [])),
                      "library_mode": payload.get("library_report", {}).get("mode"),
                      "handoff": payload.get("evidence", {}).get("handoff", {}).get("manifest_json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
