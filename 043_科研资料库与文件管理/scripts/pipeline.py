#!/usr/bin/env python3
"""043 科研资料库与文件管理 —— 真实文件库工单适配版（纯标准库、离线、确定性、可复核）。

工单形态（对齐 detail.md「Actual file library and current task workorder」）：
组织方授权的真实 N 文档资料库修复：真实文件（含 PDF/DOCX）、别名副本、行政性
版本说明、伴随元数据表、检索/问答请求、真实的恢复与索引操作建议。本脚本即该
工单的执行体，绝不改写输入库，绝不补造缺失材料。

主流程（13 阶段，与 evidence.operation_log[].step 一一对应）：
  load_case → workorder → inventory → extract → dedupe → versions → metadata
  → plan → apply → archive → index → requests → ops → report

  1. workorder  库目录解析（materials_root/library_path/files_root/library.root…
                字段名容错）、目录/命名模板、伴随表、人工补充表、版本说明、请求
  2. inventory  目录级扫描（os.walk 排序遍历）：逐文件 SHA-256/大小/类型/权限位/
                mtime；剔除 .ocr.txt/.ocr.json 与二进制件伴生 .txt/.json 侧车
                （侧车入台账不作文档）；软链接不跟随；空文件/不可读/超上限全部
                记原因码，不静默
  3. extract    文本获取链：侧车 → pdf_min 提取 PDF 文本层 → DOCX zip+xml 段落
                → 纯文本；加密/损坏/无文本层分别记 R_FILE_ENCRYPTED /
                R_FILE_CORRUPT / R_NO_TEXT_LAYER，诚实跳过不伪造文本
  4. dedupe     按内容哈希分组：同哈希异名 = 别名/完全重复；canonical 选择 =
                非诱饵名（copy/backup/latest 降权）→ 名最短 → mtime 最早 → 路径
  5. versions   按行政性版本说明（version_notes/admin_notes/versions）+ 文件名
                模式归版本组；canonical = 版本序最大（final>vN>draft）、并列时
                非副本优先、诱饵名降权；重建 谁替代谁 的链并写差异说明；冲突
                显式进 conflicts/unmet
  6. metadata   元数据字段抽取：人工给定 > 伴随表 > 内容 > 文件名解析 > 目录名 >
                扩展名；每个字段记出处（provenance），未知记「未知」不补造；
                伴随表与文件名冲突时按出处分级取舍并记录冲突明细
  7. plan       目录模板与命名模板渲染 → 目标路径建议树（同目录序号自增）
  8. apply      只向 --out/library 新建复制并逐文件重算哈希核验；目标已存在且
                内容相同 → noop；内容不同 → W_TARGET_EXISTS 不覆盖
  9. archive    归档校验：源库全程前后哈希对照（证未动）+ out/library 归档清单
                （文件数/总字节/逐文件哈希）与完整性核验
  10. index     可检索索引（canonical 归一、术语）
  11. requests  检索/去重/版本请求逐条应答（未知请求类型进 uncertainty）
  12. ops       ops_log：入库/合并/版本归并/修复/索引重建全部为「建议，未执行，
                输入只读」，逐条附依据
  13. report    三段契约：资料结果 / 操作证据 / 失败与恢复

硬约束：只用标准库、不联网；静默跳过禁止；同名不覆盖；幂等（同输入除
generated_at 外逐字节一致）；未完成绝不写成完成。
"""

from __future__ import annotations

import hashlib
import json
import os
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
    ensure_dir,
    first_present,
    load_case,
    load_json,
    load_task_text,
    norm,
    norm_title,
    parse_args,
    read_table_snapshot,
    read_text,
    resolve_table_ref,
    sha256_text,
    stable_id,
    tokens,
)
import pdf_min  # noqa: E402

SKILL = "research-file-library"
RULES_DIR = Path(__file__).resolve().parent.parent / "rules"
SCRIPT_VERSION = "0.2.0"

ALIASES = {
    "资料结果": "主结果",
    "文件清单": "主结果",
    "目录树": "主结果",
    "主结果": "主结果",
    "main_result": "主结果",
    "操作证据": "证据",
    "证据记录": "证据",
    "evidence": "证据",
    "failure_recovery": "失败与恢复",
    "失败与恢复": "失败与恢复",
    "状态说明": "失败与恢复",
    "status": "失败与恢复",
}

# 库目录字段名容错（工单可能用 materials_root / library_path / files_root 等）
LIBRARY_KEYS = ("materials_root", "library_path", "files_root", "library_dir",
                "library_root", "material_root")
VERSION_NOTE_KEYS = ("version_notes", "admin_notes", "versions")
COMPANION_KEYS = ("metadata_table", "companion_table", "manifest_csv", "manifest_json",
                  "metadata_csv", "manifest")
# 伴随表文件名（库根目录下自动识别；也是合法文档，照常入库）
COMPANION_FILENAMES = ("metadata.csv", "manifest.csv", "metadata.json", "manifest.json")

BINARY_EXTS = {"pdf", "docx", "pptx", "xlsx", "png", "jpg", "jpeg", "tif", "tiff", "bin"}
DOCX_EXTS = {"docx"}
TEXT_EXTS = {"md", "txt", "markdown", "rst", "csv", "tsv", "json", "tex", "log", "html", "htm"}

# 版本/诱饵词：版本排序与 canonical 降权共用
_VERSION_TOKEN = re.compile(r"(draft|final|v(?:er|ersion)?[._ -]?(\d+)|rev[._ -]?(\d+)|latest|copy|backup)", re.I)
_BAIT_NAME = re.compile(r"(copy|backup|latest)", re.I)


def load_rules() -> dict:
    out = {}
    for name in ("dedupe.json", "field_dictionary.json", "convention.json"):
        path = RULES_DIR / name
        if path.exists():
            out[name.split(".")[0]] = json.loads(path.read_text(encoding="utf-8"))
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iso_mtime(path: Path) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")


# 元数据出处标签（评分六要素之一「元数据出处」的统一口径）
PROV_LABEL = {
    "filename": "文件名解析",
    "directory": "目录名解析",
    "extension": "扩展名推断",
    "content": "内容提取",
    "companion": "伴随表",
    "manual": "人工给定",
    "absent": "未知",
}


# --------------------------------------------------------------------------------------
# 阶段 1：工单（workorder）
# --------------------------------------------------------------------------------------
def resolve_root(case: dict, case_path: Path) -> tuple:
    """库目录解析：字段名容错；相对路径按 cwd → case 同目录解析。

    展示路径统一相对 case 目录（display_root），保证 --case 传相对/绝对路径
    结果逐字节一致（幂等口径）。返回 (绝对解析路径, exclude, 展示用 tried)。"""
    case_dir = case_path.parent

    def display_root(p: Path) -> str:
        try:
            return p.resolve().relative_to(case_dir.resolve()).as_posix() or "."
        except ValueError:
            return str(p)

    raw = first_present(case, "library", "root", "workdir", default=None)
    root, exclude = "", []
    if isinstance(raw, dict):
        root = str(first_present(raw, "root", "path", "dir", default=""))
        exclude = [str(x) for x in as_list(first_present(raw, "exclude", "skip_dirs", default=[]))]
    elif raw:
        root, exclude = str(raw), []
    if not root:
        for key in LIBRARY_KEYS:
            val = case.get(key)
            if isinstance(val, str) and val.strip():
                root = val.strip()
                break
            if isinstance(val, dict):
                root = str(first_present(val, "root", "path", "dir", default=""))
                exclude = [str(x) for x in as_list(first_present(val, "exclude", "skip_dirs", default=[]))]
                if root:
                    break
    cands = [Path(root)] if root else []
    if root and not Path(root).is_absolute():
        cands.append(case_dir / root)
    tried = [display_root(c) for c in cands]
    for c in cands:
        if c.exists() and c.is_dir():
            return c.resolve(), exclude, tried
    return (cands[-1] if cands else Path("."), exclude, tried)


def parse_version_notes(case: dict) -> dict:
    """行政性版本说明：{match/path/file, doc, version, note} → 按归一化路径后缀匹配。"""
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
            notes[norm(match)] = {
                "match": match,
                "doc": str(first_present(item, "doc", "document", "title", default="") or ""),
                "version": str(first_present(item, "version", "ver", default="") or ""),
                "note": str(first_present(item, "note", "admin_note", default="") or ""),
            }
    return notes


def match_note(path_norm: str, notes: dict) -> dict | None:
    if path_norm in notes:
        return notes[path_norm]
    for k in sorted(notes, key=len, reverse=True):
        if path_norm.endswith("/" + k) or path_norm.endswith(k):
            return notes[k]
    return None


def load_companion_rows(case: dict, case_path: Path, root: Path) -> list:
    """伴随元数据表（CSV/JSON/内联 rows）：逐行带 provenance=伴随表[文件名]。"""
    rows, seen = [], set()

    def push_rows(snapshot, label):
        for row in snapshot.get("rows") or []:
            if isinstance(row, dict):
                rows.append({**row, "_provenance": f"伴随表[{label}]"})

    # 1) case 显式引用（metadata_table / companion_table / manifest_csv…）
    for key in COMPANION_KEYS:
        val = case.get(key)
        for item in as_list(val):
            try:
                if isinstance(item, str):
                    path, _cand = resolve_table_ref(item, str(case_path.parent))
                    snap = read_table_snapshot(path, str(case_path.parent))
                    push_rows(snap, Path(item).name or item)
                elif isinstance(item, dict) and first_present(item, "path", "file", default=""):
                    ref = str(first_present(item, "path", "file", default=""))
                    path, _cand = resolve_table_ref(ref, str(case_path.parent))
                    snap = read_table_snapshot(path, str(case_path.parent))
                    push_rows(snap, Path(ref).name or ref)
                elif isinstance(item, dict):
                    snap = read_table_snapshot(item, str(case_path.parent))
                    push_rows(snap, key)
            except Exception as exc:  # noqa: BLE001 —— 伴随表读取失败不静默
                rows.append({"_provenance": f"伴随表[{key}]", "_load_error": str(exc)[:160]})
    # 2) 库根目录自动识别 metadata.csv / manifest.csv / metadata.json / manifest.json
    if root and Path(root).is_dir():
        for name in COMPANION_FILENAMES:
            cand = Path(root) / name
            if cand.is_file() and str(cand) not in seen:
                seen.add(str(cand))
                try:
                    snap = read_table_snapshot(cand, str(Path(root)))
                    push_rows(snap, name)
                except Exception as exc:  # noqa: BLE001
                    rows.append({"_provenance": f"伴随表[{name}]", "_load_error": str(exc)[:160]})
    return rows


def build_workorder(case: dict, task_text: str, rules: dict, case_path: Path) -> dict:
    root, exclude, tried = resolve_root(case, case_path)
    try:
        root_display = Path(root).resolve().relative_to(case_path.parent.resolve()).as_posix() or "."
    except ValueError:
        root_display = Path(root).name or str(root)
    conv = first_present(case, "convention", "layout", default={}) or {}
    if not isinstance(conv, dict):
        conv = {}
    dfl = rules.get("convention", {})
    supplement = []
    for row in as_list(first_present(case, "manifest_supplement", "supplement", "manual_metadata", default=[])):
        if isinstance(row, dict):
            target = str(first_present(row, "path", "file", "name", default="") or "")
            row = {k: v for k, v in row.items() if k not in ("path", "file", "name")}
            supplement.append({**row, "_provenance": f"人工给定[{target or 'supplement'}]", "_target": target})
    return {
        "root": str(root),
        "root_display": root_display,
        "root_tried": tried,
        "exclude": exclude or dfl.get("default_exclude", []),
        "directory_template": str(first_present(conv, "directory_template", "dir_template",
                                                default=dfl.get("default_directory_template", "{project}/{year}/{kind}"))),
        "naming_template": str(first_present(conv, "naming_template", "name_template",
                                            default=dfl.get("default_naming_template", "{project}_{year}_{kind}_{seq}{ext}"))),
        "supplement": supplement,
        "companion": load_companion_rows(case, case_path, Path(root)),
        "version_notes": parse_version_notes(case),
        "requests": [
            {"id": str(first_present(r, "id", default=f"Q{i}")),
             "type": str(first_present(r, "type", "op", default="search")),
             "text": str(first_present(r, "text", "query", "target", default=""))}
            for i, r in enumerate(as_list(first_present(case, "requests", "queries", default=[])), start=1)
            if isinstance(r, dict)
        ],
        "max_files": int(first_present(case, "max_files", default=dfl.get("max_files", 5000)) or 5000),
        "task_text": task_text,
    }


# --------------------------------------------------------------------------------------
# 阶段 2：盘点（目录级扫描，失败不静默）
# --------------------------------------------------------------------------------------
def is_sidecar_name(name: str, sibling_names: set) -> bool:
    """侧车判定：<f>.ocr.txt / <f>.ocr.json；以及二进制件的伴生 <f>.txt/.json。"""
    if name.endswith(".ocr.txt") or name.endswith(".ocr.json"):
        return True
    for suf in (".txt", ".json"):
        if name.endswith(suf):
            base = name[: -len(suf)]
            base_ext = Path(base).suffix.lstrip(".").lower()
            if base in sibling_names and base_ext in BINARY_EXTS:
                return True
    return False


def inventory(wo: dict) -> tuple:
    root = Path(wo["root"])
    exclude = {norm(e) for e in wo["exclude"]}
    entries, skipped, sidecars = [], [], []
    if not root.exists():
        return entries, [{"path": str(root), "code": "S_ROOT_MISSING", "reason": "资料库根目录不存在"}], sidecars
    if not root.is_dir():
        return entries, [{"path": str(root), "code": "S_ROOT_NOT_DIR", "reason": "library 路径存在但不是目录"}], sidecars

    # 第一遍：只收集候选路径（确定性排序），用于侧车判定
    candidates = []
    walk_errors = []

    def _onerror(exc):
        walk_errors.append({"path": getattr(exc, "filename", ""), "code": "S_DIR_UNREADABLE",
                            "reason": f"目录不可读（权限不足或其他错误）：{str(exc)[:120]}"})

    for dirpath, dirnames, filenames in os.walk(root, onerror=_onerror):
        dirnames.sort()
        dirnames[:] = [d for d in dirnames if norm(d) not in exclude]
        for name in sorted(filenames):
            candidates.append(Path(dirpath) / name)
    skipped.extend(walk_errors)
    names = {p.name for p in candidates}

    count = 0
    for p in candidates:
        rel = str(p.relative_to(root))
        if is_sidecar_name(p.name, names):
            sidecars.append({"path": rel, "owner": p.name.endswith(".ocr.txt") and p.name[:-8]
                             or p.name.endswith(".ocr.json") and p.name[:-9]
                             or p.name.rsplit(".", 1)[0],
                             "kind": "ocr_sidecar" if ".ocr." in p.name else "companion_sidecar"})
            continue
        count += 1
        if count > wo["max_files"]:
            skipped.append({"path": rel, "code": "S_OVER_MAX_FILES",
                            "reason": f"超过单次处理上限 {wo['max_files']}，本轮未纳入"})
            continue
        if p.is_symlink():
            skipped.append({"path": rel, "code": "S_SYMLINK",
                            "reason": "软链接不跟随（防目录逃逸与重复计数），需人工确认目标"})
            continue
        try:
            st = p.stat()
        except OSError as exc:
            skipped.append({"path": rel, "code": "S_STAT_FAILED", "reason": str(exc)[:160]})
            continue
        if st.st_size == 0:
            skipped.append({"path": rel, "code": "S_EMPTY_FILE", "reason": "0 字节文件，哈希无区分度，未入库"})
            continue
        try:
            digest = sha256_file(p)
        except OSError as exc:
            skipped.append({"path": rel, "code": "S_READ_FAILED", "reason": str(exc)[:160]})
            continue
        try:
            text_probe = p.read_bytes()[:4096]
            text_probe.decode("utf-8")
            decodable = True
        except (UnicodeDecodeError, OSError):
            decodable = False
        entries.append({
            "rel_path": rel,
            "name": p.name,
            "dir": str(Path(rel).parent) if str(Path(rel).parent) != "." else "",
            "ext": p.suffix.lower(),
            "bytes": st.st_size,
            "sha256": digest,
            "mode": oct(st.st_mode & 0o777),
            "mtime": iso_mtime(p),
            "decodable_utf8": decodable,
            "text": "",
            "extraction_method": "",
            "extraction_gap_code": "",
            "extraction_gap": "",
            "ocr_confidence": None,
        })
    return entries, skipped, sidecars


# --------------------------------------------------------------------------------------
# 阶段 3：文本获取链（侧车 → PDF → DOCX → 纯文本；失败诚实记码）
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


def sidecar_text_for(root: Path, entry: dict, sidecars: list) -> tuple:
    """为扫描件/二进制件找伴生侧车文本与 OCR 元数据（不入文档清单，只作提取辅助）。"""
    rel = entry["rel_path"]
    base = str(Path(root) / rel)
    cands = [Path(base + ".ocr.txt"), Path(base).with_name(Path(base).stem + ".ocr.txt"),
             Path(base + ".txt"), Path(base).with_name(Path(base).stem + ".txt")]
    for cand in cands:
        if cand.resolve() == Path(base).resolve():
            continue
        txt = read_text(cand)
        if txt.strip():
            ocr = {}
            for jcand in (Path(base + ".ocr.json"), Path(base).with_name(Path(base).stem + ".ocr.json")):
                data = load_json(jcand, default=None)
                if isinstance(data, dict):
                    ocr = data
                    break
            method = "sidecar_ocr_text" if ".ocr." in cand.name else "sidecar_text"
            return txt, method, ocr
    return "", "", {}


def extract_texts(wo: dict, entries: list, sidecars: list) -> None:
    root = Path(wo["root"])
    for e in entries:
        ext = e["ext"].lstrip(".")
        # 1) 侧车优先（扫描件代表：正文由 .ocr.txt 提供）
        sc_txt, sc_method, ocr = sidecar_text_for(root, e, sidecars)
        if sc_txt.strip():
            e["text"] = sc_txt
            e["extraction_method"] = sc_method
            conf = ocr.get("mean_confidence", ocr.get("confidence"))
            try:
                e["ocr_confidence"] = round(float(conf), 4) if conf is not None else None
            except (TypeError, ValueError):
                e["ocr_confidence"] = None
            continue
        if ext == "pdf":
            got = pdf_min.read_pdf(Path(root) / e["rel_path"])
            if got["encrypted"]:
                e["extraction_gap_code"] = "R_FILE_ENCRYPTED"
                e["extraction_gap"] = got["errors"][0] if got["errors"] else "检测到 /Encrypt（加密件），不做解密，诚实跳过文本提取"
                continue
            if got["errors"] and not got["ok"]:
                corrupt = bool(got.get("streams_failed")) or not got.get("streams_total")
                e["extraction_gap_code"] = "R_FILE_CORRUPT" if corrupt else "R_NO_TEXT_LAYER"
                e["extraction_gap"] = "；".join(got["errors"])
                continue
            e["text"] = "\n".join(pg["text"] for pg in got["pages"])
            e["extraction_method"] = "pdf_textlayer"
            continue
        if ext in DOCX_EXTS:
            paras = read_docx_paragraphs(Path(root) / e["rel_path"])
            if not paras:
                e["extraction_gap_code"] = "R_FILE_CORRUPT"
                e["extraction_gap"] = "DOCX 无法解析出段落（zip 损坏或结构非法），未抽取文本"
                continue
            e["text"] = "\n".join(paras)
            e["extraction_method"] = "docx_xml"
            continue
        if ext in TEXT_EXTS and e["decodable_utf8"]:
            e["text"] = read_text(Path(root) / e["rel_path"])
            e["extraction_method"] = "plain_text"
            continue
        # 二进制无侧车/无提取器
        if not e["decodable_utf8"]:
            e["extraction_gap_code"] = "R_FILE_NO_EXTRACTOR"
            e["extraction_gap"] = "非 UTF-8 二进制且无侧车文本：按字节哈希入库，内容未抽取"
        else:
            e["extraction_gap_code"] = "R_FILE_NO_EXTRACTOR"
            e["extraction_gap"] = f"扩展名 .{ext or '未知'} 无可用提取器，内容未抽取"


# --------------------------------------------------------------------------------------
# 阶段 4/5：去重与版本链（全部按内容/身份，绝不按文件名判重复）
# --------------------------------------------------------------------------------------
def version_token_of(name: str, rules: dict) -> int | None:
    cfg = rules.get("dedupe", {})
    stem, _ext = os.path.splitext(name)
    m = re.search(cfg.get("version_pattern", r"[_-][vV](\d+)"), stem)
    if m:
        return int(m.group(1))
    m2 = re.search(cfg.get("version_pattern_loose", r"(?i)(?:^|[^a-z])(v|rev)[._ -]?(\d+)"), stem)
    if m2:
        return int(m2.group(2))
    return None


def version_rank(version: str) -> tuple:
    """版本排序键：final > v<n>/rev<n> > draft > 其他；同档按数字大者优先。确定性。"""
    v = str(version or "").lower().replace(" ", "")
    m = re.search(r"(\d+)", v)
    num = m.group(1).zfill(4) if m else ""
    order = 3 if "final" in v else (2 if re.search(r"v|rev", v) else (1 if "draft" in v else 0))
    return (order, num, v)


def doc_key_of(name: str, explicit_doc: str) -> str:
    """文档同一性键：行政性说明显式 doc > 文件名去版本/副本词后的强归一。"""
    if explicit_doc:
        return norm_title(explicit_doc)
    stem, ext = os.path.splitext(name)
    stripped = _VERSION_TOKEN.sub(" ", stem)
    return (norm_title(stripped) + ext.lower()) or norm_title(name)


def identity_key(name: str, rules: dict) -> str:
    """旧口径兼容：去版本号与副本标记后的身份键（版本组回退判据）。"""
    stem, ext = os.path.splitext(name)
    cfg = rules.get("dedupe", {})
    m = re.search(cfg.get("version_pattern", r"[_-][vV](\d+)"), stem)
    if m:
        stem = stem[: m.start()]
    m2 = re.search(cfg.get("copy_pattern", r"\s*\(\d+\)$"), stem)
    if m2:
        stem = stem[: m2.start()]
    return norm_title(stem) + ext.lower()


def dedupe(entries: list, rules: dict) -> tuple:
    """同哈希异名 = 别名/完全重复。canonical：非诱饵名（copy/backup/latest 降权）→
    归一名最短 → mtime 最早 → 路径字典序。"""
    cfg = rules.get("dedupe", {})
    groups: dict = {}
    for e in entries:
        groups.setdefault(e["sha256"], []).append(e)
    dup_groups, canonical_of = [], {}
    for digest, items in sorted(groups.items()):
        items_sorted = sorted(items, key=lambda x: (bool(_BAIT_NAME.search(x["name"])),
                                                    len(norm_title(x["name"])), x["mtime"], x["rel_path"]))
        canonical = items_sorted[0]
        canonical_of[digest] = canonical["rel_path"]
        if len(items_sorted) > 1:
            dup_groups.append({
                "sha256": digest,
                "bytes": items_sorted[0]["bytes"],
                "count": len(items_sorted),
                "canonical": canonical["rel_path"],
                "aliases": [x["rel_path"] for x in items_sorted[1:]],
                "rule": cfg.get("canonical_rule_alias",
                                "同 SHA-256 异名 = 完全重复/别名；canonical = 非诱饵名（copy/backup/latest 降权）→ 名归一最短 → mtime 最早 → 路径字典序"),
            })
    return dup_groups, canonical_of


def build_versions(entries: list, rules: dict, version_notes: dict, canonical_of: dict) -> tuple:
    """版本组重建：行政性版本说明（version_notes/admin_notes/versions）优先，
    文件名模式回退（basis 如实标注）。canonical = 版本序最大者，并列时非副本/
    非诱饵名优先。链 = 按版本序的 谁替代谁，逐条写差异说明。冲突显式返回。"""
    groups: dict = {}
    notes_unmatched = set(version_notes)
    for e in entries:
        note = match_note(norm(e["rel_path"]), version_notes)
        if note:
            key = norm(note["match"])
            if key in notes_unmatched:
                notes_unmatched.discard(key)
        canon_rel = canonical_of.get(e["sha256"])
        inherited = ""
        if (not note) and canon_rel and canon_rel != e["rel_path"]:
            # 别名副本未带版本说明：继承同哈希规范件的行政性版本说明（如实标注 inherited）
            note = match_note(norm(canon_rel), version_notes)
            inherited = canon_rel
        explicit_doc = str((note or {}).get("doc", "") or "")
        ver_note = str((note or {}).get("version", "") or "")
        tok = version_token_of(e["name"], rules)
        if ver_note:
            version, basis = ver_note, "version_note"
        elif tok is not None:
            version, basis = f"v{tok}", "filename_pattern"
        else:
            version, basis = "", ""
        key = doc_key_of(e["name"], explicit_doc)
        item = {**e, "version": version, "version_basis": basis, "version_note": note or {},
                "note_inherited_from": inherited,
                "identity": key, "is_copy": bool(re.search(r"\s*\(\d+\)$", os.path.splitext(e["name"])[0])
                                               or _BAIT_NAME.search(e["name"]))}
        groups.setdefault(key, []).append(item)

    chains, conflicts, near_dups = [], [], []
    for key in sorted(groups):
        members = groups[key]
        versions = [m for m in members if m["version"]]
        digests = {m["sha256"] for m in members}
        if len(members) < 2 or len(digests) < 2:
            continue
        if len(versions) < 2:
            # 同身份不同内容但无版本信息：近重复提示，不强行成链
            near_dups.append({"identity": key, "members": [m["rel_path"] for m in sorted(members, key=lambda x: x["rel_path"])],
                              "note": "同归一化标题/身份但内容哈希不同且版本信息不足：按近重复提示，需人工确认是否不同修订，未自动合并"})
            continue
        max_rank = max(version_rank(m["version"]) for m in versions)
        tops = [m for m in versions if version_rank(m["version"]) == max_rank]
        canonical = sorted(tops, key=lambda m: (bool(_BAIT_NAME.search(m["name"])), m["rel_path"]))[0]
        ordered = sorted(members, key=lambda m: (version_rank(m["version"]), m["mtime"], m["rel_path"]))
        chain = []
        for i, item in enumerate(ordered):
            prev = ordered[i - 1] if i else None
            delta = None
            if prev is not None:
                delta = {"supersedes": prev["rel_path"], "bytes_delta": item["bytes"] - prev["bytes"],
                         "same_content": item["sha256"] == prev["sha256"]}
                if prev["text"].strip() and item["text"].strip():
                    a = set(x for x in prev["text"].splitlines() if x.strip())
                    b = set(x for x in item["text"].splitlines() if x.strip())
                    delta["lines_added"] = len(b - a)
                    delta["lines_removed"] = len(a - b)
                if item["sha256"] == prev["sha256"]:
                    delta["note"] = "内容与上一版完全一致（SHA-256 相同）：属于副本/别名，不构成新版本"
                elif prev["version"] and item["version"]:
                    delta["note"] = f"{prev['version']} → {item['version']}：按行政性版本说明/文件名模式排序的差异说明"
            chain.append({"order": i + 1, "path": item["rel_path"], "sha256": item["sha256"],
                          "version": item["version"], "version_basis": item["version_basis"],
                          "note_inherited_from": item["note_inherited_from"] or None,
                          "mtime": item["mtime"], "bytes": item["bytes"],
                          "admin_note": (item["version_note"] or {}).get("note", ""),
                          "duplicate_of": canonical_of.get(item["sha256"], "")
                          if canonical_of.get(item["sha256"]) != item["rel_path"] else "",
                          "diff_vs_previous": delta})
        # 冲突检测：a) 版本说明标注「当前版/最新」的成员不在最高版本序上；
        # b) 版本说明版本与文件名版本号不一致；c) 最高版本序并列
        current_marked = [m for m in versions if re.search(r"当前|最新|current", (m["version_note"] or {}).get("note", ""), re.I)]
        for m in current_marked:
            if version_rank(m["version"]) < max_rank:
                conflicts.append({"code": "W_VERSION_CONFLICT", "path": m["rel_path"],
                                  "reason": (f"行政性版本说明把 {m['rel_path']}（{m['version']}）标为当前版，"
                                             f"但组内存在更高版本序 {canonical['version']}（{canonical['rel_path']}）："
                                             f"以版本序为准，冲突显式记录，需人工裁决")})
        if len(tops) > 1 and len({m['sha256'] for m in tops}) > 1:
            conflicts.append({"code": "W_VERSION_TIE", "path": key,
                              "reason": f"版本组 {key} 最高版本序 {canonical['version']} 并列多个不同内容成员："
                                        f"canonical 取非诱饵名 {canonical['rel_path']}，其余需人工确认"})
        filename_tok = {m["rel_path"]: version_token_of(m["name"], rules) for m in versions}
        for m in versions:
            tok = filename_tok[m["rel_path"]]
            note_ver = (m["version_note"] or {}).get("version", "")
            note_num = re.search(r"(\d+)", str(note_ver))
            if tok is not None and note_num and int(note_num.group(1)) != tok:
                conflicts.append({"code": "W_VERSION_NOTE_MISMATCH", "path": m["rel_path"],
                                  "reason": (f"文件名版本号 v{tok} 与行政性版本说明「{note_ver}」的数字不一致："
                                             f"以版本说明为准（basis=version_note），冲突显式记录")})
        chains.append({"identity": key, "count": len(chain),
                       "version_note_source": "version_note" if any(m["version_basis"] == "version_note" for m in versions) else "filename_pattern",
                       "canonical": {"path": canonical["rel_path"], "version": canonical["version"]},
                       "supersedes": [c["path"] for c in chain],
                       "latest": chain[-1]["path"], "chain": chain,
                       "policy": "canonical=版本序最大（final>vN>draft，并列非副本/非诱饵名优先）；旧版只读归档建议，不删除"})
    notes_unmet = [{"code": "S_NOTE_UNMATCHED", "path": version_notes[k]["match"],
                    "reason": f"行政性版本说明指向的文件不在库中（doc={version_notes[k]['doc']}）：未凭空建档，需人工核对路径"}
                   for k in sorted(notes_unmatched)]
    return chains, conflicts, near_dups, notes_unmet


# --------------------------------------------------------------------------------------
# 阶段 6：元数据（逐字段出处：人工给定 > 伴随表 > 内容 > 文件名 > 目录名 > 扩展名）
# --------------------------------------------------------------------------------------
_PROV_ORDER = {"manual": 6, "companion": 5, "content": 4, "filename": 3, "directory": 2, "extension": 1}


def first_title_line(text: str) -> str:
    """正文首个有效行 → 标题（跳过空行与 [[page:N]] 侧车页标记）。"""
    for ln in text.splitlines():
        s = ln.strip("# ").strip()
        if not s or s.startswith("[[page:"):
            continue
        return s[:80]
    return ""


def metadata_of(entry: dict, wo: dict, rules: dict) -> dict:
    fd = rules.get("field_dictionary", {})
    patterns = fd.get("filename_patterns", {})
    fields, prov, conflicts = {}, {}, []

    def put(name, value, source_label, source_key):
        if value in (None, ""):
            return
        value = str(value)
        if name in fields and fields[name] != value:
            # 出处分级：高者优先；冲突如实记录（case3 的伴随表 vs 文件名）
            old_key = prov[name]
            if _PROV_ORDER.get(source_key, 0) > _PROV_ORDER.get(old_key, 0):
                conflicts.append({"field": name, "kept": value, "kept_provenance": source_label,
                                  "dropped": fields[name], "dropped_provenance": prov[name]})
                fields[name], prov[name] = value, source_label
            else:
                conflicts.append({"field": name, "kept": fields[name], "kept_provenance": prov[name],
                                  "dropped": value, "dropped_provenance": source_label})
            return
        fields[name] = value
        prov[name] = source_label

    stem = os.path.splitext(entry["name"])[0]
    for fname, spec in patterns.items():
        pat = spec.get("regex") if isinstance(spec, dict) else str(spec)
        if not pat:
            continue
        m = re.search(pat, entry["rel_path"])
        if m:
            put(fname, m.group(1) if m.groups() else m.group(0), PROV_LABEL["filename"], "filename")
    if "project" not in fields and entry["dir"]:
        put("project", entry["dir"].split("/")[0], PROV_LABEL["directory"], "directory")
    ext_kind = fd.get("ext_kinds", {}).get(entry["ext"], "")
    if ext_kind:
        put("kind", ext_kind, PROV_LABEL["extension"], "extension")
    if entry["text"].strip() and entry["ext"] not in (".csv", ".tsv", ".json", ".log", ".html", ".htm"):
        first = first_title_line(entry["text"])
        if first:
            put("title", first, PROV_LABEL["content"], "content")
    # 伴随表（CSV/JSON，库内或 case 引用）
    for row in wo["companion"]:
        if row.get("_load_error"):
            continue
        target = str(first_present(row, "path", "file", "name", "_target", default="") or "")
        if target and (norm(target) == norm(entry["rel_path"]) or norm(target) == norm(entry["name"])):
            for k, v in row.items():
                if k.startswith("_") or k in ("path", "file", "name"):
                    continue
                put(k, v, row["_provenance"], "companion")
    # 人工给定（manifest_supplement）：最高优先级，逐字段覆盖
    for row in wo["supplement"]:
        target = row.get("_target", "")
        if target and (norm(target) == norm(entry["rel_path"]) or norm(target) == norm(entry["name"])):
            for k, v in row.items():
                if k.startswith("_"):
                    continue
                put(k, v, row["_provenance"], "manual")
    for name in (fd.get("required_fields") or []):
        if name not in fields:
            prov[name] = PROV_LABEL["absent"]
    fields["seq"] = fields.get("seq") or ""
    known = [k for k, v in prov.items() if v != PROV_LABEL["absent"]]
    return {"fields": fields, "provenance": prov, "conflicts": conflicts,
            "fill_rate": round(len(known) / max(1, len(prov)), 4)}


# --------------------------------------------------------------------------------------
# 阶段 7/8：落库计划与执行（只向 --out 新建复制，绝不改写输入库）
# --------------------------------------------------------------------------------------
def plan_targets(entries: list, metas: dict, wo: dict, canonical_of: dict) -> tuple:
    """先给规范件分序号，别名副本沿用规范件序号加 -aliasN，避免同一内容伪装成两次实验。"""
    rows, seq_of_dir = [], {}
    target_of_canonical = {}
    canon = [e for e in entries if canonical_of.get(e["sha256"]) == e["rel_path"]]
    alias = [e for e in entries if canonical_of.get(e["sha256"]) != e["rel_path"]]

    def render(e):
        md = metas[e["rel_path"]]
        vals = dict(md["fields"])
        vals.setdefault("project", "UNASSIGNED")
        vals.setdefault("year", "UNDATED")
        vals.setdefault("kind", "other")
        vals["ext"] = e["ext"]
        try:
            rel_dir = wo["directory_template"].format(**vals)
        except (KeyError, IndexError) as exc:
            rel_dir = "unsorted"
            vals["template_error"] = str(exc)[:80]
        seq_of_dir[rel_dir] = seq_of_dir.get(rel_dir, 0) + 1
        vals["seq"] = vals.get("seq") or f"{seq_of_dir[rel_dir]:02d}"
        try:
            name = wo["naming_template"].format(**vals)
        except (KeyError, IndexError) as exc:
            name = e["name"]
            vals["template_error"] = str(exc)[:80]
        return f"{rel_dir}/{name}", vals

    for e in canon:
        target, vals = render(e)
        target_of_canonical[e["sha256"]] = target
        rows.append({"source": e["rel_path"], "target": f"library/{target}", "sha256": e["sha256"],
                     "role": "canonical",
                     "basis": f"目录模板 {wo['directory_template']} / 命名模板 {wo['naming_template']}"
                              f"（字段：project={vals.get('project')}, year={vals.get('year')}, kind={vals.get('kind')}, seq={vals.get('seq')}）",
                     "fields_used": {k: vals[k] for k in ("project", "year", "kind", "seq") if k in vals},
                     "template_error": vals.get("template_error", "")})
    alias_count: dict = {}
    for e in alias:
        base = target_of_canonical.get(e["sha256"], "")
        if base:
            dir_part, name = base.rsplit("/", 1)
            stem, ext = os.path.splitext(name)
            k = alias_count.get(base, 0) + 1
            alias_count[base] = k
            target = f"{dir_part}/{stem}-alias{k}{ext}"
            basis = f"完全重复副本（同 SHA-256）：沿用规范件 {canonical_of.get(e['sha256'])} 的目标路径加 -alias{k}"
        else:
            target, _vals = render(e)
            basis = "副本的规范件不在本批计划中，按模板独立渲染"
        md = metas[e["rel_path"]]
        rows.append({"source": e["rel_path"], "target": f"library/{target}", "sha256": e["sha256"],
                     "role": "alias", "basis": basis,
                     "canonical_source": canonical_of.get(e["sha256"], ""),
                     "fields_used": {k: md["fields"][k] for k in ("project", "year", "kind") if k in md["fields"]},
                     "template_error": ""})
    # 同名不同内容（不同来源目录）显式登记
    name_groups: dict = {}
    for e in entries:
        name_groups.setdefault(e["name"], []).append(e)
    name_collisions = [{"name": n, "paths": [x["rel_path"] for x in items],
                        "same_content": len({x["sha256"] for x in items}) == 1,
                        "note": "同名文件：同内容已按哈希归别名；不同内容各自入库（不覆盖），需人工确认命名"}
                       for n, items in sorted(name_groups.items()) if len(items) > 1]
    return rows, name_collisions


def apply_plan(wo: dict, entries: list, plan: list) -> tuple:
    root = Path(wo["root"])
    dest_root = Path(wo["apply_root"])
    by_src = {e["rel_path"]: e for e in entries}
    ops, conflicts = [], []
    for row in plan:
        e = by_src[row["source"]]
        dest = dest_root / row["target"]
        if dest.exists():
            if sha256_file(dest) == e["sha256"]:
                ops.append({"op": "noop_already_present", "from": row["source"], "to": row["target"],
                            "sha256": e["sha256"], "bytes": e["bytes"], "verified": True})
                continue
            conflicts.append({"path": row["target"], "code": "W_TARGET_EXISTS",
                              "reason": "目标已存在且内容不同，未覆盖（覆盖会切断版本链），保留原文件并记录"})
            continue
        ensure_dir(dest.parent)
        try:
            shutil.copyfile(root / e["rel_path"], dest)
        except OSError as exc:
            conflicts.append({"path": row["target"], "code": "W_COPY_FAILED", "reason": str(exc)[:160]})
            continue
        got = sha256_file(dest)
        if got != e["sha256"]:
            conflicts.append({"path": row["target"], "code": "W_HASH_MISMATCH",
                              "reason": f"落库后哈希不符 src={e['sha256'][:12]} dst={got[:12]}，不视为入库成功"})
            continue
        ops.append({"op": "copy", "from": row["source"], "to": row["target"],
                    "sha256": e["sha256"], "bytes": e["bytes"], "verified": True})
    return ops, conflicts


# --------------------------------------------------------------------------------------
# 阶段 9：归档校验（源库前后哈希对照 + 落库树清单与核验）
# --------------------------------------------------------------------------------------
def snapshot_all_hashes(root: Path) -> dict:
    """对库内全部文件（含侧车与 0 字节件）取 SHA-256，用作「输入未动」证明。"""
    out = {}
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                out[str(p.relative_to(root))] = {"sha256": sha256_file(p), "bytes": p.stat().st_size}
            except OSError:
                out[str(p.relative_to(root))] = {"sha256": None, "bytes": None}
    return out


def verify_untouched(before: dict, root: Path, root_display: str = "") -> dict:
    changed, rechecked = [], 0
    for rel, info in before.items():
        p = Path(root) / rel
        rechecked += 1
        if not p.is_file() or sha256_file(p) != info["sha256"]:
            changed.append(rel)
    return {"root": root_display or str(root), "files": len(before), "rechecked": rechecked,
            "hashes_unchanged": not changed, "changed": changed,
            "input_modified": bool(changed),
            "policy": "输入库全程只读：本流程只向 --out 写落库树与清单，绝不改写输入库"}


def archive_manifest(wo: dict, ops: list) -> dict:
    """归档清单：落库树文件数、总字节、逐文件哈希 + 与入库核验结果对照。"""
    dest_root = Path(wo["apply_root"])
    files, total = [], 0
    if dest_root.is_dir():
        for p in sorted(dest_root.rglob("*")):
            if p.is_file():
                size = p.stat().st_size
                total += size
                files.append({"path": p.relative_to(dest_root).as_posix(),
                              "sha256": sha256_file(p), "bytes": size})
    expected = {op["to"]: op["sha256"] for op in ops}
    mismatches = [f["path"] for f in files if expected.get(f["path"]) and f["sha256"] != expected[f["path"]]]
    missing = [t for t in expected if t not in {f["path"] for f in files}]
    return {"tree": "out/library", "file_count": len(files), "total_bytes": total,
            "files": files, "hash_algorithm": "sha256",
            "verify": {"checked": len(files), "mismatch": mismatches, "missing": missing,
                       "ok": not mismatches and not missing}}


# --------------------------------------------------------------------------------------
# 阶段 10/11：索引与请求应答
# --------------------------------------------------------------------------------------
def build_index(entries: list, metas: dict, plan: list) -> list:
    target_of = {p["source"]: p["target"] for p in plan}
    index = []
    for e in entries:
        md = metas[e["rel_path"]]
        text = e["text"]
        index.append({
            "id": stable_id(e["rel_path"], e["sha256"], n=8),
            "path": target_of.get(e["rel_path"], e["rel_path"]),
            "source_path": e["rel_path"],
            "sha256": e["sha256"],
            "bytes": e["bytes"],
            "kind": md["fields"].get("kind", "other"),
            "project": md["fields"].get("project", ""),
            "year": md["fields"].get("year", ""),
            "title": md["fields"].get("title", ""),
            "terms": tokens(" ".join([e["name"], md["fields"].get("title", ""), text[:2000]]))[:80],
            "searchable": bool(text.strip()),
            "extraction_method": e["extraction_method"] or None,
        })
    return index


def answer_requests(wo: dict, index: list, chains: list, dups: list) -> list:
    out = []
    for q in wo["requests"]:
        if q["type"] in ("search", "query"):
            qs = set(tokens(q["text"]))
            scored = []
            for item in index:
                hit = len(qs & set(item["terms"]))
                if hit:
                    scored.append({"path": item["path"], "score": hit, "sha256": item["sha256"][:12],
                                   "searchable": item["searchable"]})
            scored.sort(key=lambda x: (-x["score"], x["path"]))
            out.append({"request": q["id"], "type": q["type"], "text": q["text"],
                        "hits": scored[:10], "hit_count": len(scored)})
        elif q["type"] in ("duplicate", "dedupe"):
            out.append({"request": q["id"], "type": q["type"], "text": q["text"],
                        "duplicate_groups": len(dups), "aliases": sum(len(d["aliases"]) for d in dups)})
        elif q["type"] in ("version", "history", "restore"):
            qs = set(tokens(q["text"]))
            matched = [c for c in chains if any(qs & set(tokens(x)) for x in c["supersedes"])]
            out.append({"request": q["id"], "type": q["type"], "text": q["text"],
                        "version_chain": matched,
                        "restore_hint": ("按 chain 顺序回取：latest/canonical 为当前有效版本，回退时复制上一 order 的文件并保留原 sha256"
                                         if matched else "未匹配到版本链")})
        else:
            out.append({"request": q["id"], "type": q["type"], "text": q["text"],
                        "note": "未登记的请求类型，未处理（不猜意图）"})
    return out


# --------------------------------------------------------------------------------------
# 阶段 12：ops_log（全部建议、未执行、输入只读）
# --------------------------------------------------------------------------------------
def build_ops_log(wo: dict, entries: list, plan: list, dup_groups: list, chains: list,
                  near_dups: list, gaps: list) -> list:
    ops = []
    for row in plan:
        ops.append({"op": "ingest_suggestion", "targets": [row["source"]],
                    "basis": row["basis"] + (f"；模板错误：{row['template_error']}" if row.get("template_error") else ""),
                    "action": f"建议入库到 out/{row['target']}（本流程已复制并哈希核验）；源文件保持原位只读"})
    for g in dup_groups:
        ops.append({"op": "merge_duplicate_candidates", "targets": [g["canonical"]] + g["aliases"],
                    "basis": f"SHA-256 完全一致（{g['sha256'][:16]}…）：内容相同、仅文件名不同",
                    "action": f"建议保留规范名 {g['canonical']}，其余移入重复归档目录；本流程未执行任何改名/删除（输入只读）"})
    for nd in near_dups:
        ops.append({"op": "review_near_duplicate", "targets": nd["members"],
                    "basis": f"同归一化身份（{nd['identity']}）但内容哈希不同",
                    "action": "建议人工确认是否同一文献的不同修订；未执行合并"})
    for c in chains:
        ops.append({"op": "consolidate_versions", "targets": c["supersedes"],
                    "basis": f"版本组 {c['identity']}（来源={c['version_note_source']}）；"
                              f"canonical={c['canonical']['version']}（{c['canonical']['path']}）",
                    "action": "建议旧版只读归档、引用统一指向 canonical；本流程未执行归并"})
    for g in gaps:
        ops.append({"op": "repair_file_gap", "targets": [g["rel_path"]],
                    "basis": g["extraction_gap"],
                    "action": f"建议人工修复（代码 {g['extraction_gap_code']}：加密件需口令、损坏件需换源、"
                              f"无文本层扫描件需补 OCR 侧车）；文本提取已诚实跳过，未伪造内容"})
    ops.append({"op": "rebuild_index", "targets": ["out/library"],
                "basis": "哈希清单/别名归一/版本组已重建",
                "action": "建议检索索引以 canonical 文件为键重建；索引已写入本结果（不额外改文件）"})
    ops.append({"op": "rollback_ready", "targets": ["out/library"],
                "basis": "本轮全部操作为新建复制，源库零改动（见 integrity）",
                "action": "回滚 = 删除 out/library 整树；无需恢复源文件"})
    for op in ops:
        op["input_readonly"] = True
        op["executed"] = False
        op["scope"] = "suggestion_only"
    return ops


# --------------------------------------------------------------------------------------
# 阶段 13：报告组装
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    res = Result(case, skill=SKILL, clock=Clock(args.budget))
    res.log("load_case", case_id=res.case_id, contract=sorted(res.contract))
    case_path = Path(args.case)
    wo = build_workorder(case, task_text, rules, case_path)
    wo["apply_root"] = str(ensure_dir(Path(args.out) / "library"))
    res.log("workorder", root=Path(wo["root"]).name, root_tried=len(wo["root_tried"]),
            exclude=len(wo["exclude"]), requests=len(wo["requests"]),
            companion_rows=len(wo["companion"]), version_notes=len(wo["version_notes"]))

    # 源库只读证明：处理前全量哈希快照（含侧车与 0 字节件）
    hash_before = snapshot_all_hashes(Path(wo["root"]))

    entries, skipped, sidecars = inventory(wo)
    res.log("inventory", files=len(entries), skipped=len(skipped), sidecars=len(sidecars))

    if not entries:
        payload = {
            "主结果": {"status": "empty", "files": [], "reason": "资料库无可入库文件（空目录/全部被跳过）"},
            "证据": {"root": Path(wo["root"]).name, "root_tried": wo["root_tried"],
                    "skipped": skipped, "sidecars": sidecars,
                    "integrity": verify_untouched(hash_before, Path(wo["root"]), wo["root_display"])},
            "失败与恢复": {"overall": "empty", "skipped": skipped,
                          "recovery": "确认 library 路径与 exclude 规则后重跑；本轮未改动任何源文件"},
        }
        for k, v in payload.items():
            res.set(k, v)
        res.unmet("资料库文件", "未盘点入库文件")
        res.status["overall"] = "empty"
        res.bind({"主结果": payload["主结果"], "证据": payload["证据"], "失败与恢复": payload["失败与恢复"]}, ALIASES)
        return res

    extract_texts(wo, entries, sidecars)
    gaps = [e for e in entries if e["extraction_gap_code"]]
    res.log("extract", extracted=sum(1 for e in entries if e["extraction_method"]),
            gaps=len(gaps), methods=sorted({e["extraction_method"] for e in entries if e["extraction_method"]}))

    dup_groups, canonical_of = dedupe(entries, rules)
    res.log("dedupe", groups=len(dup_groups), aliases=sum(len(d["aliases"]) for d in dup_groups))
    chains, version_conflicts, near_dups, notes_unmet = build_versions(entries, rules, wo["version_notes"], canonical_of)
    res.log("versions", chains=len(chains), conflicts=len(version_conflicts), near_dups=len(near_dups))
    metas = {e["rel_path"]: metadata_of(e, wo, rules) for e in entries}
    # 别名继承：同 SHA-256 副本的「未知」字段继承规范件值，出处记「别名继承[规范件]」
    for e in entries:
        canon = canonical_of.get(e["sha256"])
        if not canon or canon == e["rel_path"]:
            continue
        am, cm = metas[e["rel_path"]], metas[canon]
        for fname, value in cm["fields"].items():
            if fname == "seq":
                continue
            if am["provenance"].get(fname, PROV_LABEL["absent"]) == PROV_LABEL["absent"]:
                am["fields"][fname] = value
                am["provenance"][fname] = f"别名继承[{canon}]"
        am["fill_rate"] = round(
            len([k for k, v in am["provenance"].items() if v != PROV_LABEL["absent"]]) / max(1, len(am["provenance"])), 4)
    field_conflicts = {p: m["conflicts"] for p, m in metas.items() if m["conflicts"]}
    res.log("metadata", files=len(metas),
            avg_fill=round(sum(m["fill_rate"] for m in metas.values()) / max(1, len(metas)), 4),
            field_conflicts=sum(len(v) for v in field_conflicts.values()))
    plan, name_collisions = plan_targets(entries, metas, wo, canonical_of)
    res.log("plan", targets=len(plan), name_collisions=len(name_collisions))
    ops, conflicts = apply_plan(wo, entries, plan)
    res.log("apply", copied=sum(1 for o in ops if o["op"] == "copy"),
            noop=sum(1 for o in ops if o["op"] == "noop_already_present"), conflicts=len(conflicts))
    integrity = verify_untouched(hash_before, Path(wo["root"]), wo["root_display"])
    archive = archive_manifest(wo, ops)
    res.log("archive", files=archive["file_count"], total_bytes=archive["total_bytes"],
            hashes_unchanged=integrity["hashes_unchanged"], verify_ok=archive["verify"]["ok"])
    index = build_index(entries, metas, plan)
    res.log("index", entries=len(index), searchable=sum(1 for i in index if i["searchable"]))
    answers = answer_requests(wo, index, chains, dup_groups)
    res.log("requests", count=len(answers))
    ops_log = build_ops_log(wo, entries, plan, dup_groups, chains, near_dups, gaps)
    res.log("ops", entries=len(ops_log))

    # 目录树（落库后）
    tree: dict = {}
    for op in ops:
        parts = op["to"].split("/")
        cur = tree
        for seg in parts[:-1]:
            cur = cur.setdefault(seg, {})
        cur.setdefault("__files__", []).append(parts[-1])

    def render_tree(node, prefix=""):
        lines = []
        for key in sorted(k for k in node if k != "__files__"):
            lines.append(f"{prefix}{key}/")
            lines.extend(render_tree(node[key], prefix + "  "))
        for f in sorted(node.get("__files__", [])):
            lines.append(f"{prefix}{f}")
        return lines

    manifest = []
    for e in entries:
        md = metas[e["rel_path"]]
        target = next((p["target"] for p in plan if p["source"] == e["rel_path"]), "")
        manifest.append({
            "source_path": e["rel_path"], "target_path": target, "bytes": e["bytes"],
            "sha256": e["sha256"], "mode": e["mode"], "mtime": e["mtime"],
            "ext": e["ext"], "decodable_utf8": e["decodable_utf8"],
            "extraction_method": e["extraction_method"] or None,
            "extraction_gap_code": e["extraction_gap_code"] or None,
            "duplicate_of": canonical_of.get(e["sha256"]) if canonical_of.get(e["sha256"]) != e["rel_path"] else "",
            "metadata": md["fields"], "field_provenance": md["provenance"], "fill_rate": md["fill_rate"],
            "in_library": any(op["to"] == target for op in ops),
        })

    thr = float(rules.get("field_dictionary", {}).get("fill_rate_threshold", 0.75))
    low_fill = [{"path": m["source_path"], "fill_rate": m["fill_rate"],
                 "absent_fields": [k for k, v in m["field_provenance"].items() if v == "未知"]}
                for m in manifest if m["fill_rate"] < thr]
    issues = []
    for c in conflicts:
        issues.append({"code": c["code"], "item": c["path"], "detail": c["reason"]})
    for c in version_conflicts:
        issues.append({"code": c["code"], "item": c.get("path", ""), "detail": c["reason"]})
    for s in skipped:
        issues.append({"code": s["code"], "item": s["path"], "detail": s["reason"]})
    for g in gaps:
        issues.append({"code": g["extraction_gap_code"], "item": g["rel_path"], "detail": g["extraction_gap"]})
    for s in notes_unmet:
        issues.append({"code": s["code"], "item": s["path"], "detail": s["reason"]})
    non_utf8 = [e["rel_path"] for e in entries if not e["decodable_utf8"] and not e["extraction_method"]
                and not e["extraction_gap_code"]]
    if non_utf8:
        issues.append({"code": "W_NON_UTF8", "item": ",".join(non_utf8),
                       "detail": "非 UTF-8 文件已入库并按字节哈希核验，但内容未能抽取（标题/正文元数据缺失）"})
    missing_meta = [m["source_path"] for m in manifest if not m["metadata"].get("project")]
    if missing_meta:
        issues.append({"code": "W_METADATA_MISSING", "item": ",".join(missing_meta[:6]),
                       "detail": "project 字段多路抽取均失败，已记「未知」，未按文件名猜"})
    if field_conflicts:
        issues.append({"code": "W_FIELD_CONFLICT", "item": f"{sum(len(v) for v in field_conflicts.values())} 处",
                       "detail": "伴随表/人工给定与文件名解析等来源冲突：按出处分级取舍，明细见 操作证据.field_conflicts"})
    for nc in name_collisions:
        if not nc["same_content"]:
            issues.append({"code": "W_NAME_COLLISION", "item": nc["name"],
                           "detail": f"同名不同内容：{nc['paths']} 各自入库不覆盖，需人工确认命名"})

    overall = "complete" if not issues else "partial"
    for it in issues[:16]:
        res.unmet(f"{it['code']}:{it['item']}", it["detail"])
    for q in answers:
        if q["type"] not in ("search", "query", "duplicate", "dedupe", "version", "history", "restore"):
            res.unsure(f"请求 {q['request']}", q.get("note", "未处理"))

    recovery = {
        "rollback": {
            "scope": "本轮只在 out/library 下新建文件，源库零改动（integrity.hashes_unchanged 为证）",
            "steps": ["确认没有其他进程在使用 out/library",
                      "删除 out/library 整棵新建树即可完全回滚（无覆盖、无移动，无需恢复源文件）",
                      "如需逐条回滚：按 journal 中的 op=copy 记录，仅删除该 to 路径"],
            "verification": "回滚后对 library.root 全量重算 sha256，应与本结果 操作证据.hashes 逐条一致",
        },
        "restore_points": [{"path": c["latest"], "sha256": c["chain"][-1]["sha256"],
                            "previous": c["chain"][-2]["path"] if c["count"] > 1 else ""} for c in chains],
        "skipped": skipped,
        "conflicts": conflicts + version_conflicts,
        "reproduce": {
            "command": "python3 -B scripts/pipeline.py --case <case.json> --out <out_dir>",
            "script_version": SCRIPT_VERSION,
            "rules": ["rules/dedupe.json", "rules/field_dictionary.json", "rules/convention.json"],
            "determinism": "遍历排序、规范件选择、目标路径生成均为确定性规则；同一输入产出同一清单",
        },
    }

    main_payload = {
        "status": "ok" if overall == "complete" else "partial",
        "library_mode": {
            "root": wo["root_display"], "root_tried": wo["root_tried"],
            "documents": len(entries), "sidecars_excluded": len(sidecars),
            "sidecars": sidecars,
        },
        "directory_tree": render_tree(tree),
        "files": manifest,
        "counts": {"source_files": len(entries), "in_library": len(ops),
                   "already_present": sum(1 for o in ops if o["op"] == "noop_already_present"),
                   "skipped": len(skipped), "sidecars": len(sidecars),
                   "duplicate_groups": len(dup_groups), "version_chains": len(chains),
                   "extraction_gaps": len(gaps), "conflicts": len(conflicts) + len(version_conflicts)},
        "duplicate_groups": dup_groups,
        "near_duplicate_hints": near_dups,
        "version_chains": chains,
        "naming_plan": plan,
        "index": index,
        "requests": answers,
        "convention": {"directory_template": wo["directory_template"],
                       "naming_template": wo["naming_template"], "exclude": wo["exclude"]},
    }
    evidence = {
        "source_root": Path(wo["root"]).name,
        "hashes": [{"path": m["source_path"], "sha256": m["sha256"], "bytes": m["bytes"],
                    "mode": m["mode"], "mtime": m["mtime"]} for m in manifest],
        "journal": ops,
        "journal_count": len(ops),
        "metadata_provenance": {m["source_path"]: m["field_provenance"] for m in manifest},
        "field_conflicts": field_conflicts,
        "canonical_rule": rules.get("dedupe", {}).get("canonical_rule", ""),
        "extraction": [{"path": e["rel_path"], "method": e["extraction_method"] or None,
                        "n_chars": len(e["text"]), "gap_code": e["extraction_gap_code"] or None,
                        "gap": e["extraction_gap"] or None,
                        "ocr_confidence": e["ocr_confidence"]} for e in entries],
        "integrity": integrity,
        "archive_manifest": archive,
        "ops_log": {"entries": ops_log, "executed": False,
                    "note": "全部为建议（scope=suggestion_only，input_readonly=true）：未执行改名/合并/删除，输入库前后哈希对照见 integrity"},
        "version_notes_source": ", ".join(VERSION_NOTE_KEYS),
        "script_version": SCRIPT_VERSION,
        "operation_log": res.run_log,
    }
    failure = {
        "overall": overall,
        "skipped": skipped,
        "conflicts": conflicts + version_conflicts,
        "issues": issues,
        "version_note_gaps": notes_unmet,
        "low_fill_rate_files": low_fill,
        "recovery": recovery,
    }
    res.set("主结果", main_payload)
    res.set("证据", evidence)
    res.set("失败与恢复", failure)
    res.set("main_result", main_payload)
    res.bind({"主结果": main_payload, "证据": evidence, "失败与恢复": failure,
              "资料结果": main_payload, "操作证据": evidence}, ALIASES)
    res.log("report", status=overall, issues=len(issues))
    res.done("inventory", "extract", "dedupe", "version_chain", "metadata", "plan", "apply",
             "archive_verify", "index", "ops_log", "recovery_plan")
    res.status["overall"] = overall
    res.note(f"源库只读（integrity.hashes_unchanged={integrity['hashes_unchanged']}）；"
             f"本轮新建/确认 {len(ops)} 个落库文件、{len(skipped)} 个跳过、{len(conflicts)} 个落库冲突、"
             f"{len(version_conflicts)} 个版本冲突、{len(gaps)} 个提取缺口，全部入清单")
    return res


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.self_check:
        return pdf_min.self_check()
    try:
        case = load_case(args.case)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    res = run(case, load_task_text(args, case), args)
    payload = res.build()
    if args.dry_run:
        print(json.dumps({"case_id": payload["case_id"], "status": payload["status"]["overall"]}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"],
                      "files": payload.get("主结果", {}).get("counts", {}).get("source_files")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
