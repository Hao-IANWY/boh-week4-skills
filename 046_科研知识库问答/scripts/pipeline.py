#!/usr/bin/env python3
"""046 科研知识库问答 —— 主流程 v0.2.0（纯标准库，离线、确定性、可仲裁）。

输入：case.json（两种材料形态皆支持）+ task.md
  A. 文件库模式：case 给出 materials_root / library_path 等库目录（或带路径的文件清单）
     → 目录扫描：逐文件 SHA-256/大小/类型/权限 → 同哈希异名 alias 合并 →
     按管理性版本说明（version_notes/admin_notes/versions 或 metadata.version /
     文件名模式）分版本组，canonical=版本序最大者。权限不足/加密/损坏/无文本层
     显式记码，绝不静默跳过。
  B. 内联模式：files / documents / knowledge_base / materials 直接给正文
     （pages[]/text），兼容旧版行为。

文本获取链（逐文件记录 extraction_method + 文本哈希 + 字符数，全部失败诚实 gap）：
  内联正文 → 侧车 .txt/.ocr.txt → pdf_min 提取 PDF 文本层 → zip+xml 读 DOCX 段落
  → 纯文本直读 → gap（R_NO_PERMISSION / R_FILE_ENCRYPTED / R_FILE_CORRUPT /
  R_NO_TEXT_LAYER / R_FILE_NO_EXTRACTOR 分别记码）。

六步闭环：
  1. 建库：目录扫描/内联盘点 → 切页 → 分块 → 台账（sha256/版本族/权限/可读性/
     提取方式）；同哈希副本与旧版本不重复计独立证据
  2. 检索：BM25（词频保留）+ 问题词覆盖率 → 候选块（低于阈值不取，凑不出就不作答）
  3. 取句：只从候选块抽原文整句（逐字），按问题关键词 IDF 覆盖率分
     direct / indirect / background
  4. 仲裁：同一主题数字不一致 / 极性相反 → conflicting 并列呈现；跨版本证据
     标 superseded；命中副本/旧版给 duplicate_copy / stale_version 提示
  5. 作答：答案 = 原文整句 + [claim→chunk] 引用标记拼接，不做改写式发挥；
     不确定性三态 supported / partially_supported / insufficient_evidence
  6. 缺证与操作：无达闸证据 → insufficient_evidence，写明缺哪些词、缺哪类文件，
     不拿常识补答案，不做向量兜底；恢复/索引操作只写建议与依据到 ops_log，
     绝不改写输入库，每文件前后哈希对照留证
"""

from __future__ import annotations

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
import pdf_min  # noqa: E402

SKILL = "research-kb-qa"
RULES = Path(__file__).resolve().parent.parent / "rules"
TEXT_EXTS = {"md", "txt", "markdown", "json", "csv", "tex", "log"}
DOC_EXTS = {"docx"}
LIBRARY_KEYS = ("materials_root", "library_path", "library", "library_dir", "library_root", "material_root")
VERSION_NOTE_KEYS = ("version_notes", "admin_notes", "versions")
_VERSION_TOKEN = re.compile(r"(draft|final|v(?:er|ersion)?[._ ]?(\d+)|rev[._ ]?(\d+)|latest|copy|backup)", re.I)
_FALLBACK_DOC = re.compile(r"(draft|final|v(?:er|ersion)?[._ ]?\d+|rev[._ ]?\d+|latest|copy|backup)", re.I)


def load_rules() -> dict:
    return {
        "qa": load_json(RULES / "qa_rules.json", default={}) or {},
        "schema": load_json(RULES / "kb_schema.json", default={}) or {},
        "projection": load_json(RULES / "contract_projection.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 文件库盘点：目录扫描、逐文件哈希、别名/版本组
# --------------------------------------------------------------------------------------
def file_sha256(path: Path) -> str:
    """逐文件 SHA-256（字节级）；权限不足等 OSError 向上抛，由调用方诚实记码。"""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_library_root(case: dict, case_dir: Path, work: Path) -> tuple:
    """解析库目录引用；相对路径按 case 目录 → 祖先 input/ → work → cwd → /input 回退。"""
    ref = first_present(case, *LIBRARY_KEYS)
    if ref is None:
        kb = case.get("knowledge_base")
        if isinstance(kb, dict):
            ref = first_present(kb, "root", "path", "dir", "library_root", "materials_root")
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
    """递归扫描库目录（跳过隐藏文件与侧车），逐文件 SHA-256+大小+权限；输出确定性排序。

    权限不足（chmod 0 / ACL 拒绝）不静默跳过：记录 read_error，后续在提取链里
    记 R_NO_PERMISSION。侧车文件（<f>.ocr.txt / <f>.ocr.json / 二进制文件的
    <f>.txt 伴生件）是提取辅助，不作为库文档本身建档，否则同一正文会以
    「文档+侧车」双份入库。
    """
    raw = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        read_error = ""
        try:
            sha = file_sha256(p)
        except OSError as exc:
            sha, read_error = None, f"{type(exc).__name__}: {exc}"
        raw.append({"path": rel, "name": p.name, "ext": p.suffix.lstrip(".").lower(),
                    "size": p.stat().st_size, "sha256": sha, "read_error": read_error, "_abs": p})
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
# 文本获取链：侧车 → PDF → DOCX → 纯文本 → 诚实 gap
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
        return [{"page": 1, "page_label": "", "label_source": "absent", "text": text}]
    pages = []
    for seg in text.split("[[page:")[1:]:
        m = re.match(r"(\d+)(?:\|([^\]]*))?\]\]", seg)
        if not m:
            continue
        pages.append({"page": int(m.group(1)), "page_label": (m.group(2) or "").strip(),
                      "label_source": "explicit" if (m.group(2) or "").strip() else "absent",
                      "text": seg[m.end():].lstrip("\n")})
    return pages or [{"page": 1, "page_label": "", "label_source": "absent", "text": text}]


def acquire_text(rec: dict, cfg: dict) -> dict:
    """按内联→侧车→PDF→DOCX→纯文本→gap 链取正文；记录 extraction_method/字符数/文本哈希。"""
    info = {"extraction_method": "", "n_chars": 0, "text_sha256": "", "pages": [],
            "ocr_confidence": None, "warnings": [], "gap": None, "gap_code": ""}
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
                              "text": str(body or "")})
            else:
                pages.append({"page": i, "page_label": "", "label_source": "absent", "text": str(p)})
        return finish(pages, "inline_case_json")
    if str(rec.get("inline_text") or "").strip():
        return finish(split_inline_pages(str(rec["inline_text"])), "inline_case_json")

    local = rec.get("_abs")
    if rec.get("read_error"):
        info["gap"], info["gap_code"] = ("文件无读取权限（%s）：未进入检索语料，不当作已检索"
                                         % rec["read_error"]), "R_NO_PERMISSION"
        return info
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
        if conf is not None and conf < floor:
            info["warnings"].append(f"OCR 平均置信度 {conf} 低于阈值 {floor}：该文件上的证据降级")
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
                  "text": pg["text"]} for pg in got["pages"]]
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
                        "text": "\n".join(paras)}], "docx_xml")
    if ext in TEXT_EXTS:
        txt = read_text(local)
        if txt.strip():
            return finish(split_inline_pages(txt), "plain_text")
    info["gap"], info["gap_code"] = f"无可用提取器（扩展名 .{ext or '未知'} 不在侧车/PDF/DOCX/纯文本链内）", "R_FILE_NO_EXTRACTOR"
    return info


# --------------------------------------------------------------------------------------
# 材料记录构建（扫描 + 内联合并）与输入只读证明
# --------------------------------------------------------------------------------------
def build_library(case: dict, case_dir: Path, work: Path) -> dict:
    root, tried, root_warn = resolve_library_root(case, case_dir, work)
    notes_by_path = parse_version_notes(case)
    recs, mode = [], "inline" if root is None else "library"

    if root is not None:
        scanned = scan_library_files(root)
        for f in scanned:
            meta_notes = match_note_for(norm(f["path"]), notes_by_path) or {}
            stem_version = version_from_text(f["name"].rsplit(".", 1)[0])
            rec = {"file_id": "lib-" + stable_id("lib", f["path"], n=8), "source": "scan",
                   "path": f["path"], "name": f["name"], "ext": f["ext"], "size": f["size"],
                   "file_sha256": f["sha256"], "read_error": f["read_error"], "_abs": f["_abs"],
                   "title": str(meta_notes.get("doc", "") or ""),
                   "metadata": {"version_hint": meta_notes.get("version", ""),
                                "admin_note": meta_notes.get("note", "")},
                   "version": (meta_notes.get("version", "") or stem_version),
                   "alias_of": None, "doc_key": "", "version_basis": "",
                   "pages_raw": None, "inline_text": ""}
            rec["path_norm"] = norm(rec["path"])
            recs.append(rec)
    for idx, item in enumerate(as_list(first_present(case, "files", "documents", "knowledge_base",
                                                     "materials", "docs", default=[])), start=1):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            continue
        path_str = str(first_present(item, "path", "file", "filepath", "name", default="") or "").strip()
        pages_raw = first_present(item, "pages", "page_list")
        inline_text = first_present(item, "text", "content", "body", "markdown", default="")
        if isinstance(inline_text, list):
            inline_text = "\n".join(str(x) for x in inline_text)
        inline_text = str(inline_text or "")
        if not path_str and not inline_text.strip() and not pages_raw:
            continue  # 配置型字典（如 knowledge_base: {"root": ...}）不是文档，不建档
        name = str(first_present(item, "name", "filename", default="") or Path(path_str).name)
        fid = str(first_present(item, "file_id", "id", "doc_id", default="")
                  or stable_id("kb", path_str or name, idx, n=8))
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for key in ("year", "project_id", "version", "permission", "scanned", "file_type",
                    "authors", "doi", "batch", "title"):
            if key in item and key not in meta:
                meta[key] = item[key]
        local = Path(path_str) if path_str else None
        if local is not None and not local.is_absolute():
            for base in (case_dir, case_dir.parent, work or Path("."), Path("/input")):
                if (base / local).is_file():
                    local = base / local
                    break
        ext = (Path(name or path_str).suffix.lstrip(".").lower()
               or str(meta.get("file_type", "") or "").lower())
        scanned_match = None
        if root is not None and path_str:
            tail = norm(path_str)
            scanned_match = next((r for r in recs if r["source"] == "scan"
                                  and (r["path_norm"] == tail or r["path_norm"].endswith("/" + tail)
                                       or tail.endswith(r["path_norm"]))), None)
        if scanned_match is not None:
            scanned_match["file_id"] = scanned_match.get("file_id") or fid
            scanned_match["declared_by_case"] = True
            if isinstance(item.get("metadata"), dict):
                scanned_match["metadata"].update({k: v for k, v in item["metadata"].items() if v not in (None, "")})
            continue
        rec = {"file_id": fid, "source": "inline", "path": path_str or name, "name": name, "ext": ext,
               "size": None, "file_sha256": None, "read_error": "", "_abs": local,
               "title": str(first_present(item, "title", default="") or ""),
               "metadata": meta, "pages_raw": pages_raw, "inline_text": inline_text,
               "version": str(meta.get("version", "") or ""), "alias_of": None,
               "doc_key": "", "version_basis": ""}
        if local is not None and Path(local).is_file():
            try:
                rec["file_sha256"] = file_sha256(local)
                rec["size"] = local.stat().st_size
            except OSError as exc:
                rec["read_error"] = f"{type(exc).__name__}: {exc}"
        rec["path_norm"] = norm(rec["path"])
        recs.append(rec)

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
            "recs": recs, "alias_groups": alias_groups,
            "near_duplicate_hints": near_dups, "version_groups": version_groups,
            "version_notes_source": ", ".join(VERSION_NOTE_KEYS)}


def hash_snapshot(lib: dict) -> dict:
    return {r["path"]: r.get("file_sha256") for r in lib["recs"] if r.get("file_sha256")}


def verify_untouched(before: dict, lib: dict) -> dict:
    """流程结束时逐文件重算 SHA-256，与扫描时对照，证明输入库未被改写。

    权限不足文件无法重算哈希：如实列入 unchecked，不当作「已验证未变」也不当作「已变更」。
    """
    changed, unchecked, rechecked = [], [], 0
    for r in lib["recs"]:
        abs_p = r.get("_abs")
        if abs_p is None or not Path(abs_p).is_file():
            continue
        if r.get("read_error"):
            unchecked.append({"path": r["path"], "reason": r["read_error"]})
            continue
        if before.get(r["path"]) is None:
            continue
        rechecked += 1
        try:
            if file_sha256(Path(abs_p)) != before[r["path"]]:
                changed.append(r["path"])
        except OSError as exc:
            unchecked.append({"path": r["path"], "reason": f"{type(exc).__name__}: {exc}"})
    return {"files": len(before), "rechecked": rechecked, "hashes_unchanged": not changed,
            "changed": changed, "unchecked": unchecked, "input_modified": bool(changed),
            "policy": "输入库全程只读；本流程只在输出目录写 result.json"}


# --------------------------------------------------------------------------------------
# 恢复/索引操作记录（只建议，不执行）
# --------------------------------------------------------------------------------------
def build_ops_log(lib: dict, gaps: list) -> list:
    """工单要求的恢复/索引操作：只写建议与依据，绝不改写输入库。"""
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
                    "action": "建议旧版只读归档、回答与引用统一指向 canonical；未执行"})
    for gap in gaps:
        ops.append({"op": "repair_file_gap",
                    "targets": [gap["path"]],
                    "basis": gap["reason"],
                    "action": f"建议人工修复（代码 {gap['code']}：权限不足需授权、加密件需口令、损坏件需换源、无文本层扫描件需补 OCR）；本流程诚实跳过，不伪造文本"})
    ops.append({"op": "rebuild_index",
                "targets": ["canonical_files"],
                "basis": "哈希清单/别名归一/版本组已重建；索引键应以 canonical 文件为准，副本与旧版不作为独立证据",
                "action": "建议检索索引以 canonical 文件为键重建；本流程只给出建议，未写回任何索引文件"})
    for op in ops:
        op["input_readonly"] = True
    return ops


# --------------------------------------------------------------------------------------
# 建库：切页 → 分块 → 台账
# --------------------------------------------------------------------------------------
def split_pages(rec: dict) -> list:
    """旧版内联正文兜底切页（[[page:N|label]] 标记 / \\f 分页 / 单页）。"""
    text = rec.get("text") or ""
    if not text.strip():
        return []
    out = []
    for i, seg in enumerate(text.split("\f") if "\f" in text else [text], start=1):
        m = re.match(r"\s*\[\[page:(\d+)(?:\|([^\]]*))?\]\]\s*", seg)
        if m:
            label = (m.group(2) or "").strip()
            out.append({"page": int(m.group(1)), "page_label": label,
                        "label_source": "explicit" if label else "absent", "text": seg[m.end():]})
        else:
            out.append({"page": i, "page_label": "", "label_source": "absent", "text": seg})
    return out


def parse_heading(line: str, patterns: list):
    raw = line.strip()
    for pat in patterns:
        if not raw or any(ch in raw for ch in pat.get("reject_chars", [])):
            continue
        m = re.match(pat["regex"], raw)
        if not m:
            continue
        gd = pat.get("groups") or {}
        if gd.get("level_group"):
            lv = m.group(int(gd["level_group"]))
            level = lv.count(".") + 1 if pat.get("level_from_dots") else len(lv)
        else:
            level = int(pat.get("level", 1))
        title = (m.group(int(gd["title_group"])) if gd.get("title_group") else m.group(0)).strip()
        if pat.get("max_title_chars") and len(title) > int(pat["max_title_chars"]):
            continue
        return int(level), title
    return None


def build_kb(files: list, rules: dict) -> tuple:
    cfg = rules["qa"].get("chunking", {})
    max_lines = int(cfg.get("max_lines_per_chunk", 4))
    min_chars = int(cfg.get("min_chars_per_chunk", 12))
    patterns = rules["qa"].get("heading_patterns", [])
    chunks, canon_by_file, ledger = [], {}, []
    for rec in files:
        pages = rec.get("pages") or split_pages(rec)
        fid = rec["file_id"]
        acq = rec.get("_acq") or {}
        if not pages:
            canon_by_file[fid] = ""
            ledger.append({"file_id": fid, "path": rec["path"], "chunks": 0, "pages": 0, "chars": 0,
                           "size": rec.get("size"), "file_sha256": rec.get("file_sha256"),
                           "extraction_method": acq.get("extraction_method") or None,
                           "gap_code": acq.get("gap_code") or None})
            continue
        parts, cursor = [], 0
        before = len(chunks)
        for page in pages:
            body = page["text"]
            start = cursor
            parts.append(body)
            cursor = start + len(body) + 1
            stack, buf, buf_line, buf_char = [], [], None, 0
            offset = start
            lines = body.split("\n")
            for ln, line in enumerate(lines, start=1):
                line_start = offset
                offset += len(line) + 1
                head = parse_heading(line, patterns)
                if head:
                    if buf:
                        chunks.append(_chunk(fid, page, stack, buf, buf_line, ln - 1, buf_char, line_start - 1))
                        buf, buf_line = [], None
                    lv, title = head
                    stack = [x for x in stack if x[0] < lv] + [(lv, title)]
                    continue
                if buf_line is None:
                    buf_line, buf_char = ln, line_start
                buf.append(line)
                if len(buf) >= max_lines:
                    chunks.append(_chunk(fid, page, stack, buf, buf_line, ln, buf_char, line_start + len(line)))
                    buf, buf_line = [], None
            if buf:
                chunks.append(_chunk(fid, page, stack, buf, buf_line, len(lines), buf_char, offset))
        canon = "\n".join(parts)
        canon_by_file[fid] = canon
        mine = chunks[before:]
        ledger.append({"file_id": fid, "path": rec["path"], "pages": len(pages),
                       "page_labels": [p["page_label"] or None for p in pages],
                       "label_sources": sorted({p["label_source"] for p in pages}),
                       "chunks": len(mine), "chars": len(canon), "sha256": sha256_text(canon),
                       "size": rec.get("size"), "file_sha256": rec.get("file_sha256"),
                       "extraction_method": acq.get("extraction_method") or None,
                       "text_sha256": acq.get("text_sha256") or None,
                       "ocr_confidence": acq.get("ocr_confidence"),
                       "extraction_warnings": acq.get("warnings") or [],
                       "alias_of": rec.get("alias_of"),
                       "version": rec.get("version") or rec["metadata"].get("version"),
                       "version_basis": rec.get("version_basis") or None,
                       "doc_key": rec.get("doc_key") or None,
                       "year": rec["metadata"].get("year"),
                       "project_id": rec["metadata"].get("project_id"),
                       "permission": rec["metadata"].get("permission")})
    chunks = [c for c in chunks if len(c["text"]) >= min_chars]
    chunks.sort(key=lambda x: (x["file_id"], x["page"], x["line_from"]))
    for i, c in enumerate(chunks, start=1):
        c["chunk_id"] = f"K{i:04d}"
    return chunks, canon_by_file, ledger


def _chunk(fid, page, stack, lines, line_from, line_to, char_start, char_end):
    return {"file_id": fid, "page": page["page"], "page_label": page["page_label"],
            "label_source": page["label_source"], "section_path": [t for _, t in stack],
            "line_from": int(line_from or 1), "line_to": int(line_to or line_from or 1),
            "char_start": int(char_start), "char_end": int(char_end),
            "text": "\n".join(lines).strip()}


def dedupe_chunks(chunks: list, files: dict) -> tuple:
    """同一正文内容（归一后哈希）只保留一个来源文件：canonical 文件 > 非别名 > 年份新 > 版本高。"""
    def keep_rank(c):
        f = files[c["file_id"]]
        pen = 0
        if f.get("alias_of"):
            pen += 2  # 同哈希副本不做独立证据来源
        if f.get("_canonical") is False:
            pen += 1  # 版本组内非 canonical 旧版靠后
        year = str(f["metadata"].get("year") or "")[:4]
        year = int(year) if year.isdigit() else 0
        version = f.get("version") or f["metadata"].get("version") or ""
        return (pen, -year, version_rank(version), c["chunk_id"])

    by_hash = {}
    for c in chunks:
        key = sha256_text(norm(c["text"]))
        by_hash.setdefault(key, []).append(c)
    kept, dropped = [], []
    for key in sorted(by_hash):
        group = sorted(by_hash[key], key=keep_rank)
        kept.append(group[0])
        for other in group[1:]:
            dropped.append({"chunk_id": other["chunk_id"], "file_id": other["file_id"],
                            "duplicate_of_chunk": group[0]["chunk_id"], "duplicate_of_file": group[0]["file_id"],
                            "code": "R_DUP_CONTENT",
                            "note": "同一正文内容已在 canonical/优先文件中计过一次，副本不重复计独立证据"})
    kept.sort(key=lambda c: c["chunk_id"])
    return kept, dropped


# --------------------------------------------------------------------------------------
# 取句与仲裁
# --------------------------------------------------------------------------------------
def split_sentences(text: str, terminators: list) -> list:
    """整句切分；英文句号/叹号/问号只在「后接空白/大写/结尾」时算终止，避免 1.0 mL 被切碎。"""
    out, buf = [], ""
    for i, ch in enumerate(text):
        buf += ch
        if ch in terminators:
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if ch in ".!?" and nxt and not (nxt.isspace() or nxt.isupper()):
                continue
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


_NUM = re.compile(r"\d+(?:\.\d+)?")
# 多字符单位排在单字符单位之前；忽略大小写（匹配对象是经 norm 小写化的文本，
# 否则 "96 khz" 永远配不上字面 "kHz"，同单位不同数值冲突会漏检）
_QTY = re.compile(
    r"(?P<v>\d+(?:\.\d+)?)\s*(?P<u>khz|hz|mmol|mm|nm|mM|ms|min|ml|µm|um|mol|au|mg|kg|µg|ug|rpm|kda|bp|m|s|l|g|%|℃)",
    re.I,
)


def numbers_in(text: str) -> list:
    seen, out = set(), []
    for m in _NUM.finditer(text):
        if m.group(0) not in seen:
            seen.add(m.group(0))
            out.append(m.group(0))
    return out


def quantities_near(text: str, key: str, window: int) -> list:
    """取与某个关键词最近的「数值+单位」（用于判断同一主题的量是否互斥）。"""
    nt, nk = norm(text), norm(key)
    if not nt or not nk:
        return []
    key_pos = [m.start() for m in re.finditer(re.escape(nk), nt)]
    if not key_pos:
        return []
    out = []
    for m in _QTY.finditer(nt):
        dist = min(abs(m.start() - kp) for kp in key_pos)
        if dist <= window:
            out.append({"value": m.group("v"), "unit": m.group("u"), "dist": dist})
    out.sort(key=lambda x: x["dist"])
    return out[:3]


def polarity_hits(text: str, pairs: list) -> list:
    tn = norm(text)
    hits = []
    for pair in pairs:
        pos, neg = pair[0], pair[1]
        if norm(pos) in tn:
            hits.append(("+", pos))
        if norm(neg) in tn:
            hits.append(("-", neg))
    return hits


def df_map(keys: list, kept_chunks: list, doc_tokens: dict) -> dict:
    out = {}
    for k in keys:
        out[k] = sum(1 for c in kept_chunks if k in doc_tokens[c["chunk_id"]])
    return out


def idf_of(df: int, n: int) -> float:
    import math

    return math.log(1 + (n - df + 0.5) / (df + 0.5))


def build_claims(question: dict, cand_chunks: list, rules: dict, files: dict, df: dict, n_chunks: int) -> list:
    """从候选块里抽原文整句；给每句算 IDF 覆盖（区分「真的回答了问题」和「只是提到相关词」）。"""
    cfg = rules["qa"]
    term = cfg.get("sentence_terminators", ["。", "！", "？", ".", "!", "?"])
    min_overlap = float(cfg.get("thresholds", {}).get("min_claim_overlap", 0.15))
    direct = float(cfg.get("thresholds", {}).get("direct_overlap", 0.5))
    indirect = float(cfg.get("thresholds", {}).get("indirect_overlap", 0.25))
    max_claims = int(cfg.get("thresholds", {}).get("max_claims", 8))
    keys = question["keys"]
    total_idf = sum(idf_of(df.get(k, 0), n_chunks) for k in keys) or 1.0
    claims = []
    for c in cand_chunks:
        f = files[c["file_id"]]
        if f.get("alias_of"):
            origin = "duplicate_copy"
        elif f.get("_canonical") is False:
            origin = "stale_version"
        else:
            origin = "canonical"
        for sent in split_sentences(c["text"], term):
            nt = norm(sent)
            hit = [k for k in keys if k in nt]
            if not hit:
                continue
            overlap = len(hit) / max(1, len(keys))
            if overlap < min_overlap:
                continue
            got_idf = sum(idf_of(df.get(k, 0), n_chunks) for k in hit)
            idf_cov = clamp(got_idf / total_idf)
            strength = "direct" if idf_cov >= direct else ("indirect" if idf_cov >= indirect else "background")
            claims.append({
                "claim_id": "",
                "text": sent,
                "overlap": rounded(overlap),
                "idf_coverage": rounded(idf_cov),
                "matched_keys": sorted(hit),
                "strength": strength,
                "citation": {
                    "chunk_id": c["chunk_id"],
                    "file_id": c["file_id"],
                    "path": f["path"],
                    "sha256": sha256_text(norm(c["text"])),
                    "file_sha256": f.get("file_sha256"),
                    "size": f.get("size"),
                    "extraction_method": (f.get("_acq") or {}).get("extraction_method") or None,
                    "alias_of": f.get("alias_of"),
                    "doc_key": f.get("doc_key") or None,
                    "version": f.get("version") or f["metadata"].get("version"),
                    "version_basis": f.get("version_basis") or None,
                    "year": f["metadata"].get("year"),
                    "evidence_origin": origin,
                    "page": c["page"],
                    "page_label": c["page_label"],
                    "label_source": c["label_source"],
                    "section_path": c["section_path"],
                    "line_from": c["line_from"],
                    "line_to": c["line_to"],
                    "char_range": [c["char_start"], c["char_end"]],
                },
            })
    claims.sort(key=lambda x: (-x["idf_coverage"], -x["overlap"], x["citation"]["chunk_id"]))
    claims = claims[:max_claims]
    for i, cl in enumerate(claims, start=1):
        cl["claim_id"] = f"A{i}"
    return claims


def detect_conflicts(claims: list, rules: dict) -> list:
    pairs = rules["qa"].get("polarity_pairs", [])
    window = int(rules["qa"].get("thresholds", {}).get("quantity_window_chars", 30))
    conflicts = []
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            a, b = claims[i], claims[j]
            shared = sorted(set(a["matched_keys"]) & set(b["matched_keys"]))
            if not shared:
                continue
            qa, qb = [], []
            for k in shared:
                for q in quantities_near(a["text"], k, window):
                    qa.append(dict(q, key=k))
                for q in quantities_near(b["text"], k, window):
                    qb.append(dict(q, key=k))
            hit = None
            for x in qa:
                for y in qb:
                    if x["unit"] == y["unit"] and x["value"] != y["value"]:
                        hit = (x, y)
                        break
                if hit:
                    break
            if hit:
                conflicts.append({"type": "numeric_conflict", "claims": [a["claim_id"], b["claim_id"]],
                                  "shared_keys": shared,
                                  "detail": f"同一主题（{hit[0]['key']}）的量不一致：{hit[0]['value']}{hit[0]['unit']} vs {hit[1]['value']}{hit[1]['unit']}",
                                  "resolve": "并列呈现，按版本/口径自行判断；不替作者择一"})
                continue
            sa = {s for s, _ in polarity_hits(a["text"], pairs)}
            sb = {s for s, _ in polarity_hits(b["text"], pairs)}
            if sa and sb and (("+" in sa and "-" in sb) or ("-" in sa and "+" in sb)):
                conflicts.append({"type": "polarity_conflict", "claims": [a["claim_id"], b["claim_id"]],
                                  "shared_keys": shared,
                                  "detail": "极性相反（一条肯定、一条否定同一表述）",
                                  "resolve": "并列呈现，不写成一致结论"})
    return conflicts


def version_flags(claims: list) -> list:
    """同一问题的证据跨版本：旧版本证据保留并标 superseded，作答说明以最新版本为准。

    版本新旧按管理性版本序（final>v(n)>draft）判定，不按字符串字典序。
    """
    versions = sorted({str(c["citation"].get("version") or "") for c in claims if c["citation"].get("version")},
                      key=version_rank)
    if len(versions) < 2:
        return []
    newest = versions[-1]
    old_claims = []
    for c in claims:
        v = str(c["citation"].get("version") or "")
        if v and v != newest:
            c["superseded"] = True
            old_claims.append(c["claim_id"])
    if not old_claims:
        return []
    return [{"versions": versions, "current": newest, "superseded_claims": old_claims,
             "note": "证据跨版本：旧版本证据保留并标 superseded=true，作答时以最新版本为准，但不删除旧证据"}]


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def parse_questions(case: dict, task_text: str, rules: dict) -> list:
    raw = first_present(case, "questions", "asks", "queries", "qa_pairs", default=[])
    stop = set(norm(x) for x in rules["qa"].get("stopwords", []))
    limit = int(rules["qa"].get("scoring", {}).get("max_query_terms", 10))
    out = []
    for i, item in enumerate(as_list(raw), start=1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = str(first_present(item, "text", "question", "q", "content", default="")).strip()
        if not text:
            continue
        bad_chars = set(rules["qa"].get("key_stop_chars", []))
        keys = [str(k) for k in as_list(first_present(item, "keys", "keywords", default=[]))]
        if not keys:
            for tok in tokens(text):
                if tok in stop or tok.isdigit() or len(tok) < 2 or tok in keys:
                    continue
                if any(ch in tok for ch in bad_chars):
                    continue
                keys.append(tok)
                if len(keys) >= limit:
                    break
        out.append({"question_id": str(first_present(item, "question_id", "id", default="") or f"Q{i}"),
                    "text": text, "keys": keys,
                    "scope": first_present(item, "scope", "filters", default={}) or {}})
    if not out and task_text.strip():
        text = next((l.strip() for l in task_text.splitlines() if l.strip() and not l.startswith("#")), "")
        if text:
            keys = [t for t in tokens(text) if t not in stop and len(t) > 1][:limit]
            out = [{"question_id": "Q1", "text": text, "keys": keys, "scope": {}}]
    return out


def answer_uncertainty(verdict: str, listed: list, answer_claims: list, conflicts: list, cov: dict) -> dict:
    """答案级不确定性分级：supported / partially_supported / insufficient_evidence。

    只用库内证据判定：达闸直接证据且全部来自 canonical 文件且问题关键词全覆盖
    → supported；冲突、含间接/背景证据、证据来自副本或旧版本、关键词未全覆盖
    → partially_supported；无达闸证据 → insufficient_evidence（不借常识补答）。
    """
    if verdict == "insufficient":
        return {"level": "insufficient_evidence",
                "reason": "库内没有任何段落达到作答闸门；答案留空并给出缺词/缺文件说明，未用常识补答"}
    if conflicts:
        return {"level": "partially_supported",
                "reason": "证据互相冲突（数值/极性），已并列呈现，未择一"}
    if answer_claims and any(c["strength"] != "direct" for c in answer_claims):
        return {"level": "partially_supported",
                "reason": "作答含 indirect/background 强度证据：只覆盖问题的部分要素，其余要素库内无直接论述"}
    if any(c["citation"].get("evidence_origin") != "canonical" for c in listed):
        return {"level": "partially_supported",
                "reason": "证据来自副本/旧版本文件（duplicate_copy/stale_version），版本口径已标注，建议以 canonical 版本复核"}
    if cov.get("term_coverage", 1.0) < 1.0:
        return {"level": "partially_supported",
                "reason": "问题关键词未全部在库内出现，答案只覆盖已检索到的部分，缺的部分显式声明"}
    return {"level": "supported",
            "reason": "全部作答断言为 direct 强度、来自 canonical 文件，且问题关键词全覆盖"}


def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res._contract_orig.keys()))

    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).resolve().parent

    lib = build_library(case, case_dir, work)
    for rec in lib["recs"]:
        acq = acquire_text(rec, rules["qa"])
        rec["_acq"] = acq
        rec["pages"] = acq["pages"]
    hash_before = hash_snapshot(lib)
    gaps = [{"file_id": r["file_id"], "path": r["path"], "file_type": r["ext"],
             "code": r["_acq"]["gap_code"], "reason": r["_acq"]["gap"]}
            for r in lib["recs"] if r["_acq"]["gap_code"]]
    doc_canon = {vg["doc_key"]: vg["canonical"]["path"] for vg in lib["version_groups"]}
    for rec in lib["recs"]:
        cp = doc_canon.get(rec.get("doc_key") or "")
        rec["_canonical"] = (cp == rec["path"]) if cp else None
    files_list = lib["recs"]
    files = {f["file_id"]: f for f in files_list}
    questions = parse_questions(case, task_text, rules)
    res.log("library_intake", mode=lib["mode"], files=len(files_list), gaps=len(gaps),
            alias_groups=len(lib["alias_groups"]), version_groups=len(lib["version_groups"]),
            questions=len(questions))

    unreadable = gaps  # 统一口径：任何提取失败都列名，不静默跳过

    def library_report() -> dict:
        return {
            "mode": lib["mode"],
            "root": (lib["root"].name if lib["root"] is not None else None),
            "root_resolution": lib["root_tried"] or ["inline（无库目录，使用 case 内联材料）"],
            "root_warn": lib["root_warn"],
            "files_scanned": sum(1 for r in files_list if r["source"] == "scan"),
            "inline_entries": sum(1 for r in files_list if r["source"] == "inline"),
            "files": [{"file_id": r["file_id"], "path": r["path"], "ext": r["ext"],
                       "size": r.get("size"), "file_sha256": r.get("file_sha256"),
                       "alias_of": r.get("alias_of"), "version": r.get("version") or None,
                       "version_basis": r.get("version_basis") or None,
                       "doc_key": r.get("doc_key") or None,
                       "extraction_method": (r.get("_acq") or {}).get("extraction_method") or None,
                       "n_chars": (r.get("_acq") or {}).get("n_chars") or 0,
                       "gap_code": (r.get("_acq") or {}).get("gap_code") or None}
                      for r in sorted(files_list, key=lambda r: r["path"])],
            "alias_groups": lib["alias_groups"],
            "near_duplicate_hints": lib["near_duplicate_hints"],
            "version_groups": lib["version_groups"],
            "gaps": unreadable,
            "version_notes_fields": lib["version_notes_source"],
        }

    if not files_list or not questions:
        res.set("answers", [])
        res.status["overall"] = "empty"
        res.unmet("问答结果", "缺少知识库文件或问题集，未作答" if not files_list else "缺少问题集")
        if lib["root_warn"] and files_list:
            res.note(lib["root_warn"])
        elif lib["root_warn"]:
            res.unmet("文件库目录", lib["root_warn"])
        ops = build_ops_log(lib, gaps)
        res.set("library_report", library_report())
        res.set("ops_log", ops)
        res.evidence["kb"] = {"files": len(files_list), "questions": len(questions)}
        res.evidence["library"] = {**library_report(), "integrity": verify_untouched(hash_before, lib)}
        res.evidence["ops_log"] = {"entries": ops, "executed": False,
                                   "note": "全部为建议；输入库前后哈希对照见 evidence.library.integrity"}
        payload = {"answers": [], "insufficient": [], "conflicts": [], "coverage": [],
                   "library_report": library_report(), "ops_log": ops}
        res.bind(payload, aliases={"问答": "answers", "回答": "answers", "主结果": "answers",
                                   "文件库": "library_report", "盘点": "library_report", "台账": "library_report",
                                   "操作": "ops_log", "恢复": "ops_log",
                                   "证据记录": "evidence", "状态说明": "status"})
        bind_contract(res, payload, rules["projection"])
        res.done("文件库盘点" if files_list else "盘点")
        return res

    chunks, canon_by_file, ledger = build_kb(files_list, rules)
    kept_chunks, dup_dropped = dedupe_chunks(chunks, files)
    res.log("build_kb", chunks=len(chunks), kept=len(kept_chunks), dup_dropped=len(dup_dropped))

    if not kept_chunks:
        res.set("answers", [])
        res.status["overall"] = "empty"
        res.unmet("问答结果", "知识库无任何可检索 chunk（全部文件无文本层/权限不足/损坏）")
        ops = build_ops_log(lib, gaps)
        res.set("library_report", library_report())
        res.set("ops_log", ops)
        res.evidence["kb"] = {"ledger": ledger, "unreadable": unreadable}
        res.evidence["library"] = {**library_report(), "integrity": verify_untouched(hash_before, lib)}
        res.evidence["ops_log"] = {"entries": ops, "executed": False, "note": "全部为建议"}
        payload = {"answers": [], "insufficient": [], "conflicts": [], "coverage": [],
                   "library_report": library_report(), "ops_log": ops}
        res.bind(payload, aliases={"问答": "answers", "回答": "answers", "主结果": "answers",
                                   "文件库": "library_report", "盘点": "library_report", "台账": "library_report",
                                   "操作": "ops_log", "恢复": "ops_log",
                                   "证据记录": "evidence", "状态说明": "status"})
        bind_contract(res, payload, rules["projection"])
        res.done("文件库盘点")
        return res

    sc = rules["qa"].get("scoring", {})
    bm = sc.get("bm25", {})
    th = rules["qa"].get("thresholds", {})
    min_chunk = float(th.get("min_chunk_score", 0.2))
    min_cov = float(th.get("min_chunk_term_match", 0.25))
    top_k = int(th.get("max_candidate_chunks", 4))
    min_idf_cov = float(th.get("min_idf_coverage", 0.5))
    min_idf_secondary = float(th.get("min_idf_coverage_secondary", 0.3))
    doc_tokens = {c["chunk_id"]: tokens(c["text"] + " " + " ".join(c["section_path"]), unique=False) for c in kept_chunks}
    doc_norm = {c["chunk_id"]: norm(c["text"]) for c in kept_chunks}
    corpus = list(doc_tokens.values())

    answers, insufficient, all_conflicts, coverage_rows, traces = [], [], [], [], {}
    for q in questions:
        keys = q["keys"]
        scored = []
        for c in kept_chunks:
            b = bm25_score(keys, doc_tokens[c["chunk_id"]], corpus, k1=float(bm.get("k1", 1.2)), b=float(bm.get("b", 0.75)))
            hit = [k for k in keys if k in doc_tokens[c["chunk_id"]] or k in doc_norm[c["chunk_id"]]]
            tm = len(hit) / max(1, len(keys))
            scored.append({"chunk_id": c["chunk_id"], "bm25": b, "term_match": tm, "hit": hit})
        top_b = max([s["bm25"] for s in scored], default=0.0) or 1.0
        for s in scored:
            s["score"] = clamp(float(sc.get("weights", {}).get("bm25", 0.6)) * clamp(s["bm25"] / top_b)
                               + float(sc.get("weights", {}).get("term_match", 0.4)) * s["term_match"])
        scored.sort(key=lambda x: (-x["score"], x["chunk_id"]))
        traces[q["question_id"]] = {"keys": keys, "chunks_scored": len(scored),
                                    "top": [{"chunk_id": s["chunk_id"], "score": rounded(s["score"]),
                                             "bm25": rounded(s["bm25"]), "term_match": rounded(s["term_match"])}
                                            for s in scored[:5]]}
        cand_ids = [s["chunk_id"] for s in scored[:top_k] if s["score"] >= min_chunk and s["term_match"] >= min_cov]
        by_id = {c["chunk_id"]: c for c in kept_chunks}
        cand = [by_id[cid] for cid in cand_ids]
        df = df_map(keys, kept_chunks, doc_tokens)
        claims = build_claims(q, cand, rules, files, df, len(kept_chunks))
        ranked = sorted(keys, key=lambda k: (df.get(k, 0), k))
        top_keys = set(ranked[: max(2, int(round(len(ranked) * float(th.get("top_key_ratio", 0.3)))))])

        def qualifies(cl):
            hit_top = bool(set(cl["matched_keys"]) & top_keys)
            return (cl["idf_coverage"] >= min_idf_cov) or (hit_top and cl["idf_coverage"] >= min_idf_secondary)

        answer_claims = [c for c in claims if qualifies(c)]
        conflicts = detect_conflicts(claims, rules)
        conflicting_ids = {cid for row in conflicts for cid in row["claims"]}
        keep_ids = {c["claim_id"] for c in answer_claims} | conflicting_ids
        vflags = version_flags(claims)
        listed = [c for c in claims if c["claim_id"] in keep_ids]

        if conflicts and listed:
            verdict = "conflicting"
        elif answer_claims:
            verdict = "answered"
        else:
            verdict = "insufficient"

        cov_row = {"question_id": q["question_id"], "keys": keys,
                   "keys_in_corpus": sum(1 for k in keys if df.get(k, 0) > 0),
                   "keys_total": len(keys),
                   "term_coverage": rounded(sum(1 for k in keys if df.get(k, 0) > 0) / max(1, len(keys))),
                   "df": {k: df.get(k, 0) for k in keys},
                   "candidate_chunks": len(cand), "claims": len(claims),
                   "answer_claims": len(answer_claims), "verdict": verdict}
        uncertainty = answer_uncertainty(verdict, listed, answer_claims, conflicts, cov_row)
        cov_row["uncertainty"] = uncertainty["level"]

        # 副本/旧版本提示：命中非 canonical 来源时显式标注，不当独立新证据
        evidence_hints = []
        for c in listed:
            origin = c["citation"].get("evidence_origin")
            if origin == "duplicate_copy":
                evidence_hints.append({
                    "claim_id": c["claim_id"], "kind": "duplicate_copy",
                    "note": (f"断言来自同哈希副本 {c['citation']['path']}（alias_of={c['citation'].get('alias_of')}）："
                             f"同一正文已在 canonical 文件中计过，此处不作独立证据；建议引用统一指向 canonical")})
            elif origin == "stale_version":
                canon_path = doc_canon.get(c["citation"].get("doc_key") or "")
                evidence_hints.append({
                    "claim_id": c["claim_id"], "kind": "stale_version",
                    "note": (f"断言来自旧版本 {c['citation'].get('version')}（{c['citation']['path']}）；"
                             f"canonical={canon_path}：保留版本口径作答，防止版本串味")})

        if verdict == "insufficient":
            covered = [k for k in keys if df.get(k, 0) > 0]
            missing = [k for k in keys if df.get(k, 0) == 0]
            best_cov = max([c["idf_coverage"] for c in claims], default=0.0)
            def _relevant(g):
                return any(k and (norm(k) in norm(g["path"]) or norm(g["path"].rsplit(".", 1)[0]) in norm(k))
                           for k in keys)
            # 全部不可读文件如实列为「未检索」上下文（尤其问题关键词与文件名有交集者），
            # 说明召回不足的边界：这些内容若在库内，当前确实搜不到
            blocked = [{"path": g["path"], "code": g["code"], "filename_overlap": _relevant(g)}
                       for g in unreadable]
            insufficient.append({
                "question_id": q["question_id"], "question": q["text"],
                "code": "R_INSUFFICIENT_EVIDENCE",
                "reason": "库内没有段落达到「问题关键词 IDF 覆盖 ≥ 阈值」的要求，未用常识补答案",
                "unmatched_keys": missing,
                "keys_present_elsewhere": covered,
                "best_partial_overlap": {"claim_id": claims[0]["claim_id"] if claims else None,
                                         "idf_coverage": best_cov,
                                         "required": min_idf_cov,
                                         "note": "有相关段落但覆盖不足，只作检索证据，不作为答案"},
                "unreadable_files_not_searched": blocked,
                "what_is_missing": f"库内没有任何文件同时论述 {'/'.join(missing) if missing else '该问题'} 与其余关键词；需补充对应文件、章节或字段后再问",
                "searched": {"files": len(files_list), "chunks": len(kept_chunks),
                             "candidate_chunks": len(cand)},
            })
            answers.append({"question_id": q["question_id"], "question": q["text"], "verdict": verdict,
                            "answer_text": "", "citations": [], "claims": claims,
                            "conflicts": [], "version_flags": [], "evidence_hints": [],
                            "uncertainty": uncertainty, "missing": insufficient[-1]})
        else:
            markers = " ".join(f"{c['text']} [{c['claim_id']}]" for c in listed)
            answers.append({
                "question_id": q["question_id"],
                "question": q["text"],
                "verdict": verdict,
                "answer_text": markers,
                "citations": [{"claim_id": c["claim_id"], "chunk_id": c["citation"]["chunk_id"],
                               "file_id": c["citation"]["file_id"], "path": c["citation"]["path"],
                               "file_sha256": c["citation"]["file_sha256"],
                               "extraction_method": c["citation"]["extraction_method"],
                               "alias_of": c["citation"]["alias_of"],
                               "doc_key": c["citation"]["doc_key"],
                               "version": c["citation"]["version"],
                               "version_basis": c["citation"]["version_basis"],
                               "evidence_origin": c["citation"]["evidence_origin"],
                               "page": c["citation"]["page"],
                               "page_label": c["citation"]["page_label"],
                               "section_path": c["citation"]["section_path"],
                               "line_from": c["citation"]["line_from"], "line_to": c["citation"]["line_to"],
                               "char_range": c["citation"]["char_range"],
                               "strength": c["strength"], "superseded": bool(c.get("superseded"))} for c in listed],
                "used_claims": [c["claim_id"] for c in listed],
                "claims": claims,
                "conflicts": conflicts,
                "version_flags": vflags,
                "evidence_hints": evidence_hints,
                "uncertainty": uncertainty,
                "missing": {},
            })
        if conflicts:
            all_conflicts.append({"question_id": q["question_id"], "conflicts": conflicts})
        coverage_rows.append(cov_row)

    answered = sum(1 for a in answers if a["verdict"] in ("answered", "conflicting"))
    if answered == 0:
        res.status["overall"] = "empty"
        res.unmet("问答结果", "全部问题在库内均无达到阈值的证据，已逐题给出缺证说明")
    elif insufficient:
        res.status["overall"] = "partial"
        for item in insufficient:
            res.unmet(f"问题缺证:{item['question_id']}", "R_INSUFFICIENT_EVIDENCE — 库内无支撑段落，未用常识补答案")
    else:
        res.status["overall"] = "complete"
    if all_conflicts:
        for row in all_conflicts:
            res.note(f"{row['question_id']} 存在冲突证据，已并列呈现，未择一写入结论")
    if dup_dropped:
        res.note(f"{len(dup_dropped)} 个 chunk 与其它文件正文重复，只保留一个来源（canonical 优先），避免同一证据被计两次")
    if lib["alias_groups"]:
        res.note(f"{len(lib['alias_groups'])} 组同哈希别名/完全重复文件已归并，副本不作独立证据来源")
    if lib["version_groups"]:
        res.note(f"{len(lib['version_groups'])} 个版本组已标 canonical，回答引用统一指向 canonical，旧版证据标 superseded/stale_version")
    if unreadable:
        by_code = {}
        for g in unreadable:
            by_code[g["code"]] = by_code.get(g["code"], 0) + 1
        res.unmet("文件库提取", "；".join(f"{v} 份 {k}" for k, v in sorted(by_code.items())) +
                  "——诚实跳过，不伪造文本层（详见 evidence.library.gaps）")
        res.unsure("不可读文件", f"{len(unreadable)} 个文件无文本层/加密/损坏/权限不足，未纳入检索范围（已列名）")
    if any("absent" in (row.get("label_sources") or []) for row in ledger):
        res.unsure("印刷页码", "部分文件无显式印刷页标签，page_label 留空并标 label_source=absent")
    partial_q = [a["question_id"] for a in answers if a["uncertainty"]["level"] == "partially_supported"]
    if partial_q:
        res.unsure("答案不确定性", f"{len(partial_q)} 题为 partially_supported（{', '.join(partial_q)}）：原因见各题 uncertainty.reason")
    ocr_low = [r["path"] for r in files_list
               if (r.get("_acq") or {}).get("ocr_confidence") is not None
               and r["_acq"]["ocr_confidence"] < float(th.get("ocr_confidence_floor", 0.85))]
    if ocr_low:
        res.unsure("OCR 置信度", f"{len(ocr_low)} 个文件 OCR 平均置信度低于阈值，其上的证据按降级处理：{', '.join(ocr_low)}")
    if lib["near_duplicate_hints"]:
        res.note(f"{len(lib['near_duplicate_hints'])} 对近重复（同归一化标题、内容哈希不同）仅提示，不自动合并")
    if lib["root_warn"]:
        if lib["mode"] == "library" or not files_list:
            res.unmet("文件库目录", lib["root_warn"])
        else:
            res.note(lib["root_warn"])

    ops = build_ops_log(lib, gaps)
    integrity = verify_untouched(hash_before, lib)
    if not integrity["hashes_unchanged"]:
        res.unmet("输入完整性", f"输入文件哈希发生变化：{integrity['changed'][:5]}")
    res.log("ops", ops=len(ops), integrity_ok=integrity["hashes_unchanged"],
            rechecked=integrity["rechecked"], unchecked=len(integrity["unchecked"]))

    lib_report = library_report()
    payload = {"answers": answers, "insufficient": insufficient,
               "conflicts": all_conflicts, "coverage": coverage_rows,
               "library_report": lib_report, "ops_log": ops}
    res.set("answers", answers)
    res.set("insufficient", insufficient)
    res.set("conflicts", all_conflicts)
    res.set("coverage", coverage_rows)
    res.set("library_report", lib_report)
    res.set("ops_log", ops)
    res.evidence["kb"] = {
        "files": len(files_list), "chunks": len(chunks), "retrievable_chunks": len(kept_chunks),
        "ledger": ledger,
        "chunk_table": [{"chunk_id": c["chunk_id"], "file_id": c["file_id"], "page": c["page"],
                         "page_label": c["page_label"], "section_path": c["section_path"],
                         "line_from": c["line_from"], "line_to": c["line_to"],
                         "char_range": [c["char_start"], c["char_end"]], "chars": len(c["text"])}
                        for c in kept_chunks[:300]],
        "unreadable": unreadable,
        "dedupe": {"dropped": dup_dropped[:50], "policy": rules["qa"].get("dedupe", {})},
    }
    res.evidence["library"] = {
        **lib_report,
        "integrity": integrity,
        "hash_policy": "逐文件 SHA-256（字节级）；扫描时与流程结束时各取一次，全部一致才记 hashes_unchanged=true；权限不足文件列 unchecked，不冒充已验证",
        "extraction_chain": "内联正文 → 侧车 .txt/.ocr.txt（可带 .ocr.json 置信度）→ pdf_min 文本层 → DOCX zip+xml → 纯文本 → 诚实 gap（R_NO_PERMISSION/R_FILE_ENCRYPTED/R_FILE_CORRUPT/R_NO_TEXT_LAYER/R_FILE_NO_EXTRACTOR）",
    }
    res.evidence["retrieval"] = {"queries": [traces[q["question_id"]] for q in questions if q["question_id"] in traces],
                                 "scoring": sc, "thresholds": th,
                                 "recompute": "chunk_score = w_bm25·(bm25/max_bm25) + w_term·term_match；claim 只取 overlap ≥ min_claim_overlap 的原文整句"}
    res.evidence["citation_rules"] = {"granularity": "原文整句逐字（不做改写）",
                                      "location": "文件+sha256+版本+页(双记)+章节+行区间+字符区间+提取方式+evidence_origin",
                                      "strength_levels": rules["qa"].get("strength_levels", {}),
                                      "conflict_rule": rules["qa"].get("conflict_rule", "")}
    res.evidence["coverage"] = coverage_rows
    res.evidence["uncertainty_policy"] = rules["qa"].get("uncertainty_levels", {})
    res.evidence["ops_log"] = {"entries": ops, "executed": False,
                               "note": "全部为建议；输入库前后哈希对照见 evidence.library.integrity"}

    res.bind(payload, aliases={"问答": "answers", "回答": "answers", "主结果": "answers",
                               "文件库": "library_report", "盘点": "library_report", "台账": "library_report",
                               "操作": "ops_log", "恢复": "ops_log",
                               "证据记录": "evidence", "状态说明": "status"})
    bind_contract(res, payload, rules["projection"])
    res.done("文件库盘点与哈希建档", "建库与去重（canonical 优先）", "检索取块", "取句与强度分级",
             "冲突仲裁与版本标注", "断言级引用拼装", "缺证声明", "恢复/索引建议与只读证明")
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
        "skill": {"name": SKILL, "version": "0.2.0"},
        "answers": [],
        "insufficient": [{"code": "R_INPUT_ERROR", "reason": str(exc)}],
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
                          "answers": len(payload.get("answers", []))}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    counts = {}
    for a in payload.get("answers", []):
        counts[a["verdict"]] = counts.get(a["verdict"], 0) + 1
    unc = {}
    for a in payload.get("answers", []):
        lv = (a.get("uncertainty") or {}).get("level")
        if lv:
            unc[lv] = unc.get(lv, 0) + 1
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "verdicts": counts, "uncertainty": unc,
                      "library_mode": payload.get("library_report", {}).get("mode")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
