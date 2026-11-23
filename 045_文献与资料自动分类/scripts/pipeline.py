#!/usr/bin/env python3
"""045 文献与资料自动分类 —— 主流程（纯标准库，离线、确定性、可回滚）。

输入：case.json + task.md。两种材料形态皆支持：
  A. 文件库模式（真实文件库工单）：case 给出 library_path / materials_root 等库目录
     （字符串路径）或带路径的文件清单 → 递归扫描目录 → 逐文件 SHA-256+大小+类型+权限
     → 同哈希异名识别别名/完全重复 → 同归一化标题提示近重复 → 按管理性版本说明
     （version_notes/admin_notes/versions 或 metadata.version / 文件名版本词）分版本组。
  B. 内联模式：files/documents 直接给标题/摘要/正文（兼容旧行为）。

文本获取链（逐文件记录 extraction_method + 文本哈希 + 字符数，全部失败则诚实 gap）：
  内联正文 → 侧车 .txt/.ocr.txt → pdf_min 提取 PDF 文本层 → zip+xml 读 DOCX 段落
  → 纯文本直读 → gap（加密 R_FILE_ENCRYPTED / 损坏 R_FILE_CORRUPT / 无文本层
  R_NO_TEXT_LAYER / 无提取器 R_FILE_NO_EXTRACTOR / 权限不足 R_FILE_NO_PERMISSION）。

七步闭环：
  1. 盘点：doc_id、路径、逐文件字节级 SHA-256/大小/权限、标题（出处留痕）/正文/元数据
     （case 条目 + 伴随元数据 CSV + 管理性版本说明三处合并，缺失如实标注不补造）
  2. 去重：字节 SHA-256 强判别名/完全重复；正文归一指纹判同文不同名副本
  3. 版本：DOI 或「标题强归一+第一作者」聚内容版本族；管理性版本说明+文件名版本词
     聚文件版本组（canonical=正式版角色优先、版本序最大，全部保留不删除，归档只写建议）
  4. 分类：taxonomy.json 规则优先（元数据命中 + 字段加权词命中 − negative）→ 饱和置信度；
     预印本/正式版/补充材料/修订版角色区分，多标签与边界模糊给出逐条命中依据
  5. 标签：tag_vocabulary.json 同义归并（merged_from 留痕）+ 粒度上限，抑制标签爆炸
  6. 命名：模板渲染 + 非法字符/超长/多语言/缺字段降级（null_token 占位，严禁补造）；
     目标名冲突 → 版本感知消歧（绝不覆盖），乱码文件名 → 人工确认队列
  7. 操作：全部作为建议落 ops（移动/改名/合并/归档 + 依据 + 幂等键 + 回滚路径），
     文件库模式绝不改写输入库（前后哈希对照留证，可续跑：未处理项清单单列）；
     仅内联模式下显式 --apply 才真正移动工作区文件（目标冲突不覆盖）。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import unescape as _xml_unescape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kit import (  # noqa: E402
    Clock,
    Result,
    as_list,
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
)
import pdf_min  # noqa: E402

SKILL = "literature-auto-classifier"
RULES = Path(__file__).resolve().parent.parent / "rules"
ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]+')
TEXT_EXTS = {"md", "txt", "markdown", "json", "csv", "tex", "log", "html", "htm"}
DOC_EXTS = {"docx"}
LIBRARY_KEYS = ("library_path", "materials_root", "library_root", "library_dir", "library")
VERSION_NOTE_KEYS = ("version_notes", "admin_notes", "versions")
METADATA_KEYS = ("metadata_csv", "metadata_table", "metadata_rows", "accompanying_metadata")
AUX_DOC_KEYS = ("case.json", "task.md", "TASK.md")
_VERSION_TOKEN = re.compile(r"(preprint|arxiv|draft|final|rev(?:ised|ision)?[._ ]?(\d+)|"
                            r"v(?:er|ersion)?[._ ]?(\d+)|v(\d+)|r(\d+)|supplementary|si\b)", re.I)
_MOJIBAKE = re.compile(r"(\ufffd)|Ã.|Â.|â[\x80-\xbf]|æ[\x80-\xbf]|æ.æ")


def load_rules() -> dict:
    return {
        "taxonomy": load_json(RULES / "taxonomy.json", default={}) or {},
        "tags": load_json(RULES / "tag_vocabulary.json", default={}) or {},
        "classify": load_json(RULES / "classification_rules.json", default={}) or {},
        "projection": load_json(RULES / "contract_projection.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 文件库盘点：扫描、哈希、权限、别名/重复、版本说明、元数据表
# --------------------------------------------------------------------------------------
def file_sha256(path: Path) -> tuple:
    """流式 SHA-256；权限不足返回 (None, 'R_FILE_NO_PERMISSION')，永不抛异常。"""
    h = hashlib.sha256()
    try:
        with Path(path).open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except PermissionError:
        return None, "R_FILE_NO_PERMISSION"
    except OSError as exc:  # noqa: BLE001
        return None, "R_FILE_UNREADABLE"
    return h.hexdigest(), ""


def resolve_library_root(case: dict, case_dir: Path, work: Path) -> tuple:
    """解析库目录引用；相对路径按 case 目录 → 祖先 input/ → work → cwd → /input 回退。"""
    ref = first_present(case, *LIBRARY_KEYS)
    if not isinstance(ref, str) or not ref.strip():
        return None, [], "case 未声明文件库目录（library_path/materials_root 等字符串路径），按内联材料处理"
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


def scan_library_files(root: Path) -> tuple:
    """递归扫描库目录（跳过隐藏文件与软链），逐文件 SHA-256+大小+权限；输出确定性排序。

    侧车文件（<f>.ocr.txt / <f>.ocr.json / 二进制文件的 <f>.txt 伴生件）是提取辅助，
    不作为库文档建档；空目录单独记录。
    """
    raw, empty_dirs = [], []
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            continue
        if p.is_dir():
            if not any(p.iterdir()):
                empty_dirs.append(p.relative_to(root).as_posix())
            continue
        if not p.is_file() or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        sha, perm_code = file_sha256(p)
        try:
            size = p.stat().st_size
        except OSError:  # noqa: BLE001
            size = None
        raw.append({"path": rel, "name": p.name, "ext": p.suffix.lstrip(".").lower(),
                    "size": size, "file_sha256": sha, "permission_code": perm_code,
                    "permission_ok": not perm_code, "_abs": p})
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

    return [f for f in raw if not is_sidecar(f)], empty_dirs


def version_from_text(value: str) -> str:
    m = _VERSION_TOKEN.search(str(value or ""))
    if not m:
        return ""
    tok = m.group(0).lower().replace(" ", "")
    if tok.startswith("preprint") or tok.startswith("arxiv"):
        return "preprint"
    if tok.startswith("supp") or tok == "si":
        return "supplementary"
    if tok.startswith("draft"):
        return "draft"
    if tok.startswith("final"):
        return "final"
    return tok


def version_rank_key(version: str) -> tuple:
    """版本排序键：journal/final > v<n>/rev<n> > draft > preprint；数字大者优先。确定性。"""
    v = str(version or "").lower()
    num = ""
    m = re.search(r"(\d+)", v)
    if m:
        num = m.group(1).zfill(4)
    if "final" in v:
        order = 4
    elif re.search(r"v|rev|r\d", v):
        order = 3
    elif "draft" in v:
        order = 2
    elif "preprint" in v:
        order = 1
    else:
        order = 0
    return (order, num, v)


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
        if path_norm.endswith("/" + k) or path_norm.endswith(k):
            return notes[k]
    return None


def load_metadata_rows(case: dict, case_dir: Path, lib_root: Path | None = None) -> tuple:
    """伴随元数据表（CSV/TSV/JSON 或内联行）：按 path/name 列把作者/年份/期刊/DOI/类型
    并入库文档 metadata；取不到的行不补造。返回 (rows, source_note)。"""
    import csv as _csv

    value = first_present(case, *METADATA_KEYS)
    if value in (None, "", [], {}):
        return [], "case 未提供伴随元数据表（metadata_csv 等）"
    try:
        if isinstance(value, (list, dict)):
            rows = as_list(value)
            return [r for r in rows if isinstance(r, dict)], "inline(metadata_rows)"
        path = Path(str(value))
        if not path.is_absolute():
            bases = [case_dir, case_dir.parent, Path.cwd(), Path("/input")]
            if lib_root is not None:
                bases.insert(0, lib_root)  # 库内伴随表最常见
            for base in bases:
                if (base / path).is_file():
                    path = base / path
                    break
        if not path.is_file():
            return [], f"伴随元数据表不存在：{value}（如实记录，不补造字段）"
        if path.suffix.lower() in (".csv", ".tsv", ".txt"):
            with path.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = _csv.DictReader(fh, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
                return [dict(r) for r in reader], str(path)
        data = load_json(path, default=[])
        return as_list(data), str(path)
    except Exception as exc:  # noqa: BLE001 —— 元数据表损坏如实记录
        return [], f"伴随元数据表读取失败：{type(exc).__name__}: {exc}"


def join_metadata_row(rec: dict, rows: list) -> dict:
    """按归一化 path/name/file 匹配元数据表行 → metadata 字典（缺失键不出现）。"""
    if not rows:
        return {}
    targets = {norm(rec["path"]), norm(rec["name"])}
    row = None
    for r in rows:
        key = norm(str(first_present(r, "path", "file", "filename", "name", default="") or ""))
        if key and key in targets:
            row = r
            break
        if not row and key and (norm(rec["path"]).endswith("/" + key) or norm(rec["path"]).endswith(key)):
            row = r
    if row is None:
        return {}
    meta = {}
    mapping = {"authors": ("authors", "author"), "year": ("year",), "venue": ("venue", "journal"),
               "doi": ("doi",), "type": ("type", "doc_type"), "title": ("title",),
               "version": ("version",), "project_id": ("project_id",)}
    for key, cands in mapping.items():
        val = first_present(row, *cands)
        if val in (None, ""):
            continue
        if key == "authors" and isinstance(val, str):
            val = [a.strip() for a in re.split(r"[;|]", val) if a.strip()]
        meta[key] = val
    return meta


def build_alias_groups(recs: list) -> list:
    """同字节 SHA-256 异名 → 别名/完全重复组；canonical 优先非 copy/backup/latest(1) 命名。"""
    by_hash = {}
    for rec in recs:
        if rec.get("file_sha256"):
            by_hash.setdefault(rec["file_sha256"], []).append(rec)
    groups = []
    bait = re.compile(r"(copy|backup|\(1\)|latest)", re.I)
    for h in sorted(by_hash):
        members = by_hash[h]
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda r: (bool(bait.search(r["name"])), r["path"]))
        canonical = ordered[0]
        for r in ordered[1:]:
            r["alias_of"] = canonical["path"]
        groups.append({"sha256": h, "kind": "alias_or_duplicate",
                       "canonical": canonical["path"],
                       "members": [r["path"] for r in ordered],
                       "size": canonical.get("size"),
                       "note": "同 SHA-256 不同文件名：完全重复/别名（canonical 优先非 copy/backup/(1) 命名；"
                               "仅合并建议，未执行任何改名/删除）"})
    return groups


def doc_key_of(rec: dict, explicit: str = "") -> str:
    """文档同一性键：管理性说明显式 doc > 元数据 doc/title > 文件名去版本词后的强归一。"""
    if explicit:
        return norm_title(explicit)
    meta = rec.get("metadata") or {}
    for key in ("doc_id", "document_id", "doc"):
        if norm_title(str(meta.get(key) or "")):
            return norm_title(str(meta[key]))
    title = rec.get("title") or meta.get("title") or rec["name"].rsplit(".", 1)[0]
    stripped = _VERSION_TOKEN.sub(" ", str(title))
    return norm_title(stripped) or norm_title(str(title))


def build_version_groups(recs: list, notes_by_path: dict) -> list:
    """文件级版本组：同一 doc_key 且带版本信息者归组；canonical=版本序最大者
    （并列时正式版角色优先、非别名副本优先）。全部保留不删除，归档只写建议。"""
    role_rank = {"journal": 0, "conference": 1, "thesis": 1, "dataset": 1, "patent": 1,
                 "preprint": 2, "supplementary": 3, "other": 4, "duplicate": 5}
    groups = {}
    for rec in recs:
        note = match_note_for(rec["path_norm"], notes_by_path)
        explicit_doc = str((note or {}).get("doc", "") or "")
        meta = rec.get("metadata") or {}
        version = rec.get("version") or str(meta.get("version", "") or "") \
            or ((note or {}).get("version", "") if note else "")
        basis = "version_note" if (note and (note.get("version") or "").strip()) else (
            "metadata" if str(meta.get("version", "") or "").strip()
            else ("filename_pattern" if version else ""))
        if not version:
            rec["version"], rec["version_basis"], rec["doc_key"] = "", "", doc_key_of(rec, explicit_doc)
            continue
        rec["version"], rec["version_basis"] = version, basis
        rec["doc_key"] = doc_key_of(rec, explicit_doc)
        if basis == "filename_pattern":
            rec["version_warnings"] = "版本取自文件名模式（启发式），管理性版本说明缺失"
        groups.setdefault(rec["doc_key"], []).append(rec)
    out = []
    for key in sorted(groups):
        members = groups[key]
        if len(members) < 2:
            continue
        def rank_of(r):
            # 文件级分组不按内容角色抢 canonical：版本序优先，并列时非别名副本、路径稳定
            # 升序取 [-1] 即 canonical：非别名（True）排在别名（False）之后
            return (version_rank_key(r["version"]),
                    not bool(r.get("alias_of")), norm(r.get("path", "")))
        canonical = sorted(members, key=rank_of)[-1]
        ranked = sorted(members, key=lambda r: r["path"])
        out.append({
            "doc_key": key,
            "canonical": {"path": canonical["path"], "version": canonical["version"]},
            "members": [{"path": r["path"], "version": r["version"], "basis": r["version_basis"],
                         "note": (match_note_for(r["path_norm"], notes_by_path) or {}).get("note", "")}
                        for r in ranked],
            "policy": "canonical=版本序最大者（final>v(n)>draft>preprint，并列时非别名副本优先）；"
                      "全部保留不删除，旧版只读归档建议写 ops_log",
        })
    return out


# --------------------------------------------------------------------------------------
# 文本获取链（侧车 → PDF → DOCX → 纯文本 → 诚实 gap）
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


def read_sidecar_text(abs_path: Path) -> tuple:
    """侧车文本：<f>.txt / <stem>.txt / <f>.ocr.txt / <stem>.ocr.txt（不含自身）。"""
    cands = [Path(str(abs_path) + ".txt"), abs_path.with_suffix(".txt"),
             Path(str(abs_path) + ".ocr.txt"), abs_path.with_name(abs_path.stem + ".ocr.txt")]
    seen = []
    for cand in cands:
        if cand in seen or (cand.exists() and cand.resolve() == abs_path.resolve()):
            continue
        seen.append(cand)
        txt = read_text(cand)
        if txt.strip():
            return txt, str(cand.name)
    return "", ""


def acquire_text(rec: dict, cfg: dict) -> dict:
    """按 内联 → 侧车 → PDF → DOCX → 纯文本 → gap 链取正文；记录提取方式/字符数/文本哈希。"""
    info = {"extraction_method": "", "n_chars": 0, "text_sha256": "",
            "gap": None, "gap_code": "", "warnings": []}

    def finish(text: str, method: str) -> dict:
        info.update({"extraction_method": method, "n_chars": len(text),
                     "text_sha256": sha256_text(text)})
        return info

    inline = str(rec.get("inline_text") or "")
    if inline.strip():
        return finish(inline, "inline_case_json")

    local = rec.get("_abs")
    if local is None or not Path(local).is_file():
        info["gap"], info["gap_code"] = "内联无正文且文件路径不可读（或不存在），文本获取失败", "R_NO_TEXT"
        return info
    if not rec.get("permission_ok", True):
        info["gap"], info["gap_code"] = "权限不足：文件存在但当前进程不可读", "R_FILE_NO_PERMISSION"
        return info

    sc_txt, sc_name = read_sidecar_text(Path(local))
    if sc_txt.strip():
        method = "sidecar_ocr_text" if ".ocr." in sc_name else "sidecar_text"
        return finish(sc_txt, method)

    ext = rec.get("ext", "")
    if ext == "pdf":
        got = pdf_min.read_pdf(Path(local))
        if got["encrypted"]:
            info["gap"], info["gap_code"] = got["errors"][0], "R_FILE_ENCRYPTED"
            return info
        if got["errors"] and not got["ok"]:
            corrupt = bool(got.get("streams_failed")) or not got.get("streams_total")
            info["gap"], info["gap_code"] = "；".join(got["errors"]), \
                "R_FILE_CORRUPT" if corrupt else "R_NO_TEXT_LAYER"
            return info
        text = "\n".join(pg["text"] for pg in got["pages"])
        if not text.strip():
            info["gap"], info["gap_code"] = "PDF 内容流均无文本操作（无文本层扫描件）", "R_NO_TEXT_LAYER"
            return info
        info["warnings"].extend(got["errors"] + got["warnings"])
        return finish(text, "pdf_textlayer")
    if ext in DOC_EXTS:
        paras = read_docx_paragraphs(Path(local))
        if not paras:
            info["gap"], info["gap_code"] = "DOCX 无法解析出段落（可能损坏或为空文档）", "R_FILE_CORRUPT"
            return info
        return finish("\n".join(paras), "docx_xml")
    if ext in TEXT_EXTS:
        txt = read_text(Path(local))
        if txt.strip():
            return finish(txt, "plain_text")
        info["gap"], info["gap_code"] = f"纯文本文件为空（0 字符）", "R_NO_TEXT"
        return info
    info["gap"], info["gap_code"] = \
        f"无可用提取器（扩展名 .{ext or '未知'} 不在侧车/PDF/DOCX/纯文本链内）", "R_FILE_NO_EXTRACTOR"
    return info


# --------------------------------------------------------------------------------------
# 盘点：扫描记录 + 内联条目 → 统一 doc 记录
# --------------------------------------------------------------------------------------
def build_library(case: dict, case_dir: Path, work: Path, cfg: dict) -> dict:
    root, tried, root_warn = resolve_library_root(case, case_dir, work)
    notes = parse_version_notes(case)
    meta_rows, meta_note = load_metadata_rows(case, case_dir, root)
    recs, gaps, mode = [], [], "inline" if root is None else "library"
    scan_recs, empty_dirs, aux_recs = [], [], []
    aux_paths = set()
    if root is not None and meta_rows and meta_note and not meta_note.startswith("inline"):
        mp = Path(meta_note)
        try:
            if mp.is_file():
                aux_paths.add(mp.resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            pass  # 元数据表在库外：无需从文档清单剔除

    def push(rec):
        rec["path_norm"] = norm(rec["path"])
        recs.append(rec)

    if root is not None:
        scanned, empty_dirs = scan_library_files(root)
        for f in scanned:
            if f["path"] in aux_paths:
                # 伴随元数据表是建档辅助，不是文献文档：登记哈希但不分类
                aux_recs.append({"file_id": "aux-" + stable_id("aux", f["path"], n=8),
                                 "path": f["path"], "name": f["name"], "ext": f["ext"],
                                 "size": f["size"], "file_sha256": f["file_sha256"],
                                 "permission_ok": f["permission_ok"], "_abs": f["_abs"],
                                 "path_norm": norm(f["path"])})
                continue
            note = match_note_for(norm(f["path"]), notes) or {}
            meta = join_metadata_row(f, meta_rows)
            if meta_note and meta:
                meta["_metadata_source"] = meta_note
            if note.get("note"):
                meta.setdefault("admin_note", note["note"])
            stem_version = version_from_text(f["name"].rsplit(".", 1)[0])
            rec = {"file_id": "lib-" + stable_id("lib", f["path"], n=8), "source": "scan",
                   "path": f["path"], "name": f["name"], "ext": f["ext"], "size": f["size"],
                   "file_sha256": f["file_sha256"], "permission_ok": f["permission_ok"],
                   "permission_code": f["permission_code"], "_abs": f["_abs"],
                   "title": str(note.get("doc", "") or meta.get("title", "") or ""),
                   "metadata": meta, "version": (note.get("version", "") or stem_version),
                   "alias_of": None, "doc_key": "", "version_basis": ""}
            if not f["permission_ok"]:
                rec["_pre_gap"] = f["permission_code"]
            push(rec)
            scan_recs.append(rec)

    inline_items = as_list(first_present(case, "files", "documents", "items", "batch", default=[]))
    for idx, item in enumerate(inline_items, start=1):
        if isinstance(item, str):
            item = {"path": item}
        if not isinstance(item, dict):
            continue
        path_str = str(first_present(item, "path", "file", "filepath", "name", default="") or "").strip()
        name = str(first_present(item, "name", "filename", default="") or Path(path_str).name)
        title = str(first_present(item, "title", default="") or "").strip()
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        for key in ("authors", "author", "year", "venue", "journal", "doi", "type", "language",
                    "project_id", "permission", "scanned", "version"):
            if key in item and key not in meta:
                meta[key] = item[key]
        local = Path(path_str) if path_str else None
        if local is not None and not local.is_absolute():
            for base in (case_dir, case_dir.parent, work or Path("."), Path("/input")):
                if (base / local).is_file():
                    local = base / local
                    break
        ext = (Path(name or path_str).suffix.lstrip(".").lower() or "")
        inline_text = first_present(item, "text", "content", "body", "fulltext", default="")
        if isinstance(inline_text, list):
            inline_text = "\n".join(str(x) for x in inline_text)
        inline_text = str(inline_text or "")
        abstract = str(first_present(item, "abstract", "summary", default="") or "").strip()
        keywords = [str(k) for k in as_list(first_present(item, "keywords", "tags", default=[]))]
        # 与扫描记录合并：同一物理文件只建档一次，内联条目补标题/元数据
        tail = norm(path_str or name)
        scanned_match = None
        if root is not None and tail:
            scanned_match = next((r for r in recs if r["source"] == "scan" and
                                  (r["path_norm"] == tail or r["path_norm"].endswith("/" + tail)
                                   or tail.endswith(r["path_norm"]))), None)
        if scanned_match is not None:
            if title:
                scanned_match["title"] = title
            scanned_match["declared_by_case"] = True
            if inline_text.strip():
                scanned_match["inline_text"] = inline_text
            for k, v in {"abstract": abstract, "keywords": keywords}.items():
                if v and not scanned_match.get(k):
                    scanned_match[k] = v
            scanned_match["metadata"].update({k: v for k, v in meta.items() if v not in (None, "")})
            continue
        fid = str(first_present(item, "doc_id", "id", "file_id", default="")
                  or stable_id("doc", path_str or name, idx, n=8))
        rec = {"file_id": fid, "source": "inline", "path": path_str or name, "name": name,
               "ext": ext, "size": None, "file_sha256": None, "permission_ok": True,
               "permission_code": "", "_abs": local, "title": title, "abstract": abstract,
               "keywords": keywords, "inline_text": inline_text,
               "metadata": meta, "version": str(meta.get("version", "") or ""),
               "alias_of": None, "doc_key": "", "version_basis": ""}
        push(rec)

    alias_groups = build_alias_groups(recs)
    version_groups = build_version_groups(recs, notes)

    # 近重复提示：同归一化标题（doc_key）但正文指纹不同 → 可能是不同版本/修订，人工确认
    seen_keys = {}
    near_dups = []
    for rec in sorted(recs, key=lambda r: r["path"]):
        if rec.get("alias_of"):
            continue
        key = rec.get("doc_key") or doc_key_of(rec)
        body_probe = norm(rec.get("inline_text") or rec.get("title") or rec["name"])
        if key and key in seen_keys and seen_keys[key] != body_probe:
            near_dups.append({"a": seen_keys[key + "@path"], "b": rec["path"], "title_key": key,
                              "note": "同归一化标题但内容不同：近重复（可能为不同版本/修订），"
                                      "建议人工确认，不自动合并"})
        elif key:
            seen_keys[key] = body_probe
            seen_keys[key + "@path"] = rec["path"]

    aux_note = meta_note
    return {"root": root, "root_tried": tried, "root_warn": root_warn, "mode": mode,
            "recs": recs, "scan_recs": scan_recs, "aux_recs": aux_recs, "empty_dirs": empty_dirs,
            "alias_groups": alias_groups, "near_duplicate_hints": near_dups,
            "version_groups": version_groups, "version_notes": notes,
            "metadata_note": aux_note, "metadata_rows": len(meta_rows)}


def detect_garbled_name(name: str) -> bool:
    """乱码文件名启发式：替换字符 ï¿½ 或 latin-1/UTF-8 误解码残迹（Ã/Â/â/æ 引导序列）。"""
    return bool(_MOJIBAKE.search(str(name or "")))


def title_from_body(body: str) -> str:
    for line in str(body or "").splitlines():
        line = line.strip().strip("# ").strip()
        if len(line) >= 6:
            return line
    return ""


def collect_docs(case: dict, work: Path, lib: dict, cfg: dict) -> tuple:
    docs, unreadable = [], []
    notes = lib["version_notes"]
    for idx, rec in enumerate(lib["recs"], start=1):
        if rec.get("_pre_gap"):
            acq = {"extraction_method": "", "n_chars": 0, "text_sha256": "",
                   "gap": "权限不足：文件存在但当前进程不可读", "gap_code": rec["_pre_gap"], "warnings": []}
        else:
            acq = acquire_text(rec, cfg)
        rec["_acq"] = acq
        meta = dict(rec.get("metadata") or {})
        note = match_note_for(rec["path_norm"], notes)
        title = str(rec.get("title") or meta.get("title") or "")
        title_source = "case_or_metadata"
        if not title and note and note.get("doc"):
            title, title_source = note["doc"], "version_note_doc"
        text = rec.get("_text") or ""
        if not acq["gap_code"] and not text:
            text = _text_of(rec, acq)
            rec["_text"] = text
        if not title and not acq["gap_code"]:
            guess = title_from_body(text)
            if guess:
                title, title_source = guess, "body_first_line"
        if not title:
            title_source = "absent"
        doc = {
            "doc_id": rec["file_id"],
            "path": rec["path"],
            "name": rec["name"],
            "ext": rec["ext"],
            "title": title,
            "title_source": title_source,
            "abstract": str(rec.get("abstract") or ""),
            "body": text,
            "keywords": [str(k) for k in as_list(rec.get("keywords") or [])],
            "metadata": meta,
            "local": str(rec.get("_abs") or ""),
            "local_exists": bool(rec.get("_abs") and Path(rec["_abs"]).is_file()),
            "file_sha256": rec.get("file_sha256"),
            "size": rec.get("size"),
            "permission_ok": rec.get("permission_ok", True),
            "extraction": acq,
            "version": rec.get("version") or "",
            "version_basis": rec.get("version_basis") or "",
            "alias_of": rec.get("alias_of"),
            "filename_garbled": detect_garbled_name(rec["name"]),
        }
        docs.append(doc)
        if acq["gap_code"] and not (doc["title"] or doc["abstract"] or text.strip()):
            unreadable.append({"doc_id": doc["doc_id"], "path": doc["path"],
                               "code": acq["gap_code"], "reason": acq["gap"]})
    return docs, unreadable


def _text_of(rec: dict, acq: dict) -> str:
    """从记录重取正文：内联直出；磁盘文件按链重读一次（方法已记入 acq，保证一致）。"""
    inline = str(rec.get("inline_text") or "")
    if inline.strip():
        return inline
    local = rec.get("_abs")
    if local is None or not Path(local).is_file() or not rec.get("permission_ok", True):
        return ""
    sc_txt, _ = read_sidecar_text(Path(local))
    if sc_txt.strip():
        return sc_txt
    ext = rec.get("ext", "")
    if ext == "pdf":
        got = pdf_min.read_pdf(Path(local))
        if got["ok"] and not got["encrypted"]:
            return "\n".join(pg["text"] for pg in got["pages"])
        return ""
    if ext in DOC_EXTS:
        return "\n".join(read_docx_paragraphs(Path(local)))
    if ext in TEXT_EXTS:
        return read_text(Path(local))
    return ""


# --------------------------------------------------------------------------------------
# 字段与命中（分类核心，沿用 0.1 分类学）
# --------------------------------------------------------------------------------------
def field_text(doc: dict, field: str) -> str:
    if field == "title":
        return doc["title"]
    if field == "keywords":
        return " ; ".join(doc["keywords"])
    if field == "abstract":
        return doc["abstract"]
    if field == "body":
        return doc["body"]
    if field == "venue":
        return str(doc["metadata"].get("venue") or doc["metadata"].get("journal") or "")
    return ""


_ASCII_TERM = re.compile(r"^[a-z0-9][a-z0-9 ._+:/\-]*$")


def find_term(text: str, term: str) -> list:
    """返回归一化文本里的出现位置（大小写/全半角归一后），用于可解释命中。

    ASCII 术语按词边界匹配：避免 retrosyn**thesis** 命中 thesis、**si**s 命中 si 这类子串误判。
    CJK 术语按子串匹配（中文无空格分词）。
    """
    nt, nterm = norm(text), norm(term)
    if not nt or not nterm:
        return []
    out = []
    if _ASCII_TERM.match(nterm):
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(nterm) + r"(?![a-z0-9])")
        for m in pat.finditer(nt):
            out.append(m.start())
            if len(out) >= 5:
                break
    else:
        pos = nt.find(nterm)
        while pos != -1 and len(out) < 5:
            out.append(pos)
            pos = nt.find(nterm, pos + 1)
    return out


def score_category(doc: dict, cat: dict, cfg: dict) -> dict:
    fw = cfg.get("field_weights", {})
    hits, total, neg = [], 0.0, 0.0
    for rule in cat.get("metadata_rules", []):
        actual = doc["metadata"].get(rule.get("field", ""))
        allowed = [norm(str(x)) for x in as_list(rule.get("equals", []))]
        if actual is not None and norm(str(actual)) in allowed:
            weight = float(rule.get("weight", 1.0))
            total += weight
            hits.append({"kind": "metadata", "field": rule.get("field", ""), "value": str(actual),
                         "matched": rule.get("label", ""), "weight": weight})
    for term, tw in (cat.get("terms") or {}).items():
        for field in ("title", "keywords", "abstract", "body", "venue"):
            text = field_text(doc, field)
            if not text:
                continue
            offsets = find_term(text, term)
            if offsets:
                weight = float(tw) * float(fw.get(field, 1.0))
                total += weight
                hits.append({"kind": "term", "field": field, "term": term, "normalized_offsets": offsets,
                             "weight": rounded(weight)})
                break
    for term, tw in (cat.get("negative_terms") or {}).items():
        for field in ("title", "keywords", "abstract", "body"):
            if find_term(field_text(doc, field), term):
                neg += float(tw)
                hits.append({"kind": "negative", "field": field, "term": term, "weight": -float(tw)})
                break
    k = float(cfg.get("saturation_k", 1.0))
    raw = max(0.0, total - neg)
    return {"category": cat.get("id", ""), "name": cat.get("name", ""), "raw_score": rounded(raw),
            "negative": rounded(neg), "confidence": rounded(clamp(raw / (raw + k)) if raw > 0 else 0.0),
            "hits": hits}


def classify(doc: dict, taxonomy: dict, cfg: dict) -> dict:
    cats = taxonomy.get("categories", [])
    scored = [score_category(doc, c, cfg) for c in cats]
    scored.sort(key=lambda x: (-x["confidence"], -x["raw_score"], x["category"]))
    high = float(cfg.get("thresholds", {}).get("high_confidence", 0.6))
    review = float(cfg.get("thresholds", {}).get("review_confidence", 0.35))
    max_labels = int(cfg.get("thresholds", {}).get("max_type_labels", 2))
    primary = scored[0] if scored else {"category": "", "confidence": 0.0}
    labels = [s for s in scored if s["confidence"] >= review][:max_labels]
    for s in labels:
        s["level"] = "high" if s["confidence"] >= high else "review"
    if labels and labels[0]["confidence"] >= review:
        status = "classified" if labels[0]["confidence"] >= high else "low_confidence"
    else:
        status = "unclassified"
    return {"status": status, "labels": labels, "primary": primary,
            "alternatives": [s for s in scored[1:3] if s["category"] != primary["category"]]}


def tag_document(doc: dict, vocab: dict, cfg: dict) -> tuple:
    tags, merges = [], []
    field_weight = cfg.get("field_weights", {})
    max_tags = int(cfg.get("thresholds", {}).get("max_tags", 4))
    min_conf = float(cfg.get("thresholds", {}).get("min_tag_confidence", 0.3))
    k = float(cfg.get("saturation_k", 1.0))
    for entry in vocab.get("tags", []):
        canon = entry.get("tag", "")
        variants = [canon] + [str(v) for v in as_list(entry.get("synonyms", []))]
        hit_terms, total = [], 0.0
        for term in variants:
            for field in ("title", "keywords", "abstract", "body"):
                text = field_text(doc, field)
                if not text:
                    continue
                if find_term(text, term):
                    weight = float(entry.get("weight", 1.0)) * float(field_weight.get(field, 1.0))
                    total += weight
                    hit_terms.append({"term": term, "field": field, "weight": rounded(weight)})
                    break
        if not hit_terms:
            continue
        conf = rounded(clamp(total / (total + k)))
        if conf < min_conf:
            continue
        merged = sorted({h["term"] for h in hit_terms if norm(h["term"]) != norm(canon)})
        tags.append({"tag": canon, "parent": entry.get("parent", ""), "confidence": conf,
                     "matched_terms": sorted({h["term"] for h in hit_terms}),
                     "fields": sorted({h["field"] for h in hit_terms})})
        if merged:
            merges.append({"canonical": canon, "merged_from": merged})
    tags.sort(key=lambda x: (-x["confidence"], x["tag"]))
    return tags[:max_tags], merges


# --------------------------------------------------------------------------------------
# 去重 / 版本
# --------------------------------------------------------------------------------------
def fingerprint(doc: dict) -> str:
    raw = "\n".join([doc["title"], doc["abstract"], doc["body"]])
    return sha256_text(norm(raw))


def role_of(doc: dict, role_cfg: dict) -> str:
    """先看 metadata.type，再看标题/路径；ASCII 词按词边界匹配，避免 si⊂sis 之类误判。"""
    t = str(doc["metadata"].get("type", "") or "")
    for role, needles in role_cfg.items():
        if role == "other":
            continue
        if t and any(find_term(t, n) for n in as_list(needles)):
            return role
    for role, needles in role_cfg.items():
        if role == "other":
            continue
        if any(find_term(doc["title"], n) or find_term(doc["path"], n) for n in as_list(needles)):
            return role
    return "other"


def dedupe_and_versions(docs: list, cfg: dict) -> tuple:
    """正文归一指纹强判副本；DOI / 「标题强归一+第一作者」聚内容版本族。

    字节级别名（同 SHA-256 异名）在 build_alias_groups 已判，这里并入 duplicate 视图。
    canonical 选择：角色序（正式版优先）→ 版本序（管理性版本说明/文件名）→ 年份新 → 非别名。
    """
    groups, seen = [], {}
    for d in docs:
        fp = fingerprint(d)
        d["fingerprint"] = fp
        if fp in seen:
            seen[fp].append(d["doc_id"])
        else:
            seen[fp] = [d["doc_id"]]
            groups.append({"sha256": fp, "doc_ids": seen[fp]})
    hash_of = {d["doc_id"]: d["fingerprint"] for d in docs}
    dup_map = {}
    dup_kind = {}
    for d in docs:
        if d.get("alias_of"):
            # 字节级别名（更强判据）优先于正文指纹：canonical 取别名组 canonical 对应 doc_id
            target = next((x for x in docs if x["path"] == d["alias_of"]), None)
            if target is not None and target["doc_id"] != d["doc_id"]:
                dup_map[d["doc_id"]] = target["doc_id"]
                dup_kind[d["doc_id"]] = "byte_alias"
    for g in groups:
        ids = sorted(g["doc_ids"])
        primary = ids[0]
        for other in ids[1:]:
            if other in dup_map:
                continue
            dup_map[other] = primary
            dup_kind[other] = "text_duplicate"
    fam = {}
    for d in docs:
        doi = norm(str(d["metadata"].get("doi", "") or "")).replace("https://doi.org/", "")
        if doi:
            key = f"doi:{doi}"
        else:
            first_author = ""
            authors = as_list(d["metadata"].get("authors") or d["metadata"].get("author"))
            if authors:
                first_author = norm(str(authors[0]))
            key = f"title:{norm_title(d['title'])}|au:{first_author}"
        if norm_title(d["title"]) or doi:
            fam.setdefault(key, []).append(d["doc_id"])
    role_cfg = cfg.get("version_roles", {})
    rank = cfg.get("version_role_rank", {})
    by_id = {d["doc_id"]: d for d in docs}
    families = []
    for key in sorted(fam):
        ids = fam[key]
        if len(ids) < 2:
            continue
        rows = []
        for did in ids:
            d = by_id[did]
            role = role_of(d, role_cfg)
            if did in dup_map:
                role = "duplicate"
            rows.append({"doc_id": did, "year": d["metadata"].get("year"), "path": d["path"],
                         "type": d["metadata"].get("type", ""), "role": role,
                         "version": d.get("version") or ""})
        rows.sort(key=lambda r: (int(rank.get(r["role"], 9)), -version_rank_key(r["version"])[0],
                                 -int(str(r["year"] or 0)[:4] or 0),
                                 r["doc_id"] in dup_map, r["doc_id"]))
        primary = rows[0]["doc_id"]
        for r in rows:
            r["kept_as_primary"] = r["doc_id"] == primary
        families.append({"family": key, "primary_doc": primary, "members": rows,
                         "evidence": "DOI 归一相同" if key.startswith("doi:") else "标题强归一+第一作者相同",
                         "policy": "正式版角色优先、版本序大者优先；全部保留不删除，预印本/修订版/补充材料"
                                   "不当独立文献，归档建议写 ops_log"})
    return groups, dup_map, dup_kind, families, hash_of


# --------------------------------------------------------------------------------------
# 命名与目录（模板渲染 + 降级 + 版本感知冲突消歧）
# --------------------------------------------------------------------------------------
def sanitize(text: str, max_len: int) -> str:
    s = ILLEGAL.sub("-", str(text or ""))
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-{2,}", "-", s).strip("-.")
    return s[:max_len]


def first_author(doc: dict) -> str:
    authors = as_list(doc["metadata"].get("authors") or doc["metadata"].get("author"))
    if not authors:
        return ""
    a = str(authors[0]).strip()
    parts = re.split(r"[\s,]+", a)
    return parts[-1] if len(parts) > 1 and len(parts[-1]) > 1 else a


def render(template: str, values: dict, null_tokens: dict) -> tuple:
    missing = []

    class Safe(dict):
        def __missing__(self, key):
            missing.append(key)
            return null_tokens.get(key, "na")

    from string import Formatter

    used = {f for _, f, _, _ in Formatter().parse(template) if f}
    clean = dict(values)
    for k in sorted(used):
        if str(values.get(k, "")).strip():
            continue
        missing.append(k)
        clean[k] = null_tokens.get(k, "na")
    out = template.format_map(Safe(**clean))
    return out, sorted(set(missing))


def plan_naming(doc: dict, cat_id: str, taxonomy: dict, cfg: dict) -> tuple:
    name_cfg = cfg.get("naming", {})
    null_tokens = name_cfg.get("null_tokens", {})
    cat = next((c for c in taxonomy.get("categories", []) if c.get("id") == cat_id), {})
    dir_tpl = cat.get("dir_template") or name_cfg.get("default_dir_template", "{root}/{doc_type}/{year}")
    year = str(doc["metadata"].get("year") or "")
    values = {
        "root": name_cfg.get("library_root", "library"),
        "doc_type": cat_id or null_tokens.get("doc_type", "unclassified"),
        "year": year[:4] if year else "",
        "project": str(doc["metadata"].get("project_id") or ""),
        "author": first_author(doc),
        "title": sanitize(doc["title"], int(name_cfg.get("title_max_chars", 40))),
        "venue": sanitize(str(doc["metadata"].get("venue") or doc["metadata"].get("journal") or ""), 20),
        "ext": doc["ext"] or "pdf",
    }
    dir_path, dir_missing = render(dir_tpl, values, null_tokens)
    file_tpl = cat.get("name_template") or name_cfg.get("default_name_template", "{author}-{year}-{title}.{ext}")
    file_name, file_missing = render(file_tpl, values, null_tokens)
    file_name = sanitize(Path(file_name).stem, int(name_cfg.get("filename_max_chars", 120))) + "." + values["ext"]
    degraded_reasons = []
    if dir_missing or file_missing:
        degraded_reasons.append("missing_fields:" + ",".join(sorted(set(dir_missing + file_missing))))
    if doc.get("filename_garbled"):
        degraded_reasons.append("filename_garbled")
    if not doc["title"]:
        degraded_reasons.append("no_title")
    return {
        "directory": {"path": dir_path, "template": dir_tpl, "missing_fields": dir_missing,
                      "degraded": bool(dir_missing)},
        "filename": {"from": doc["name"], "to": file_name, "template": file_tpl,
                     "missing_fields": file_missing, "degraded": bool(file_missing),
                     "degraded_reasons": degraded_reasons},
    }


def resolve_name_conflicts(records: list, cfg: dict) -> list:
    """目标名冲突 → 版本感知消歧：追加 __版本/角色/指纹短码，绝不覆盖。

    返回冲突清单（evidence.naming.conflicts 用），并把 records 里冲突者的 filename.to 改写。
    """
    conflicts = []
    dst_owner = {}
    for rec in records:
        if rec["status"] == "unclassified" or rec["duplicate"]["is_duplicate"]:
            continue
        dst = rec["directory"]["path"] + "/" + rec["filename"]["to"]
        dst_owner.setdefault(dst, []).append(rec)
    for dst in sorted(dst_owner):
        owners = dst_owner[dst]
        if len(owners) < 2:
            continue
        for rec in owners:
            stem, dot, ext = rec["filename"]["to"].rpartition(".")
            token = (rec["version"]["role"] if rec["version"]["role"] not in ("single", "other") else "") \
                or rec["version"].get("version_token") or rec["fingerprint"][:6]
            token = sanitize(token, 20) or rec["fingerprint"][:6]
            new_name = f"{stem}__{token}.{ext}"
            rec["filename"]["to"] = new_name
            rec["filename"]["degraded_reasons"] = rec["filename"].get("degraded_reasons", []) + \
                [f"name_conflict_resolved:{token}"]
            rec["filename"]["degraded"] = True
            conflicts.append({"dst": dst, "doc_id": rec["doc_id"], "resolution": new_name,
                              "token": token,
                              "note": "与同目录目标名冲突：按版本/角色/指纹短码消歧，绝不覆盖既有文件"})
    return conflicts


# --------------------------------------------------------------------------------------
# 操作计划（全部建议 + 幂等键 + 回滚；文件库模式绝不改写输入）
# --------------------------------------------------------------------------------------
def build_operations(records: list, cfg: dict, lib: dict) -> tuple:
    ops, seq = [], 0
    lib_mode = lib["mode"] == "library"

    def push(**kw):
        nonlocal seq
        seq += 1
        op = {"op_id": f"OP{seq:04d}", **kw}
        op["input_readonly"] = lib_mode
        op["executed"] = False
        op["suggested_action"] = op.get("action", "")
        ops.append(op)
        return op

    for g in lib["alias_groups"]:
        push(action="merge_duplicate_candidates", targets=g["members"],
             basis=f"SHA-256 完全一致（{g['sha256'][:16]}…）：内容相同、仅文件名不同（别名/完全重复）",
             idempotency_key=stable_id("alias", g["sha256"], n=12),
             rollback={"action": "none", "note": "仅建议保留规范名并移入重复归档目录；未执行，天然可逆"},
             reason=g["note"])
    for rec in records:
        if rec["status"] == "unclassified":
            continue
        src = rec["path"]
        dst = f"{rec['directory']['path']}/{rec['filename']['to']}"
        dup = rec["duplicate"]
        if dup["is_duplicate"]:
            kind = dup.get("kind") or "text_duplicate"
            basis = (f"与 {dup['duplicate_of']} 字节级 SHA-256 相同（别名/完全重复）"
                     if kind == "byte_alias" else
                     f"与 {dup['duplicate_of']} 正文归一指纹相同（同文不同名副本）")
            push(action="archive_duplicate", doc_id=rec["doc_id"], src=src,
                 dst=f"{cfg.get('naming', {}).get('archive_root', 'archive')}/duplicates/{rec['filename']['to']}",
                 basis=basis, kind=kind,
                 idempotency_key=stable_id("dup", rec["doc_id"], dup["duplicate_of"], n=12),
                 rollback={"action": "move", "src": f"{cfg.get('naming', {}).get('archive_root', 'archive')}"
                           f"/duplicates/{rec['filename']['to']}", "dst": src},
                 reason="副本只归档不覆盖不删除；canonical 文件保留原语义")
            continue
        if rec.get("name_conflict_existing"):
            push(action="manual_resolve_conflict", doc_id=rec["doc_id"], src=src, dst=dst,
                 basis=rec["name_conflict_existing"],
                 idempotency_key=stable_id("conflict", rec["doc_id"], dst, n=12),
                 rollback={"action": "none", "note": "未执行任何移动，天然可逆"},
                 reason="目标路径已存在内容不同的文件：禁止覆盖，转人工确认队列")
            continue
        if norm(src) != norm(dst):
            push(action="move_rename", doc_id=rec["doc_id"], src=src, dst=dst,
                 basis=f"目录模板 {rec['directory']['template']} + 命名模板 {rec['filename']['template']}"
                       + ("（缺字段已降级占位）" if rec["filename"]["degraded"] else ""),
                 idempotency_key=stable_id("mv", rec["doc_id"], dst, n=12),
                 rollback={"action": "move_rename", "src": dst, "dst": src},
                 reason="按目录模板与命名模板归档")
    for vg in lib["version_groups"]:
        push(action="consolidate_versions", targets=[m["path"] for m in vg["members"]],
             basis=f"管理性版本说明/文件名版本词归组 {vg['doc_key']}；canonical="
                   f"{vg['canonical']['version']}（{vg['canonical']['path']}）",
             idempotency_key=stable_id("ver", vg["doc_key"], n=12),
             rollback={"action": "none", "note": "仅建议旧版只读归档，未执行任何移动/删除，天然可逆"},
             reason=vg["policy"])
    return ops, seq


def apply_operations(ops: list, work: Path, lib_mode: bool) -> list:
    """仅内联模式下显式 --apply 时执行；文件库模式一律拒绝（输入库只读）。"""
    journal = []
    for op in ops:
        entry = {"op_id": op["op_id"], "action": op["action"],
                 "src": op.get("src", ""), "dst": op.get("dst", "")}
        if lib_mode:
            entry.update({"applied": False, "code": "R_APPLY_READONLY",
                          "reason": "文件库模式：输入库只读，操作仅作建议落 ops_log，未执行"})
            journal.append(entry)
            continue
        src = Path(op.get("src") or "")
        if not str(src):
            entry.update({"applied": False, "code": "R_SKIP_NOOP", "reason": "组级建议操作，无需落盘"})
            journal.append(entry)
            continue
        if not src.is_absolute():
            src = work / src
        dst = Path(op.get("dst") or "")
        if not dst.is_absolute():
            dst = work / dst
        if not src.exists():
            entry.update({"applied": False, "code": "R_SKIP_MISSING",
                          "reason": "源文件不在工作区，未执行（计划仍留存，可续跑）"})
        elif dst.exists():
            entry.update({"applied": False, "code": "R_SKIP_CONFLICT", "reason": "目标已存在，未覆盖"})
        else:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                entry.update({"applied": True, "code": "", "reason": ""})
            except OSError as exc:  # noqa: BLE001
                entry.update({"applied": False, "code": "R_SKIP_OSERROR", "reason": str(exc)})
        journal.append(entry)
    return journal


def verify_untouched(lib: dict) -> dict:
    """流程结束时逐文件重算 SHA-256，与扫描时对照，证明输入库未被改写（含伴随表）。"""
    changed, rechecked, total = [], 0, 0
    for r in list(lib["scan_recs"]) + list(lib.get("aux_recs") or []):
        total += 1
        abs_p = r.get("_abs")
        if abs_p is None or not Path(abs_p).is_file():
            continue
        sha, _ = file_sha256(Path(abs_p))
        rechecked += 1
        if r.get("file_sha256") and sha != r["file_sha256"]:
            changed.append(r["path"])
    return {"files": total, "rechecked": rechecked, "hashes_unchanged": not changed,
            "changed": changed, "input_modified": bool(changed),
            "policy": "输入库全程只读；本流程只在输出目录写 result.json 与操作日志"}


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    taxonomy, vocab, cfg = rules["taxonomy"], rules["tags"], rules["classify"]
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res._contract_orig.keys()))

    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(args.case).resolve().parent
    lib = build_library(case, case_dir, work, cfg)
    docs, unreadable = collect_docs(case, work, lib, cfg)
    res.log("library_intake", mode=lib["mode"], files=len(lib["recs"]),
            scan_files=len(lib["scan_recs"]), alias_groups=len(lib["alias_groups"]),
            version_groups=len(lib["version_groups"]), empty_dirs=len(lib["empty_dirs"]),
            metadata_rows=lib["metadata_rows"])
    res.log("intake", docs=len(docs), unreadable=len(unreadable))

    if not docs:
        res.set("classification", [])
        res.status["overall"] = "empty"
        res.unmet("分类结果", "case.json 未给出任何文件（files/documents/batch 与库目录均为空）")
        res.evidence["inventory"] = {"docs": 0, "library": library_report(lib, [])}
        res.done("盘点")
        return res

    groups, dup_map, dup_kind, families, hash_of = dedupe_and_versions(docs, cfg)
    fam_of = {}
    for fam in families:
        for m in fam["members"]:
            fam_of[m["doc_id"]] = (fam["family"], fam["primary_doc"], m["role"])

    records, unclassified, low_conf, all_merges = [], [], [], []
    for doc in docs:
        cls = classify(doc, taxonomy, cfg)
        tags, merges = tag_document(doc, vocab, cfg)
        all_merges.append({"doc_id": doc["doc_id"], "merges": merges})
        cat_id = cls["labels"][0]["category"] if cls["labels"] else ""
        naming = plan_naming(doc, cat_id, taxonomy, cfg)
        fam = fam_of.get(doc["doc_id"])
        acq = doc["extraction"]
        version_token = version_from_text(doc.get("version") or "") or (doc.get("version") or "")
        rec = {
            "doc_id": doc["doc_id"],
            "path": doc["path"],
            "name": doc["name"],
            "fingerprint": hash_of.get(doc["doc_id"], ""),
            "file_sha256": doc.get("file_sha256"),
            "size": doc.get("size"),
            "extraction": {"method": acq["extraction_method"], "n_chars": acq["n_chars"],
                           "text_sha256": acq["text_sha256"],
                           "gap_code": acq["gap_code"] or None},
            "title": doc["title"],
            "title_source": doc["title_source"],
            "filename_garbled": doc["filename_garbled"],
            "status": cls["status"],
            "doc_type": {
                "primary": cat_id or None,
                "confidence": cls["labels"][0]["confidence"] if cls["labels"] else rounded(cls["primary"]["confidence"]),
                "level": cls["labels"][0]["level"] if cls["labels"] else "none",
                "labels": [{"category": s["category"], "name": s["name"], "confidence": s["confidence"],
                            "level": s["level"], "raw_score": s["raw_score"], "hits": s["hits"]} for s in cls["labels"]],
                "alternatives": [{"category": s["category"], "confidence": s["confidence"],
                                  "reason": "低于阈值未采用"} for s in cls["alternatives"]],
            },
            "tags": tags,
            "directory": naming["directory"],
            "filename": naming["filename"],
            "duplicate": {"is_duplicate": doc["doc_id"] in dup_map,
                          "duplicate_of": dup_map.get(doc["doc_id"]),
                          "kind": dup_kind.get(doc["doc_id"]),
                          "hash_group": next((g["doc_ids"] for g in groups if doc["doc_id"] in g["doc_ids"]), [])},
            "version": ({"family": fam[0], "primary_doc": fam[1], "role": fam[2],
                         "version_token": version_token} if fam else
                        {"family": None, "primary_doc": doc["doc_id"], "role": "single",
                         "version_token": version_token}),
            "metadata": {k: doc["metadata"].get(k) for k in ("authors", "year", "venue", "doi", "type")
                         if doc["metadata"].get(k) not in (None, "")},
        }
        records.append(rec)
        if cls["status"] == "unclassified":
            unclassified.append({
                "doc_id": doc["doc_id"], "path": doc["path"], "name": doc["name"],
                "code": "R_UNCLASSIFIED",
                "reason": "所有类别置信度均低于 review 阈值",
                "extraction_gap": acq["gap_code"] or None,
                "best_candidate": {
                    "category": cls["primary"].get("category") if cls["primary"].get("confidence", 0) > 0 else None,
                    "confidence": cls["primary"].get("confidence", 0.0),
                    "note": "无任何规则命中时不给候选类别" if cls["primary"].get("confidence", 0) <= 0 else "最接近但未达阈值",
                },
                "missing": [f for f in ("title", "year", "venue", "doi", "type")
                            if not (doc["title"] if f == "title" else doc["metadata"].get(f))],
            })
        elif cls["status"] == "low_confidence":
            low_conf.append({"doc_id": doc["doc_id"], "path": doc["path"],
                             "label": cat_id, "confidence": cls["labels"][0]["confidence"],
                             "code": "R_LOW_CONFIDENCE", "note": "入选 review 档，需人工确认后再落入目录"})

    # 命名冲突消歧 + 与库内既有文件冲突检测（绝不覆盖）
    name_conflicts = resolve_name_conflicts(records, cfg)
    existing_conflicts = []
    if lib["root"] is not None:
        root_name = lib["root"].name
        for rec in records:
            if rec["status"] == "unclassified" or rec["duplicate"]["is_duplicate"]:
                continue
            parts = Path(rec["directory"]["path"]).parts
            rel_dir = Path(*parts[1:]) if parts and parts[0] == root_name else Path(rec["directory"]["path"])
            target = lib["root"] / rel_dir / rec["filename"]["to"]
            if target.is_file():
                sha, _ = file_sha256(target)
                if sha and sha != rec.get("file_sha256"):
                    rec["name_conflict_existing"] = \
                        f"目标路径已存在同名不同内容文件 {rel_dir.as_posix()}/{rec['filename']['to']}" \
                        f"（SHA-256 不同）：禁止覆盖"
                    existing_conflicts.append({"doc_id": rec["doc_id"], "dst": rec["filename"]["to"]})

    ops, _ = build_operations(records, cfg, lib)
    apply_mode = "--apply" in (sys.argv[1:] if len(sys.argv) > 1 else [])
    journal = apply_operations(ops, work, lib["mode"] == "library") if apply_mode else []

    dist = {}
    for rec in records:
        key = rec["doc_type"]["primary"] or "unclassified"
        dist[key] = dist.get(key, 0) + 1
    tag_dist = {}
    for rec in records:
        for t in rec["tags"]:
            tag_dist[t["tag"]] = tag_dist.get(t["tag"], 0) + 1

    total = len(records)
    classified = sum(1 for r in records if r["status"] == "classified")
    if classified == total and not unclassified:
        res.status["overall"] = "complete"
    elif classified == 0 and not records:
        res.status["overall"] = "empty"
    elif classified == 0:
        res.status["overall"] = "empty" if total == 0 else "partial"
    else:
        res.status["overall"] = "partial"
    if unclassified:
        res.unmet("未分类", f"{len(unclassified)} 份文件无类别达阈值，已单列并给最接近候选，不强行归类")
    if low_conf:
        res.unmet("低置信", f"{len(low_conf)} 份文件落在 review 档，需人工确认")
    gaps = [{"doc_id": d["doc_id"], "path": d["path"], "code": d["extraction"]["gap_code"],
             "reason": d["extraction"]["gap"], "suggested_action": ""}
            for d in docs if d["extraction"]["gap_code"]]
    for g in gaps:
        g["suggested_action"] = {"R_FILE_ENCRYPTED": "人工提供口令后重跑提取",
                                 "R_FILE_CORRUPT": "换源重新获取文件后重跑",
                                 "R_NO_TEXT_LAYER": "补 OCR 侧车（<file>.ocr.txt）后重跑",
                                 "R_FILE_NO_PERMISSION": "提升读取权限后重跑",
                                 "R_FILE_NO_EXTRACTOR": "转码为 PDF/DOCX/纯文本后重跑",
                                 "R_NO_TEXT": "确认文件内容后重跑"}.get(g["code"], "人工处理后重跑")
    if gaps:
        by_code = {}
        for g in gaps:
            by_code[g["code"]] = by_code.get(g["code"], 0) + 1
        res.unmet("文件提取", "；".join(f"{v} 份 {k}" for k, v in sorted(by_code.items()))
                  + "——诚实跳过，未伪造文本（详见 unprocessed）")
    garbled = [r for r in records if r["filename_garbled"]]
    if garbled:
        res.unsure("乱码文件名", f"{len(garbled)} 份文件名疑似乱码（含替换字符或 latin-1 误解码残迹），"
                               f"已入人工确认队列，命名采用标题/降级占位")
    if name_conflicts:
        res.note(f"{len(name_conflicts)} 处目标名冲突已按版本/角色/指纹短码消歧（绝不覆盖）")
    if existing_conflicts:
        res.unsure("目标冲突", f"{len(existing_conflicts)} 份文件目标名与库内既有不同内容文件冲突，"
                               f"禁止覆盖，转人工确认队列")
    if lib["near_duplicate_hints"]:
        res.note(f"{len(lib['near_duplicate_hints'])} 对近重复（同归一化标题、内容不同）仅提示，不自动合并")
    if lib["root_warn"]:
        res.unmet("文件库目录", lib["root_warn"])

    integrity = verify_untouched(lib)
    if not integrity["hashes_unchanged"]:
        res.unmet("输入完整性", f"输入文件哈希发生变化：{integrity['changed'][:5]}")

    manual_review = (
        [{"doc_id": r["doc_id"], "path": r["path"], "code": "R_LOW_CONFIDENCE",
          "label": r["doc_type"]["primary"], "confidence": r["doc_type"]["confidence"],
          "note": "分类置信度落在 review 档，需人工确认后再入目录"} for r in records
         if r["status"] == "low_confidence"]
        + [{"doc_id": r["doc_id"], "path": r["path"], "code": "R_FILENAME_GARBLE",
            "note": "文件名疑似乱码：保留原文件不动，命名建议以标题为准", "name": r["name"]} for r in garbled]
        + [{"doc_id": r["doc_id"], "path": r["path"], "code": "R_NAME_CONFLICT",
            "note": r["name_conflict_existing"], "dst": r["filename"]["to"]} for r in records
           if r.get("name_conflict_existing")]
    )
    unprocessed = [{"doc_id": d["doc_id"], "path": d["path"], "code": d["extraction"]["gap_code"],
                    "reason": d["extraction"]["gap"], "suggested_action": next(
                        (g["suggested_action"] for g in gaps if g["doc_id"] == d["doc_id"]), "人工处理后重跑")}
                   for d in docs if d["extraction"]["gap_code"]]

    payload = {
        "classification": records,
        "unclassified": unclassified,
        "low_confidence": low_conf,
        "manual_review": manual_review,
        "unprocessed": unprocessed,
        "operations": ops,
        "library_report": library_report(lib, docs),
    }
    records_view = [{"doc_id": r["doc_id"], "path": r["path"], "status": r["status"],
                     "doc_type": r["doc_type"]["primary"], "confidence": r["doc_type"]["confidence"],
                     "tags": [t["tag"] for t in r["tags"]],
                     "dst_dir": r["directory"]["path"], "dst_name": r["filename"]["to"],
                     "duplicate_of": r["duplicate"]["duplicate_of"],
                     "extraction_method": r["extraction"]["method"],
                     "file_sha256": r["file_sha256"]} for r in records]

    res.evidence["rules"] = {
        "taxonomy_version": taxonomy.get("version"),
        "tag_vocabulary_version": vocab.get("version"),
        "classification_rules_version": cfg.get("version"),
        "thresholds": cfg.get("thresholds", {}),
        "field_weights": cfg.get("field_weights", {}),
        "saturation": cfg.get("saturation_k"),
        "formula": "confidence = raw/(raw+k)，raw = Σ(term_weight×field_weight) + metadata 命中权重 − negative 权重",
    }
    res.evidence["inventory"] = {
        "docs": total,
        "readable": total - len(unprocessed),
        "unreadable": unreadable,
        "hash_groups": [{"sha256": g["sha256"], "doc_ids": sorted(g["doc_ids"])} for g in groups],
        "version_families": families,
    }
    res.evidence["library"] = {
        **payload["library_report"],
        "integrity": integrity,
        "hash_policy": "逐文件 SHA-256（字节级）；扫描时与流程结束时各取一次，全部一致才记 hashes_unchanged=true",
        "extraction_chain": "内联正文 → 侧车 .txt/.ocr.txt → pdf_min 文本层 → DOCX zip+xml → 纯文本 → 诚实 gap",
    }
    res.evidence["label_evidence"] = [
        {"doc_id": r["doc_id"], "labels": r["doc_type"]["labels"], "tags": r["tags"]} for r in records
    ]
    res.evidence["tag_normalization"] = {"merges": all_merges,
                                         "policy": vocab.get("normalization", {}),
                                         "distribution": tag_dist}
    res.evidence["naming"] = {
        "templates": cfg.get("naming", {}),
        "degraded_docs": [r["doc_id"] for r in records
                          if r["directory"]["degraded"] or r["filename"]["degraded"]],
        "conflicts": name_conflicts,
        "existing_conflicts": existing_conflicts,
        "garbled_names": [r["doc_id"] for r in garbled],
        "note": "缺字段用 null_token 占位并标 degraded=true；冲突按版本/角色/指纹短码消歧，绝不覆盖",
    }
    res.evidence["distribution"] = {"by_doc_type": dist, "by_tag": tag_dist}
    res.evidence["operations"] = {
        "mode": "apply" if apply_mode else "dry_run",
        "library_mode": lib["mode"] == "library",
        "planned": len(ops),
        "journal": journal,
        "idempotency": "每步带 idempotency_key；重复运行得到同一计划；未处理项在 unprocessed 单列，可续跑",
        "rollback": "每步 rollback 字段给出逆操作或「未执行，天然可逆」说明",
        "readonly_proof": integrity,
    }
    res.evidence["scope"] = {"cases_seen": [res.case_id], "no_network": True,
                             "note": "类别、标签、命名全部由 rules/ 与材料本身推出，未使用外部模型判断"}

    res.set("classification", records)
    res.set("unclassified", unclassified)
    res.set("low_confidence", low_conf)
    res.set("manual_review", manual_review)
    res.set("operations", ops)
    res.set("unprocessed", unprocessed)
    res.set("library_report", payload["library_report"])
    res.set("records_view", records_view)
    res.bind(payload, aliases={"分类": "classification", "归类": "classification", "主结果": "classification",
                               "证据记录": "evidence", "状态说明": "status", "未分类": "unclassified",
                               "未处理项": "unprocessed", "操作日志": "operations", "恢复操作记录": "operations",
                               "文件库盘点": "library_report"})
    bind_contract(res, payload, rules["projection"])

    res.done("文件库盘点", "去重与版本族/版本组", "规则优先分类", "标签归并与粒度控制",
             "命名与目录模板降级", "批量操作计划（建议 + 幂等 + 回滚）")
    return res


def library_report(lib: dict, docs: list) -> dict:
    return {
        "mode": lib["mode"],
        "root": (lib["root"].name if lib["root"] is not None else None),
        "root_resolution": lib["root_tried"] or ["inline（无库目录，使用 case 内联材料）"],
        "files_scanned": len(lib["scan_recs"]) + len(lib.get("aux_recs") or []),
        "documents": len(lib["recs"]),
        "auxiliary_files": [{"path": r["path"], "sha256": r.get("file_sha256"),
                             "role": "伴随元数据表：建档辅助，不参与分类"} for r in lib.get("aux_recs") or []],
        "empty_dirs": lib["empty_dirs"],
        "metadata_source": lib["metadata_note"],
        "metadata_rows": lib["metadata_rows"],
        "alias_groups": lib["alias_groups"],
        "near_duplicate_hints": lib["near_duplicate_hints"],
        "version_groups": lib["version_groups"],
        "version_notes_fields": ", ".join(VERSION_NOTE_KEYS),
        "gaps": [{"path": d["path"], "code": d["extraction"]["gap_code"], "reason": d["extraction"]["gap"]}
                 for d in docs if d["extraction"]["gap_code"]],
        "extraction_methods": {d["path"]: {"method": d["extraction"]["extraction_method"] or None,
                                           "n_chars": d["extraction"]["n_chars"],
                                           "text_sha256": d["extraction"]["text_sha256"] or None,
                                           "file_sha256": d.get("file_sha256"),
                                           "size": d.get("size")}
                               for d in docs},
    }


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
        "classification": [],
        "unclassified": [{"code": "R_INPUT_ERROR", "reason": str(exc)}],
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
                          "docs": len(payload.get("classification", []))}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    lib_mode = payload.get("library_report", {}).get("mode")
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "classified": sum(1 for r in payload.get("classification", []) if r["status"] == "classified"),
                      "unclassified": len(payload.get("unclassified", [])),
                      "unprocessed": len(payload.get("unprocessed", [])),
                      "library_mode": lib_mode}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
