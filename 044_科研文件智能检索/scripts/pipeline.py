#!/usr/bin/env python3
"""044 科研文件智能检索 —— 主流程（纯标准库，离线、确定性、可复算）。

输入：case.json（两种材料形态皆支持）+ task.md
输出：out/result.json（命中清单 / 定位锚点 / 过滤执行表 / 索引与检索证据 / 状态说明）
     + ops_log.json（工单要求的文件清单+哈希清单+恢复/索引建议，只写输出目录）

两种材料形态：
  A. 文件库模式：case 给出 library_path / materials_root 等库目录字符串路径
     → 递归扫描 → 逐文件 SHA-256+大小+类型+读权限 → 同哈希异名归并为 alias_of
     → 管理性版本说明（version_notes/admin_notes/versions 或 metadata.version /
     文件名模式）分版本组，canonical=版本序最大者。
  B. 内联模式：files[]/documents/materials 直接给正文（pages[]/text），兼容旧版行为。

文本获取链（逐文件记录 extraction_method / 字节哈希 / 字符数，全部失败则诚实 gap）：
  内联正文 → 侧车 .txt/.ocr.txt（可带 .ocr.json 置信度）→ pdf_min 提取 PDF 文本层
  → DOCX 纯 stdlib zip+xml 段落 → 纯文本直读 → gap
  （加密 /R_FILE_ENCRYPTED、损坏 /R_FILE_CORRUPT、无文本层 /R_NO_TEXT_LAYER、
   无提取器 /R_FILE_NO_EXTRACTOR、无读权限 /R_FILE_NO_PERMISSION 分别记码，不静默跳过）。

七步（每步都留痕，任何人可按下表复算）：
  1. 盘点：文件清单、扩展名、元数据、读权限、文本层可用性（无文本层不当作"已检索"）
  2. 切页：按 \\f 与 [[page:N|label]] 标记切页，印刷页与物理页分开记，缺标签记 absent 不编造
  3. 分块：标题树 + 定长窗口切 chunk，保留 页/行/字符区间/章节路径/段落首句/双栏提示
  4. 去重与版本：字节/正文哈希强判副本 → alias_of 合并为单一证据源；版本组 canonical，
     命中旧版给 stale_version 提示、命中文件的同哈希副本给 duplicate_copy 提示，不重复计数
  5. 过滤：项目号/年份/类型/版本/权限/批次/扫描件硬约束逐文件判定，不可判定即不满足
  6. 检索：BM25（文档侧保留词频）+ 概念级 term_match + 邻近窗口 + 标题命中，四因子加权；
     查询词扩展（缩写/同义/上下位，rules 词表驱动，逐条可解释，不引入向量库）
  7. 回链：每条命中给 文件+哈希+页(双记)+行+字符区间+段落首句+逐字片段，并做片段-原文自检

恢复/索引操作：只写建议与依据到 ops_log（绝不改写输入库）；每文件流程前后哈希对照留证。
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
    snippet,
    stable_id,
    tokens,
)
import pdf_min  # noqa: E402

SKILL = "research-file-retrieval"
RULES = Path(__file__).resolve().parent.parent / "rules"
TEXT_EXTS = {"md", "txt", "markdown", "json", "csv", "tsv", "yaml", "yml", "html", "htm", "tex", "log", "rst"}
DOC_EXTS = {"docx"}
SIDECAR_SUFFIXES = (".txt", ".text", ".ocr.txt")
LIBRARY_KEYS = ("library_path", "materials_root", "materials_dir", "library_root", "library_dir", "library")
VERSION_NOTE_KEYS = ("version_notes", "admin_notes", "versions")
_VERSION_TOKEN = re.compile(r"(draft|final|v(?:er|ersion)?[._ ]?(\d+)|rev[._ ]?(\d+)|latest|copy|backup)", re.I)
_FALLBACK_DOC = re.compile(r"(draft|final|v(?:er|ersion)?[._ ]?\d+|rev[._ ]?\d+|latest|copy|backup)", re.I)


# --------------------------------------------------------------------------------------
# 规则装载
# --------------------------------------------------------------------------------------
def load_rules() -> dict:
    retrieval = load_json(RULES / "retrieval_rules.json", default={}) or {}
    filters = load_json(RULES / "filter_dictionary.json", default={}) or {}
    projection = load_json(RULES / "contract_projection.json", default={}) or {}
    return {"retrieval": retrieval, "filters": filters, "projection": projection}


# --------------------------------------------------------------------------------------
# 文件库盘点：扫描、哈希、读权限
# --------------------------------------------------------------------------------------
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_library_root(case: dict, case_dir: Path, work: Path) -> tuple:
    """解析库目录引用（字符串路径才触发文件库模式）。

    case.library 为 dict 时仅在其 root 字段能解析到真实目录时启用文件库模式，
    否则按内联材料处理（示例 case 常带这种装饰性 library 字段）。
    """
    ref = first_present(case, *LIBRARY_KEYS)
    strict = True
    if isinstance(ref, dict):
        ref = first_present(ref, "root", "path", "dir", "directory")
        strict = False  # dict 形式的 library 字段多为装饰性元数据，解析失败按内联处理不记缺口
    if not isinstance(ref, str) or not ref.strip():
        return None, [], "", False
    raw = Path(str(ref).strip())
    if raw.is_absolute():
        return (raw if raw.is_dir() else None), [str(raw)], "", strict
    bases = [case_dir, case_dir.parent, work or Path("."), Path.cwd(), Path("/input")]
    tried, hit = [], None

    base_tags = ["<case_dir>", "<case_dir>/..", "<work>", "<cwd>", "/input"]

    def _rel(cand: Path, tag: str) -> str:
        # 记录口径与运行 cwd 无关：case 目录内候选 relativize，其余用稳定占位标签
        try:
            rel = cand.resolve().relative_to(case_dir.resolve())
            return rel.as_posix()
        except ValueError:
            return f"{tag}/{raw.as_posix()}"

    for base, tag in zip(bases, base_tags):
        cand = base / raw
        tried.append(_rel(cand, tag))
        if hit is None and cand.is_dir():
            hit = cand
    warn = "" if hit else "库目录不存在（已尝试：" + "；".join(tried) + "）"
    return hit, tried, warn, strict


def scan_library_files(root: Path) -> tuple:
    """递归扫描库目录（跳过隐藏文件与侧车），逐文件 SHA-256+大小+读权限。

    侧车文件（<f>.ocr.txt / <f>.ocr.json / 二进制文件的 <f>.txt 伴生件）是提取辅助，
    不作为库文档本身建档，否则同一正文会以「文档+侧车」双份入库。
    读权限不足的文件不静默跳过：记录 readable=false，由获取链记 R_FILE_NO_PERMISSION。
    """
    raw = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        readable = True
        size, sha = None, None
        try:
            size = p.stat().st_size
        except OSError:
            readable = False
        if readable:
            try:
                sha = file_sha256(p)
            except OSError:
                readable = False
                sha = None
        raw.append({"path": rel, "name": p.name, "ext": p.suffix.lstrip(".").lower(),
                    "size": size, "sha256": sha, "readable": readable, "_abs": p})
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

    return [f for f in raw if not is_sidecar(f)], sorted(f["path"] for f in raw if is_sidecar(f))


# --------------------------------------------------------------------------------------
# 版本说明与版本组
# --------------------------------------------------------------------------------------
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
    title = rec.get("title") or meta.get("title") or str(rec.get("name") or rec["path"]).rsplit(".", 1)[0]
    stripped = _FALLBACK_DOC.sub(" ", str(title))
    return norm_title(stripped) or norm_title(str(title))


def parse_version_notes(case: dict) -> dict:
    """管理性版本说明：{match/path/file, doc, version, note} → 按路径后缀归一匹配。"""
    notes = {}
    for key in VERSION_NOTE_KEYS:
        raw = case.get(key)
        if raw is None and key == "versions":
            lib = case.get("library")
            raw = lib.get("versions") if isinstance(lib, dict) else None
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


def build_version_groups(recs: list, notes_by_path: dict) -> list:
    """版本组：同一 doc_key 且带版本信息的文件归组；canonical=版本序最大者。"""
    groups = {}
    for rec in recs:
        note = match_note_for(rec["path_norm"], notes_by_path) or {}
        explicit_doc = str((note or {}).get("doc", "") or "")
        meta = rec.get("metadata") or {}
        version = str(meta.get("version", "") or "") or ((note or {}).get("version", "") if note else "")
        basis = "version_note" if (note and (note.get("version") or note.get("doc"))) else \
            ("metadata" if meta.get("version") else ("filename_pattern" if version else ""))
        if not version:
            rec["version"], rec["version_basis"], rec["doc_key"] = "", "", doc_key_of(rec, explicit_doc)
            continue
        rec["version"], rec["version_basis"] = version, basis
        rec["doc_key"] = doc_key_of(rec, explicit_doc)
        if basis == "filename_pattern":
            rec.setdefault("version_warnings", "版本取自文件名模式（启发式），管理性版本说明缺失")
        groups.setdefault(rec["doc_key"], []).append(rec)
    out = []
    for key in sorted(groups):
        members = groups[key]
        if len(members) < 2:
            continue
        max_rank = max(version_rank(r["version"]) for r in members)
        tops = [r for r in members if version_rank(r["version"]) == max_rank]
        canonical = sorted(tops, key=lambda r: (r.get("alias_of") is not None, r["path"]))[0]
        ranked = sorted(members, key=lambda r: r["path"])
        out.append({
            "doc_key": key,
            "canonical": {"path": canonical["path"], "version": canonical["version"]},
            "members": [{"path": r["path"], "version": r["version"], "basis": r["version_basis"],
                         "note": (match_note_for(r["path_norm"], notes_by_path) or {}).get("note", "")}
                        for r in ranked],
            "note_source": "version_note" if any(r["version_basis"] == "version_note" for r in members) else "mixed",
            "policy": "canonical=管理性版本序最大者（final>v(n)>draft，并列时非副本优先）；旧版只读归档，不删除",
        })
    return out


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
    """按 内联→侧车→PDF→DOCX→纯文本→gap 链取正文；记录 extraction_method/字符数/文本哈希。"""
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

    pages_raw = rec.get("pages_raw")
    if isinstance(pages_raw, list) and pages_raw:
        pages = []
        for i, p in enumerate(pages_raw, start=1):
            if isinstance(p, dict):
                body = first_present(p, "text", "content", "lines", "body", default="")
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

    local = rec.get("_abs")
    if local is None or not Path(local).is_file():
        if rec.get("readable") is False:
            info["gap"] = "文件存在但无读权限（chmod/ACL 拒绝读取）：不进入语料，如实标记，绝不绕过"
            info["gap_code"] = "R_FILE_NO_PERMISSION"
            return info
        if str(rec.get("inline_text") or "").strip():
            return finish(split_inline_pages(str(rec["inline_text"])), "inline_case_json")
        info["gap"] = "内联无正文且文件路径不可读（或不存在），文本获取失败"
        info["gap_code"] = "R_NO_TEXT_LAYER"
        return info

    if rec.get("readable") is False:
        info["gap"] = "文件存在但无读权限（chmod/ACL 拒绝读取）：不进入语料，如实标记，绝不绕过"
        info["gap_code"] = "R_FILE_NO_PERMISSION"
        return info

    if str(rec.get("inline_text") or "").strip():
        return finish(split_inline_pages(str(rec["inline_text"])), "inline_case_json")

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
            info["warnings"].append(f"OCR 平均置信度 {conf} 低于阈值 {floor}：该文件上的定位证据降级参考")
        method = "sidecar_ocr_text" if ".ocr." in str(sc_meta.get("sidecar", "")) else "sidecar_text"
        return finish(split_inline_pages(sc_txt), method, [f"侧车文本来自 {sc_meta.get('sidecar')}"])

    ext = rec.get("file_type", "")
    if ext == "pdf":
        got = pdf_min.read_pdf(local)
        if got["encrypted"]:
            info["gap"], info["gap_code"] = got["errors"][0], "R_FILE_ENCRYPTED"
            return info
        if got["errors"] and not got["ok"]:
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
            info["gap"], info["gap_code"] = "DOCX 无法解析出段落（可能损坏或加密容器）", "R_FILE_CORRUPT"
            return info
        return finish([{"page": 1, "page_label": "", "label_source": "absent",
                        "text": "\n".join(paras), "two_column": False}], "docx_xml")
    if ext in TEXT_EXTS:
        txt = read_text(local)
        if txt.strip():
            return finish(split_inline_pages(txt), "plain_text")
        info["gap"], info["gap_code"] = "纯文本文件为空或不可解码", "R_NO_TEXT_LAYER"
        return info
    info["gap"], info["gap_code"] = \
        f"无可用提取器（扩展名 .{ext or '未知'} 不在侧车/PDF/DOCX/纯文本链内）", "R_FILE_NO_EXTRACTOR"
    return info


# --------------------------------------------------------------------------------------
# 材料记录构建（文件库扫描 + 内联合并）
# --------------------------------------------------------------------------------------
def build_library(case: dict, case_dir: Path, work: Path) -> dict:
    root, tried, root_warn, strict = resolve_library_root(case, case_dir, work)
    notes_by_path = parse_version_notes(case)
    recs, mode = [], "inline" if root is None else "library"
    root_note = (root_warn if root_warn and strict else "")

    def push(rec):
        rec["path_norm"] = norm(rec["path"])
        recs.append(rec)

    if root is not None:
        scanned, sidecars = scan_library_files(root)
        for f in scanned:
            meta_notes = match_note_for(norm(f["path"]), notes_by_path) or {}
            stem_version = version_from_text(f["name"].rsplit(".", 1)[0])
            push({"file_id": "lib-" + stable_id("lib", f["path"], n=8), "source": "scan",
                  "path": f["path"], "name": f["name"], "file_type": f["ext"],
                  "size": f["size"], "file_sha256": f["sha256"], "readable": f["readable"],
                  "_abs": f["_abs"], "title": str(meta_notes.get("doc", "") or ""),
                  "metadata": {"version_hint": meta_notes.get("version", ""),
                               "admin_note": meta_notes.get("note", "")},
                  "pages_raw": None, "inline_text": "", "version": "",
                  "alias_of": None, "doc_key": "", "version_basis": ""})

    for idx, item in enumerate(as_list(first_present(case, "files", "library_files", "documents",
                                                     "materials", "corpus", default=[])), start=1):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            continue
        path_str = str(first_present(item, "path", "file", "filepath", "name", default="") or "").strip()
        name = str(first_present(item, "name", "filename", default="") or Path(path_str).name)
        fid = str(first_present(item, "file_id", "id", "doc_id", default="")
                  or stable_id("file", path_str or name, idx, n=8))
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for key in ("year", "project_id", "version", "permission", "scanned", "batch",
                    "file_type", "pages_total", "source"):
            if key in item and key not in meta:
                meta[key] = item[key]
        local = Path(path_str) if path_str else None
        if local is not None and not local.is_absolute():
            for base in (case_dir, case_dir.parent, work or Path("."), Path("/input")):
                if (base / local).is_file():
                    local = base / local
                    break
        pages_raw = first_present(item, "pages", "page_list", "segments")
        inline_text = first_present(item, "text", "content", "body", "lines", "markdown", default="")
        if isinstance(inline_text, list):
            inline_text = "\n".join(str(x) for x in inline_text)
        inline_text = str(inline_text or "")
        ext = (Path(name or path_str).suffix.lstrip(".").lower()
               or str(meta.get("file_type", "") or "").lower())
        scanned_match = None
        if root is not None and path_str:
            tail = norm(path_str)
            scanned_match = next((r for r in recs if r["source"] == "scan" and
                                  (r["path_norm"] == tail or r["path_norm"].endswith("/" + tail)
                                   or tail.endswith(r["path_norm"]))), None)
        if scanned_match is not None:
            if item.get("title"):
                scanned_match["title"] = str(item["title"])
            scanned_match["declared_by_case"] = True
            scanned_match["metadata"].update({k: v for k, v in meta.items() if v not in (None, "")})
            scanned_match["file_id"] = fid
            continue
        local_exists = bool(local and Path(local).is_file())
        push({"file_id": fid, "source": "inline", "path": path_str or name, "name": name,
              "file_type": ext, "size": (Path(local).stat().st_size if local_exists else None),
              "file_sha256": (file_sha256(local) if local_exists else None),
              "readable": True if local_exists else None,
              "_abs": local if local_exists else None, "title": str(first_present(item, "title", default="") or ""),
              "metadata": meta, "pages_raw": pages_raw, "inline_text": inline_text,
              "version": str(meta.get("version", "") or ""), "alias_of": None,
              "doc_key": "", "version_basis": ""})

    # 别名/完全重复：同哈希异名归并为单一证据源
    by_hash = {}
    for rec in recs:
        h = rec.get("file_sha256")
        if not h:
            text_probe = str(rec.get("inline_text") or "")
            if not text_probe.strip() and isinstance(rec.get("pages_raw"), list):
                text_probe = "\n".join(str(p.get("text", "")) if isinstance(p, dict) else str(p)
                                       for p in rec["pages_raw"])
            if text_probe.strip():
                h = "text:" + sha256_text(text_probe)
        if h:
            by_hash.setdefault(h, []).append(rec)
    alias_groups = []
    for h in sorted(by_hash):
        members = by_hash[h]
        if len(members) < 2:
            continue
        bait = re.compile(r"(copy|backup|latest|副本)", re.I)
        members_sorted = sorted(members, key=lambda r: (bool(bait.search(r["name"])), r["path"]))
        canonical = members_sorted[0]
        for r in members_sorted[1:]:
            r["alias_of"] = canonical["path"]
        alias_groups.append({"sha256": h.removeprefix("text:"), "kind": "alias_or_duplicate",
                             "canonical": canonical["path"],
                             "members": [r["path"] for r in members_sorted],
                             "size": canonical.get("size"),
                             "note": "同 SHA-256 不同文件名：完全重复/别名文件（canonical 优先非 copy/backup/latest 命名；"
                                     "合并为单一证据源，副本不进检索语料，不计独立命中）"})

    version_groups = build_version_groups(recs, notes_by_path)
    return {"root": root, "root_tried": tried, "root_warn": root_note, "mode": mode,
            "recs": recs, "alias_groups": alias_groups, "version_groups": version_groups,
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
        try:
            if file_sha256(Path(abs_p)) != before[r["path"]]:
                changed.append(r["path"])
        except OSError:
            changed.append(r["path"] + "（重算哈希失败）")
    return {"files": len(before), "rechecked": rechecked, "hashes_unchanged": not changed,
            "changed": changed, "input_modified": bool(changed),
            "policy": "输入库全程只读；本流程只在输出目录写 result.json 与 ops_log.json"}


# --------------------------------------------------------------------------------------
# 切页与分块（chunk 保留页/行/字符区间/章节路径/段落首句/双栏提示）
# --------------------------------------------------------------------------------------
def parse_heading(line: str, patterns: list):
    raw = line.strip()
    for pat in patterns:
        if not raw:
            continue
        if any(ch in raw for ch in pat.get("reject_chars", [])):
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


def first_sentence(text: str, terminators: list) -> str:
    body = re.sub(r"\s+", " ", text).strip()
    for i, ch in enumerate(body):
        if ch in terminators:
            return body[: i + 1].strip()
    return body[:120]


def build_index(files: list, rules: dict) -> tuple:
    """生成 chunk 级索引；返回 (chunks, file_index, canon_text_by_file)。"""
    cfg = rules["retrieval"].get("chunking", {})
    max_lines = int(cfg.get("max_lines_per_chunk", 5))
    patterns = rules["retrieval"].get("heading_patterns", [])
    terms = rules["retrieval"].get("sentence_terminators", ["。", "！", "？", ".", "!", "?"])

    chunks, file_index, canon_by_file = [], [], {}
    for rec in files:
        pages = rec.get("pages") or []
        fid = rec["file_id"]
        if not pages:
            file_index.append({"file_id": fid, "path": rec["path"], "pages": 0, "chunks": 0,
                               "chars": 0, "page_labels": [], "label_sources": []})
            canon_by_file[fid] = ""
            continue
        canon_parts, cursor = [], 0
        before = len(chunks)
        for page in pages:
            body = page["text"]
            start = cursor
            canon_parts.append(body)
            cursor = start + len(body) + 1
            stack = []
            page_lines = body.split("\n")
            buf, buf_start_line, buf_chars = [], None, 0
            offset = start
            for ln, line in enumerate(page_lines, start=1):
                line_start = offset
                offset += len(line) + 1
                head = parse_heading(line, patterns)
                if head:
                    if buf:
                        chunks.append(_mk_chunk(fid, page, stack, buf, buf_start_line, ln - 1, buf_chars,
                                                line_start - 1, first_sentence("\n".join(buf), terms)))
                        buf, buf_start_line = [], None
                    level, title = head
                    stack = [x for x in stack if x[0] < level] + [(level, title)]
                    continue
                if buf_start_line is None:
                    buf_start_line, buf_chars = ln, line_start
                buf.append(line)
                if len(buf) >= max_lines:
                    chunks.append(_mk_chunk(fid, page, stack, buf, buf_start_line, ln, buf_chars,
                                            line_start + len(line), first_sentence("\n".join(buf), terms)))
                    buf, buf_start_line = [], None
            if buf:
                chunks.append(_mk_chunk(fid, page, stack, buf, buf_start_line, len(page_lines), buf_chars,
                                        offset, first_sentence("\n".join(buf), terms)))
        canon = "\n".join(canon_parts)
        canon_by_file[fid] = canon
        mine = chunks[before:]
        file_index.append(
            {
                "file_id": fid,
                "path": rec["path"],
                "pages": len(pages),
                "page_labels": [p["page_label"] or None for p in pages],
                "label_sources": sorted({p["label_source"] for p in pages}),
                "two_column_pages": [p["page"] for p in pages if p.get("two_column")],
                "chunks": len(mine),
                "chars": len(canon),
                "sha256": sha256_text(canon),
                "extraction_method": (rec.get("_acq") or {}).get("extraction_method") or None,
            }
        )
    chunks = [c for c in chunks if len(c["text"]) >= int(cfg.get("min_chars_per_chunk", 20))]
    chunks.sort(key=lambda x: (x["file_id"], x["page"], x["line_from"]))
    for i, c in enumerate(chunks, start=1):
        c["chunk_id"] = f"C{i:04d}"
    return chunks, file_index, canon_by_file


def _mk_chunk(fid, page, stack, lines, line_from, line_to, char_start, char_end, first_sent):
    text = "\n".join(lines).strip()
    return {
        "file_id": fid,
        "page": page["page"],
        "page_label": page["page_label"],
        "label_source": page["label_source"],
        "section_path": [t for _, t in stack],
        "two_column": bool(page.get("two_column")),
        "line_from": int(line_from or 1),
        "line_to": int(line_to or line_from or 1),
        "char_start": int(char_start),
        "char_end": int(char_end),
        "text": text,
        "first_sentence": first_sent,
    }


# --------------------------------------------------------------------------------------
# 硬过滤
# --------------------------------------------------------------------------------------
def file_attr(rec: dict, spec: dict):
    if spec.get("source") == "derived":
        if spec.get("key") == "extension":
            return (rec.get("file_type") or "").lower()
        return rec.get(spec.get("key", ""))
    return rec.get("metadata", {}).get(spec.get("key", ""))


def evaluate_filters(rec: dict, conditions: dict, fdict: dict) -> list:
    rows = []
    specs = fdict.get("filters", {})
    codes = fdict.get("codes", {})
    for cond_key in sorted(conditions.keys()):
        spec = specs.get(cond_key)
        want = conditions[cond_key]
        if spec is None:
            rows.append({"filter": cond_key, "expected": want, "actual": None, "satisfied": False,
                         "evaluable": False, "code": "R_FILTER_UNSUPPORTED",
                         "reason": codes.get("R_FILTER_UNSUPPORTED", "")})
            continue
        actual = file_attr(rec, spec)
        kind = spec.get("type")
        evaluable = actual not in (None, "", [])
        ok, note = evaluable, "" if evaluable else "元数据缺失，按规则视为不满足（不放宽、不猜测）"
        if ok:
            if kind == "int_min":
                try:
                    ok = int(str(actual)[:4]) >= int(want)
                except (TypeError, ValueError):
                    ok, note = False, "目标值非数值，无法满足"
            elif kind == "int_max":
                try:
                    ok = int(str(actual)[:4]) <= int(want)
                except (TypeError, ValueError):
                    ok, note = False, "目标值非数值，无法满足"
            elif kind in ("in_list", "in_list_lower"):
                ok = norm(str(actual)) in [norm(str(x)) for x in as_list(want)]
            elif kind == "not_true":
                ok = not bool(actual)
                note = "" if ok else "命中被排除项（如扫描件）"
            elif kind == "bool_true":
                ok = bool(actual)
            elif kind == "exists":
                ok = True
        rows.append({"filter": cond_key, "expected": want, "actual": actual, "satisfied": bool(ok),
                     "evaluable": bool(evaluable), "code": "" if ok else spec.get("code", ""),
                     "reason": "" if ok else (note or codes.get(spec.get("code", ""), ""))})
    return rows


# --------------------------------------------------------------------------------------
# 检索（BM25 + 词表驱动的查询扩展，可解释、无向量库）
# --------------------------------------------------------------------------------------
def query_keys(qtext: str, stop: set, limit: int) -> list:
    out = []
    for tok in tokens(qtext):
        if tok in stop or tok.isdigit() or len(tok) < 2:
            continue
        if tok not in out:
            out.append(tok)
        if len(out) >= limit:
            break
    return out


def expand_query(text: str, keys: list, lexicon: dict) -> tuple:
    """查询词扩展：缩写/同义/上下位词表驱动。

    触发条件：词表任一词（归一后）是查询文本子串，或其词元集合 ⊆ 查询词元。
    扩展只增加词元，不改写原始查询；每个扩展词元带 group 出处，逐条可解释。
    """
    if not lexicon.get("enabled", True):
        return [], []
    ntext = norm(text)
    key_set = set(keys)
    expanded, groups_hit = [], []
    for g in lexicon.get("groups", []):
        gid = str(g.get("id", ""))
        terms = [norm(t) for t in g.get("terms", []) if norm(t)]
        if not terms or not gid:
            continue
        triggered = any(t in ntext or all(k in key_set for k in tokens(t)) for t in terms)
        if not triggered:
            continue
        groups_hit.append({"group": gid, "terms": [t for t in g.get("terms", []) if norm(t)]})
        seen = set(keys) | {e["key"] for e in expanded}
        for t in terms:
            for k in tokens(t):
                if k not in seen:
                    seen.add(k)
                    expanded.append({"key": k, "via": f"expansion:{gid}"})
    return expanded, groups_hit


def group_tokens(lexicon: dict) -> dict:
    out = {}
    for g in lexicon.get("groups", []):
        gid = str(g.get("id", ""))
        toks = []
        for t in g.get("terms", []):
            for k in tokens(str(t)):
                if k not in toks:
                    toks.append(k)
        out[gid] = toks
    return out


def proximity_score(norm_text: str, keys: list, window: int) -> tuple:
    """多关键词最小覆盖窗口（按归一文本字符位置）；只有单关键词命中时返回 0。"""
    events = []
    for k in keys:
        pos = norm_text.find(k)
        while pos != -1 and len(events) <= 400:
            events.append((pos, k))
            pos = norm_text.find(k, pos + 1)
    if len(events) < 2:
        return 0.0, None
    events.sort()
    best, left, count = None, 0, {}
    for right in range(len(events)):
        count[events[right][1]] = count.get(events[right][1], 0) + 1
        while len(count) >= 2 and left < right:
            span = events[right][0] - events[left][0]
            if best is None or span < best:
                best = span
            k = events[left][1]
            count[k] -= 1
            if count[k] == 0:
                del count[k]
            left += 1
    if best is None:
        return 0.0, None
    if best > window:
        return 0.0, best
    return clamp(1.0 - best / float(window)), best


def retrieve(chunks: list, queries: list, rules: dict) -> tuple:
    sc = rules["retrieval"].get("scoring", {})
    w = sc.get("weights", {})
    bm = sc.get("bm25", {})
    window = int(sc.get("proximity_window_chars", 60))
    lexicon = rules["retrieval"].get("query_expansion", {})
    gtoks = group_tokens(lexicon)
    stop = set(norm(x) for x in rules["retrieval"].get("stopwords", []))
    doc_tokens = {c["chunk_id"]: tokens(c["text"] + " " + " ".join(c["section_path"]), unique=False) for c in chunks}
    doc_norm = {c["chunk_id"]: norm(c["text"]) for c in chunks}
    corpus = list(doc_tokens.values())
    results, traces = {}, {}
    for q in queries:
        keys = q["keys"]
        expanded = q.get("expanded", [])
        groups_hit = q.get("groups_hit", [])
        keys_full = list(keys) + [e["key"] for e in expanded
                                  if e["key"] not in keys and e["key"] not in stop]
        via_of = {e["key"]: e["via"] for e in expanded}

        def present(cid, k):
            return k in set(doc_tokens[cid]) or k in doc_norm[cid]

        scored = []
        for c in chunks:
            cid = c["chunk_id"]
            bm25 = bm25_score(keys_full, doc_tokens[cid], corpus, k1=float(bm.get("k1", 1.2)),
                              b=float(bm.get("b", 0.75)))
            present_base = [k for k in keys if present(cid, k)]
            # 概念级 term_match = 均值(原始词命中率, 扩展组覆盖率)；无扩展组时退化为原始词命中率。
            # 组内任一变体命中即算覆盖：跨语言/缩写对齐命中不会被不可译的表面词元稀释。
            covered_groups = []
            for gh in groups_hit:
                gid = gh["group"]
                if any(present(cid, k) for k in gtoks.get(gid, [])):
                    covered_groups.append(gid)
            base_ratio = len(present_base) / max(1, len(keys))
            if groups_hit:
                group_ratio = len(covered_groups) / len(groups_hit)
                term_match = (base_ratio + group_ratio) / 2.0
            else:
                term_match = base_ratio
            prox, span = proximity_score(doc_norm[cid], keys_full, window)
            head = 1.0 if any(k in norm(" ".join(c["section_path"])) for k in keys_full) else 0.0
            matched = [{"term": k, "via": "original"} for k in keys if present(cid, k)]
            matched += [{"term": k, "via": via_of.get(k, "expansion")} for k in keys_full
                        if k not in keys and present(cid, k)]
            scored.append({"chunk_id": cid, "bm25": bm25, "term_match": term_match,
                           "prox": prox, "span": span, "heading": head,
                           "matched_keys": sorted(matched, key=lambda m: (m["term"], m["via"])),
                           "covered_groups": covered_groups})
        top_bm25 = max([s["bm25"] for s in scored], default=0.0) or 1.0
        for s in scored:
            s["bm25_norm"] = clamp(s["bm25"] / top_bm25)
            s["score"] = clamp(
                float(w.get("bm25", 0.45)) * s["bm25_norm"]
                + float(w.get("term_match", 0.25)) * s["term_match"]
                + float(w.get("proximity", 0.2)) * s["prox"]
                + float(w.get("heading", 0.1)) * s["heading"]
            )
        scored.sort(key=lambda x: (-x["score"], x["chunk_id"]))
        results[q["query_id"]] = scored
        traces[q["query_id"]] = {
            "query_id": q["query_id"],
            "keys": keys,
            "expanded_keys": expanded,
            "groups_hit": groups_hit,
            "keys_full": keys_full,
            "chunks_scored": len(scored),
            "top": [{"chunk_id": s["chunk_id"], "score": rounded(s["score"]),
                     "bm25": rounded(s["bm25"]), "term_match": rounded(s["term_match"]),
                     "covered_groups": s["covered_groups"]} for s in scored[:5]],
        }
    return results, traces


# --------------------------------------------------------------------------------------
# 恢复/索引操作记录（只建议，不执行）
# --------------------------------------------------------------------------------------
def build_ops_log(lib: dict, gaps: list, integrity: dict) -> list:
    ops = []
    for g in lib["alias_groups"]:
        ops.append({"op": "merge_duplicate_candidates",
                    "targets": g["members"],
                    "basis": f"SHA-256 完全一致（{g['sha256'][:16]}…）：内容相同、仅文件名不同（别名/完全重复）",
                    "action": f"建议保留规范名 {g['canonical']}，其余移入重复归档目录；本流程未执行任何改名/删除"})
    for vg in lib["version_groups"]:
        ops.append({"op": "consolidate_versions",
                    "targets": [m["path"] for m in vg["members"]],
                    "basis": f"管理性版本说明/元数据归组 {vg['doc_key']}；canonical={vg['canonical']['version']}（{vg['canonical']['path']}）",
                    "action": "建议旧版只读归档、检索口径以 canonical 为准；未执行"})
    for gap in gaps:
        ops.append({"op": "repair_file_gap",
                    "targets": [gap["path"]],
                    "basis": gap["reason"],
                    "action": f"建议人工修复（代码 {gap['code']}：加密件需口令、损坏件需换源、无文本层扫描件需补 OCR、权限件需授权）；本流程诚实跳过，不伪造文本"})
    ops.append({"op": "rebuild_index",
                "targets": ["canonical_files"],
                "basis": "文件清单+哈希清单已重建（见 manifest.files），别名归一/版本组已归档",
                "action": "建议检索索引以 canonical 文件为键重建；未执行"})
    ops.append({"op": "verify_input_integrity",
                "targets": ["input_library"],
                "basis": f"流程前后逐文件 SHA-256 对照：rechecked={integrity['rechecked']}，hashes_unchanged={integrity['hashes_unchanged']}",
                "action": "输入库只读核验已通过；若 changed 非空须立即人工介入"})
    for op in ops:
        op["input_readonly"] = True
    return ops


def write_ops_log(out_dir: Path, manifest: dict) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ops_log.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"path": path.name, "entries": len(manifest.get("ops_log", [])),
            "note": "恢复/索引建议清单写在输出目录，绝不写回输入库"}


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def parse_queries(case: dict, task_text: str, rules: dict) -> list:
    raw = first_present(case, "queries", "questions", "asks", "search_requests", "retrieval_questions", default=[])
    stop = set(norm(x) for x in rules["retrieval"].get("stopwords", []))
    limit = int(rules["retrieval"].get("scoring", {}).get("max_query_terms", 12))
    lexicon = rules["retrieval"].get("query_expansion", {})
    out = []
    for i, item in enumerate(as_list(raw), start=1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = str(first_present(item, "text", "question", "query", "ask", "content", default="")).strip()
        if not text:
            continue
        keys = [norm(str(k)) for k in as_list(first_present(item, "keywords", "terms", default=[]))
                if norm(str(k))] or query_keys(text, stop, limit)
        keys = [k for k in keys if k not in stop]
        expanded, groups_hit = expand_query(text, keys, lexicon)
        conds = first_present(item, "filters", "constraints", "conditions", default={}) or {}
        if not isinstance(conds, dict):
            conds = {}
        out.append(
            {
                "query_id": str(first_present(item, "query_id", "id", default="") or f"Q{i}"),
                "text": text,
                "keys": keys,
                "expanded": expanded,
                "groups_hit": groups_hit,
                "filters": conds,
                "max_hits": int(first_present(item, "max_hits", "top_k", "limit", default=0) or 0),
            }
        )
    if not out and task_text.strip():
        text = "\n".join(l for l in task_text.splitlines() if l.strip() and not l.startswith("#"))[:400]
        if text.strip():
            keys = query_keys(text, stop, limit)
            expanded, groups_hit = expand_query(text, keys, lexicon)
            out = [{"query_id": "Q1", "text": text, "keys": keys, "expanded": expanded,
                    "groups_hit": groups_hit, "filters": {}, "max_hits": 0}]
    return out


def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res._contract_orig.keys()))

    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).resolve().parent
    lib = build_library(case, case_dir, work)
    for rec in lib["recs"]:
        acq = acquire_text(rec, rules["retrieval"])
        rec["_acq"] = acq
        rec["pages"] = acq["pages"]
    hash_before = hash_snapshot(lib)
    gaps_files = [{"file_id": r["file_id"], "path": r["path"], "code": r["_acq"]["gap_code"],
                   "reason": r["_acq"]["gap"]} for r in lib["recs"] if r["_acq"]["gap_code"]]
    queries = parse_queries(case, task_text, rules)
    res.log("library_intake", mode=lib["mode"], files=len(lib["recs"]), gaps=len(gaps_files),
            alias_groups=len(lib["alias_groups"]), version_groups=len(lib["version_groups"]))
    res.log("intake", files=len(lib["recs"]), unreadable=len(gaps_files), queries=len(queries))

    files = lib["recs"]
    if not files:
        res.set("matches", [])
        res.evidence["index"] = {"files": 0, "chunks": 0, "note": "case 未提供文件库（files/documents/materials 均为空且无库目录）"}
        res.status["overall"] = "empty"
        res.unmet("文件库", "case.json 未给出任何文件、库目录或可读文本，未生成任何命中")
        res.unsure("检索覆盖", "无文件库时无法估计召回")
        res.note("空结果≠库内无该内容：请先提供库目录或文件清单与旁路文本（pdf-text-tool / ocr-tool 产出）")
        res.done("盘点")
        return res

    chunks, file_index, canon_by_file = build_index(files, rules)
    fdict = rules["filters"]
    res.log("index", chunks=len(chunks), pages=sum(f["pages"] for f in file_index),
            dup_aliases=sum(1 for r in files if r.get("alias_of")))

    dropped = {g["file_id"]: dict(g) for g in gaps_files}
    for r in files:
        if r.get("alias_of"):
            dropped[r["file_id"]] = {
                "file_id": r["file_id"],
                "path": r["path"],
                "code": "R_DUP_FILE",
                "reason": fdict.get("codes", {}).get("R_DUP_FILE", ""),
                "duplicate_of": r["alias_of"],
            }
    usable = [c for c in chunks if c["file_id"] not in dropped]
    file_meta = {f["file_id"]: f for f in files}
    hashes = {f["file_id"]: (f.get("file_sha256") or (f.get("_acq") or {}).get("text_sha256") or "")
              for f in files}

    if not usable:
        res.set("matches", [])
        res.evidence["excluded"] = [dropped[k] for k in sorted(dropped)]
        res.status["overall"] = "empty"
        res.unmet("命中清单", "全部文件均不可检索（无文本层/纯副本/无权限），已逐条给出理由码")
        res.done("盘点", "切页分块", "去重")
        return res

    results, traces = retrieve(usable, queries, rules)
    th = rules["retrieval"].get("thresholds", {})
    min_score = float(th.get("min_score", 0.12))
    min_term = float(th.get("min_term_match", 0.2))
    default_hits = int(th.get("max_hits_per_query", 5))
    quote_chars = int(th.get("quote_chars", 160))

    vg_by_path = {}
    for vg in lib["version_groups"]:
        for m in vg["members"]:
            vg_by_path[m["path"]] = vg
    alias_members_of = {}
    for r in files:
        if r.get("alias_of"):
            alias_members_of.setdefault(r["alias_of"], []).append(r["path"])

    chunk_by_id = {c["chunk_id"]: c for c in usable}
    matches, query_views, gaps = [], [], []
    for q in queries:
        conds = q["filters"]
        filt_rows, excluded = {}, []
        for fid in sorted({c["file_id"] for c in usable}):
            rows = evaluate_filters(file_meta[fid], conds, fdict) if conds else []
            ok = all(r["satisfied"] for r in rows)
            filt_rows[fid] = {"satisfied": ok, "filters": rows}
            if not ok:
                bad = [r for r in rows if not r["satisfied"]]
                excluded.append({"file_id": fid, "path": file_meta[fid]["path"],
                                 "codes": [r["code"] for r in bad], "reasons": [r["reason"] for r in bad]})
        hits, blocked = [], []
        for s in results.get(q["query_id"], []):
            c = chunk_by_id[s["chunk_id"]]
            fid = c["file_id"]
            if not filt_rows.get(fid, {}).get("satisfied", True):
                if len(blocked) < 50:
                    blocked.append({"chunk_id": c["chunk_id"], "file_id": fid,
                                    "path": file_meta[fid]["path"], "score": rounded(s["score"]),
                                    "reason": "文件未通过硬过滤，命中被拦下（相关但不在提问范围内）"})
                continue
            if s["score"] < min_score or s["term_match"] < min_term:
                continue
            hits.append((s, c))
        limit = q["max_hits"] or default_hits
        matched = []
        dup_notices = []
        for rank, (s, c) in enumerate(hits[:limit], start=1):
            quote = snippet(c["text"], 0, min(len(c["text"]), quote_chars))
            core = quote.strip("…").strip()
            rec = file_meta[c["file_id"]]
            hit = {
                "rank": rank,
                "query_id": q["query_id"],
                "chunk_id": c["chunk_id"],
                "score": rounded(s["score"]),
                "score_breakdown": {
                    "bm25": rounded(s["bm25"]),
                    "bm25_norm": rounded(s["bm25_norm"]),
                    "term_match": rounded(s["term_match"]),
                    "proximity": rounded(s["prox"]),
                    "proximity_span": s["span"],
                    "heading_hit": rounded(s["heading"]),
                    "weights": {k: float(v) for k, v in rules["retrieval"]["scoring"]["weights"].items()},
                },
                "matched_keys": s["matched_keys"],
                "covered_groups": s["covered_groups"],
                "file": {
                    "file_id": c["file_id"],
                    "path": rec["path"],
                    "sha256": hashes.get(c["file_id"], ""),
                    "file_type": rec.get("file_type", ""),
                    "extraction_method": (rec.get("_acq") or {}).get("extraction_method") or None,
                    "project_id": rec["metadata"].get("project_id"),
                    "year": rec["metadata"].get("year"),
                    "version": rec.get("version") or rec["metadata"].get("version"),
                },
                "location": {
                    "page": c["page"],
                    "page_label": c["page_label"],
                    "label_source": c["label_source"],
                    "section_path": c["section_path"],
                    "line_from": c["line_from"],
                    "line_to": c["line_to"],
                    "char_range": [c["char_start"], c["char_end"]],
                    "two_column_hint": c["two_column"],
                    "read_order": ("left column top→bottom, then right column（按 x 间距聚类，启发式）"
                                   if c["two_column"] else None),
                },
                "paragraph_first_sentence": c["first_sentence"],
                "quote": quote,
                "quote_check": {"exact_match": bool(core and core in canon_by_file.get(c["file_id"], "")),
                                "checked_in": "canonical_text",
                                "char_offset": c["char_start"]},
                "filters_satisfied": True,
            }
            # 版本串味提示：命中落在非 canonical 版本/副本上时标明，防止旧版冒充独立证据
            vg = vg_by_path.get(rec["path"])
            if vg and rec["path"] != vg["canonical"]["path"]:
                hit["version_hint"] = {
                    "kind": "stale_version",
                    "anchored_version": rec.get("version") or rec["metadata"].get("version"),
                    "canonical_path": vg["canonical"]["path"],
                    "canonical_version": vg["canonical"]["version"],
                    "note": (f"命中落在非 canonical 版本 {rec.get('version') or rec['metadata'].get('version')} 上："
                             f"版本口径必须保留，canonical 为 {vg['canonical']['version']}（{vg['canonical']['path']}），"
                             f"不得把旧版内容当作最新版证据"),
                }
            for dup in alias_members_of.get(rec["path"], []):
                notice = {"path": dup, "kind": "duplicate_copy",
                          "note": f"{dup} 与命中文件 {rec['path']} SHA-256 相同（别名/副本）："
                                  f"已合并为单一证据源，不作独立命中计数"}
                if notice not in dup_notices:
                    dup_notices.append(notice)
            matched.append(hit)
        matches.append({"query_id": q["query_id"], "text": q["text"], "hits": matched, "hit_count": len(matched)})
        query_views.append(
            {
                "query_id": q["query_id"],
                "text": q["text"],
                "keys": q["keys"],
                "expanded_keys": q["expanded"],
                "groups_hit": q["groups_hit"],
                "filters": conds,
                "files_considered": len(filt_rows),
                "files_passing_filters": sum(1 for v in filt_rows.values() if v["satisfied"]),
                "hits": len(matched),
                "excluded_by_filters": excluded,
                "blocked_by_filters_top": blocked[:20],
                "filtered_but_relevant": blocked[:20],
                "duplicate_copies_of_hits": dup_notices,
                "top_scores": traces[q["query_id"]]["top"],
            }
        )
        if not matched:
            passing = sum(1 for v in filt_rows.values() if v["satisfied"])
            if blocked:
                code = "R_FILTER_BLOCKED_HITS"
            elif passing == 0:
                code = "R_FILTERS_EXCLUDE_ALL"
            else:
                code = "R_NO_HIT_IN_SCOPE"
            gaps.append({"query_id": q["query_id"], "code": code,
                         "reason": fdict.get("codes", {}).get(code, ""),
                         "next_action": "核对过滤条件是否过严或缺元数据；不得为凑结果放宽硬约束",
                         "files_passing_filters": passing,
                         "blocked_hit_count": len(blocked),
                         "coverage_note": ("注意：本库另有 "
                                           + f"{len(gaps_files)} 个文件因权限/加密/损坏/无文本层未进入语料："
                                           + "空结果不能断定库内无支撑，只能说明可检索语料内无支撑"
                                           if gaps_files else
                                           "全部入库文件均有文本层：本问空结果可判定为可检索语料内无支撑"),
                         "filters_unsatisfied_files": [e["file_id"] for e in excluded]})

    integrity = verify_untouched(hash_before, lib)
    ops = build_ops_log(lib, gaps_files, integrity)

    if not matches or all(m["hit_count"] == 0 for m in matches):
        res.status["overall"] = "empty"
        res.unmet("命中清单", "所有提问在硬过滤后的语料内均无命中，理由码见 gaps")
    elif gaps:
        res.status["overall"] = "partial"
        for g in gaps:
            res.unmet(f"提问命中:{g['query_id']}", f"{g['code']} — {g['reason']}")
    else:
        res.status["overall"] = "complete"

    if gaps_files:
        by_code = {}
        for g in gaps_files:
            by_code[g["code"]] = by_code.get(g["code"], 0) + 1
        res.unmet("文件库提取", "；".join(f"{v} 份 {k}" for k, v in sorted(by_code.items()))
                  + "——诚实跳过，不伪造文本层（详见 evidence.unreadable）")
    if not integrity["hashes_unchanged"]:
        res.unmet("输入完整性", f"输入文件哈希发生变化：{integrity['changed'][:5]}")
    if any("absent" in fi["label_sources"] for fi in file_index if fi["pages"]):
        res.unsure("印刷页码", "部分文件无显式印刷页标签，page_label 留空并标 label_source=absent，未用物理页冒充印刷页")
    stale_hits = sum(1 for m in matches for h in m["hits"] if "version_hint" in h)
    if stale_hits:
        res.unsure("版本口径", f"{stale_hits} 条命中落在非 canonical 版本上，已逐条给 stale_version 提示（不冒充最新版）")
    if lib["alias_groups"]:
        res.note(f"{len(lib['alias_groups'])} 组同哈希文件已合并为单一证据源（alias_of，不重复计命中）")
    if lib["version_groups"]:
        res.note(f"{len(lib['version_groups'])} 个版本组存在多版本文件，canonical 与版本口径写入 evidence.dedupe.version_groups")
    if lib["root_warn"]:
        res.unmet("文件库目录", lib["root_warn"])

    payload = {
        "matches": matches,
        "query_views": query_views,
        "gaps": gaps,
        "excluded_files": [dropped[k] for k in sorted(dropped)],
        "library_report": {
            "mode": lib["mode"],
            "root": (lib["root"].name if lib["root"] is not None else None),
            "root_resolution": lib["root_tried"] or ["inline（无库目录，使用 case 内联材料）"],
            "files_scanned": sum(1 for r in lib["recs"] if r["source"] == "scan"),
            "inline_entries": sum(1 for r in lib["recs"] if r["source"] == "inline"),
            "files": [
                {"file_id": r["file_id"], "path": r["path"], "file_type": r.get("file_type"),
                 "size": r.get("size"), "readable": r.get("readable"),
                 "file_sha256": r.get("file_sha256"),
                 "extraction_method": (r.get("_acq") or {}).get("extraction_method") or None,
                 "text_sha256": (r.get("_acq") or {}).get("text_sha256") or None,
                 "n_chars": (r.get("_acq") or {}).get("n_chars") or 0,
                 "pages": len(r.get("pages") or []),
                 "ocr_confidence": (r.get("_acq") or {}).get("ocr_confidence"),
                 "alias_of": r.get("alias_of"), "version": r.get("version") or None,
                 "version_basis": r.get("version_basis") or None,
                 "doc_key": r.get("doc_key") or None,
                 "status": ("gap:" + (r.get("_acq") or {}).get("gap_code", ""))
                            if (r.get("_acq") or {}).get("gap_code") else "ok"}
                for r in sorted(lib["recs"], key=lambda r: r["path"])
            ],
            "alias_groups": lib["alias_groups"],
            "version_groups": lib["version_groups"],
            "gaps": gaps_files,
            "version_notes_fields": lib["version_notes_source"],
            "integrity": integrity,
        },
        "ops_log": ops,
    }
    res.set("query_plan", {"queries": [{"query_id": q["query_id"], "text": q["text"],
                                        "keys": q["keys"], "expanded_keys": q["expanded"],
                                        "filters": q["filters"]} for q in queries],
                           "stop_rule": "语料遍历完即停；命中不足不放宽硬过滤", "skill": SKILL})
    res.set("gaps", gaps)
    res.set("excluded_files", payload["excluded_files"])
    for key, value in payload.items():
        res.set(key, value)

    res.evidence["index"] = {
        "build_params": rules["retrieval"].get("chunking", {}),
        "heading_patterns": [p["id"] for p in rules["retrieval"].get("heading_patterns", [])],
        "files": file_index,
        "chunks_total": len(chunks),
        "chunks_retrievable": len(usable),
        "chunk_table": [
            {"chunk_id": c["chunk_id"], "file_id": c["file_id"], "page": c["page"],
             "page_label": c["page_label"], "section_path": c["section_path"],
             "line_from": c["line_from"], "line_to": c["line_to"], "two_column": c["two_column"],
             "char_range": [c["char_start"], c["char_end"]], "chars": len(c["text"])}
            for c in usable[:400]
        ],
    }
    res.evidence["retrieval"] = {
        "queries": [traces[q["query_id"]] for q in queries if q["query_id"] in traces],
        "scoring": rules["retrieval"].get("scoring", {}),
        "thresholds": th,
        "query_expansion": rules["retrieval"].get("query_expansion", {}),
        "recompute": "score = w_bm25·bm25_norm + w_match·term_match + w_prox·proximity + w_head·heading；"
                     "bm25_norm = bm25 / max(bm25)；term_match = 均值(原始词命中率, 扩展组覆盖率)，"
                     "无扩展组时=原始词命中率（组内任一变体命中即覆盖，跨语言对齐不被表面词元稀释）",
    }
    res.evidence["filters"] = {
        "dictionary": fdict.get("filters", {}),
        "codes": fdict.get("codes", {}),
        "per_query": [{"query_id": v["query_id"], "excluded": v["excluded_by_filters"],
                       "passing": v["files_passing_filters"], "considered": v["files_considered"]} for v in query_views],
        "policy": fdict.get("evaluation_policy", ""),
    }
    res.evidence["dedupe"] = {
        "hash_alias_groups": lib["alias_groups"],
        "aliases": {r["file_id"]: r["alias_of"] for r in lib["recs"] if r.get("alias_of")},
        "version_groups": lib["version_groups"],
        "policy": "同哈希异名归并为单一证据源（alias_of）；版本组 canonical=版本序最大者；"
                  "副本/旧版不作独立命中，命中非 canonical 版本时给 stale_version 提示",
    }
    res.evidence["unreadable"] = [dict(u) for u in sorted(gaps_files, key=lambda x: x["file_id"])]
    res.evidence["library"] = {
        "mode": lib["mode"],
        "root_resolution": payload["library_report"]["root_resolution"],
        "integrity": integrity,
        "hash_policy": "逐文件 SHA-256（字节级）；扫描时与流程结束时各取一次，全部一致才记 hashes_unchanged=true",
        "two_column_policy": "PDF 页按 x 间距聚类列（启发式），two_column=true 时命中附阅读顺序提示；"
                             "跨栏段落不强行拼接，如实标注",
        "gap_policy": "加密/损坏/无文本层/无提取器/无权限分别记码，不静默跳过、不伪造文本、不计为已检索",
    }
    res.evidence["canonical_text"] = ("每个文件按 \\f 与 [[page:N|label]] 切页后以换行拼接；line_from/line_to 为页内 1 基线号，"
                                      "char_range 为该拼接文本中的字符区间，quote_check.exact_match 用该文本逐字复核")
    res.evidence["ops_log"] = {"entries": ops, "executed": False,
                               "note": "全部为建议；输入库前后哈希对照见 evidence.library.integrity"}

    # 工单产物：文件清单+哈希清单+恢复/索引建议（只写输出目录）
    if not getattr(args, "dry_run", False):
        manifest = {
            "case_id": res.case_id,
            "producer": SKILL,
            "policy": "恢复与索引建议清单：输入库只读，本清单不改写源文件；所有哈希为 SHA-256",
            "library_mode": lib["mode"],
            "files": payload["library_report"]["files"],
            "alias_groups": lib["alias_groups"],
            "version_groups": lib["version_groups"],
            "gaps": gaps_files,
            "integrity": integrity,
            "ops_log": ops,
        }
        res.evidence["ops_log_file"] = write_ops_log(Path(args.out), manifest)
        res.log("ops_log_written", entries=len(ops))

    res.bind(payload, aliases={"命中": "matches", "检索结果": "matches", "主结果": "matches",
                               "文件库": "library_report", "盘点": "library_report",
                               "操作": "ops_log", "恢复": "ops_log",
                               "证据记录": "evidence", "状态说明": "status"})
    bind_contract(res, payload, rules["projection"])

    res.done("文件库盘点", "切页分块", "去重与版本组", "硬过滤执行", "四因子检索（含词表扩展）", "回链锚点自检",
             "恢复/索引建议与只读核验")
    return res


def bind_contract(res: Result, payload: dict, projection: dict):
    """契约字段兜底：按 rules/contract_projection.json 关键词投影，仍无对应则显式声明缺口。"""
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
        "matches": [],
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
                          "queries": len(payload.get("matches", []))}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "hits": sum(m["hit_count"] for m in payload.get("matches", [])),
                      "library_mode": payload.get("library_report", {}).get("mode"),
                      "ops_log": payload.get("evidence", {}).get("ops_log_file", {}).get("path")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
