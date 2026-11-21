#!/usr/bin/env python3
"""056 论文逻辑与论证审查 —— 主流程（全文审查版 v2）。

输入：/input/case.json（论文全文或材料路径）+ task.md
输出：/app/output/result.json（审查范围 / 提取与覆盖率 / 主张清单 / 论证图 / 跨章节矩阵 /
      分型发现 / 抑制列表 / 句级索引 / 修改计划 / 证据 / 状态 / 不确定性）

与短节选版的关键差异（真实案例画像：完整论文全文 + 分维度工单 + 分型发现）：
  1. 全文获取链：内联文本 → 侧车 .txt/.ocr.txt → 自研 stdlib PDF 提取（zlib BT/ET Tj/TJ）
     → DOCX zip+xml → 诚实 gap；记录 extraction_method + SHA-256 + 总字符数 + 覆盖率；
  2. 章节自动切分（中英文标题词典 + 编号/罗马/汉字序号）+ 句级索引（sent_id、章节、
     段内序号、char_span），每条 finding 定位到句并带原文子串校验；
  3. facets 动态绑定：任务指令点名哪些审查维度就执行哪些（rules/facets.json），未点名维度
     在 facets_status 里标 unchecked，不冒充全查；
  4. 分型发现：id + 类型体系（外置 rules，8 大类）+ severity（附规则化判据字段）+ location
     （章节/段落/句 id/char_span）+ quote（管线内做精确子串校验）+ evidence（判断依据）+
     revision（句级可执行建议：改哪句/补什么证据/把结论降到什么强度）；
  5. 跨章节检查：引言承诺→结果交付对照、方法与结果指标口径、摘要与结论强度比对、
     图表引用存在性；
  6. 抑制系统化：有意对照 / 显式假设 / 转述他人 / 局限性自述命中白名单 → 进 suppressed
     列表（含命中原因与原文引用），不出现在 findings；
  7. 截断诚实：报告 coverage；截断或核心章节缺失时 status 不写 complete。

方法要点（论证图部分与 v1 一致，全部可复算）：
  - 主张来源三分类（作者主张 / 转述他人 / 假设情形）；
  - 边由 token 重合度阈值决定，只从证据章节指向解释章节（解释章节内部双向需更强重合）；
  - 断裂点：无支撑边、仅假设支撑、循环论证（DFS 找环）、存在攻击边但结论未处理。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
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
    parse_args,
    read_text,
    rounded,
    sha256_text,
    snippet,
    stable_id,
    tokens,
)
from pdf_min import extract_docx_text, extract_pdf_text  # noqa: E402

SKILL = "argument-and-logic-review"
RULES = Path(__file__).resolve().parent.parent / "rules"
BACKGROUND_MARKERS = ["受到广泛关注", "仍存争议", "尚不清楚", "仍有争议", "一直是", "广泛关注", "日益受到",
                      "remains unclear", "is debated", "has attracted", "is well documented", "of growing interest"]

# 章节标题词典（中英文）；level 只用于嵌套展示，角色判定看 section_role
HEADING_KEYS = (
    "abstract", "summary", "keywords", "key words", "objectives", "objective", "aims",
    "introduction", "background", "related work", "literature review", "preliminaries",
    "materials and methods", "methods", "method", "study design", "participants", "measures",
    "data collection", "statistical analysis", "statistical methods", "study population",
    "results", "findings", "discussion", "limitations", "strengths and limitations", "strengths",
    "conclusion", "conclusions", "conclusions and outlook", "implications", "clinical implications",
    "acknowledgements", "acknowledgments", "funding", "conflict of interest", "conflicts of interest",
    "data availability", "references", "bibliography", "supplementary", "supplementary material",
    "appendix", "author contributions", "ethical approval", "ethics statement", "abbreviations",
    "摘要", "关键词", "目的", "研究目的", "引言", "前言", "绪论", "背景", "相关工作", "文献综述",
    "材料与方法", "方法", "研究设计", "研究对象", "测量指标", "数据收集", "统计分析",
    "结果", "研究结果", "讨论", "局限性", "局限", "不足", "结论", "结论与展望", "启示",
    "致谢", "基金", "利益冲突", "数据可用性", "参考文献", "附录", "作者贡献", "伦理声明", "缩略词",
)
LIMITATION_HEADINGS = ("limitation", "局限", "不足", "self-critique", "caveat", "strengths and limitations")
REFERENCES_RE = re.compile(r"references|bibliography|参考文献", re.I)
ABBR_SUFFIX_RE = re.compile(r"(?:et al|i\.e|e\.g|vs|Fig|Eq|No|cf|Dr|Prof|approx|St)$")
CORE_SECTIONS = ("abstract", "introduction", "methods", "results", "discussion", "conclusion")
CORE_LEXICON = {
    "abstract": ("abstract", "summary", "摘要"),
    "introduction": ("introduction", "background", "前言", "引言", "绪论", "背景"),
    "methods": ("materials and methods", "methods", "method", "study design", "材料与方法", "方法", "研究设计"),
    "results": ("results", "findings", "结果", "研究结果"),
    "discussion": ("discussion", "讨论"),
    "conclusion": ("conclusion", "结论"),
}
TRUNCATION_RE = re.compile(
    r"truncat|abridged|omitted|excerpt|continues? in part|text cut|截断|节选|省略|未完|后续内容缺失|因长度限制",
    re.I,
)
SID_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def load_rules() -> dict:
    return {
        "arg": load_json(RULES / "argument_rules.json", default={}) or {},
        "severity": load_json(RULES / "severity.json", default={}) or {},
        "codes": (load_json(RULES / "reason_codes.json", default={}) or {}).get("codes", {}),
        "routing": load_json(RULES / "contract_routing.json", default={}) or {},
        "facets": load_json(RULES / "facets.json", default={}) or {},
    }


# --------------------------------------------------------------------------------------
# 线索词匹配：ASCII 词按词边界 + 常见屈折变体，CJK 按子串（避免 improve 误中 prove）
# --------------------------------------------------------------------------------------
def _cue_pattern(term: str):
    t = norm(term)
    if not t:
        return None
    if re.fullmatch(r"[a-z0-9 .\-']+", t):
        core = re.escape(t)
        return re.compile(r"\b" + core + r"(?:s|es|d|ed|ing)?\b")
    return re.compile(re.escape(t))


class Cues:
    """一组线索词的预编译匹配器。"""

    def __init__(self, terms):
        self.items = []
        for term in as_list(terms or []):
            pat = _cue_pattern(str(term))
            if pat is not None:
                self.items.append((norm(term), pat))

    def hits(self, text: str) -> list:
        low = norm(text)
        return [term for term, pat in self.items if pat.search(low)]


class CueBank:
    """从 rules 配置批量构建的线索词库。"""

    # 句内自述局限（即使不在 Limitations 章节也不按作者缺陷判）
    SENTENCE_LIMITATION = ["cannot rule out", "unable to rule out", "should be interpreted with caution",
                       "we acknowledge", "remains a limitation", "caveat", "remains to be tested",
                       "future studies should", "future work should", "无法排除", "不能排除",
                       "无法确定", "需要注意的是", "值得注意的是", "需谨慎解读", "作者自述局限",
                       "有待检验", "有待验证", "有待后续"]

    def __init__(self, cfg: dict):
        keys = ("claim_cues", "premise_cues", "assumption_cues", "reported_cues", "contrast_cues",
                "hedges", "strong_claims", "causal_terms", "correlational_terms", "aim_cues",
                "control_cues")
        for key in keys:
            setattr(self, key, Cues(cfg.get(key, [])))
        self.design_obs = Cues((cfg.get("design_markers") or {}).get("observational", []))
        self.design_causal = Cues((cfg.get("design_markers") or {}).get("causal", []))
        self.metrics = Cues(cfg.get("metric_terms", []))
        self.limitation_words = Cues(LIMITATION_HEADINGS)
        self.sent_limit = Cues(self.SENTENCE_LIMITATION)


# --------------------------------------------------------------------------------------
# 全文获取：内联 → 侧车 → 自研 PDF → DOCX → 诚实 gap
# --------------------------------------------------------------------------------------
INLINE_KEYS = ("manuscript", "paper", "text", "document", "content", "article")
PATH_KEYS = ("manuscript_path", "paper_path", "document_path", "text_path", "fulltext_path")


def _sidecar_candidates(pdf_path: Path) -> list:
    return [pdf_path.with_suffix(".txt"), pdf_path.with_suffix(".ocr.txt"),
            pdf_path.with_name(pdf_path.stem + "_text.txt"), pdf_path.with_suffix(".text")]


def _read_binary(path: Path) -> bytes:
    return path.read_bytes()


def _resolve_ref(ref: str, bases: list) -> tuple:
    """相对路径按多基目录解析（case 目录优先，其次 work 与 /input），返回 (解析结果, 尝试列表)。"""
    p = Path(ref)
    if p.is_absolute():
        return p, [p]
    tried = []
    seen = set()
    for base in bases:
        cand = base / p
        text = str(cand)
        if text in seen:
            continue
        seen.add(text)
        tried.append(cand)
        if cand.exists():
            return cand, tried
    return (tried[0] if tried else p), tried


def collect_manuscript(case: dict, work: Path, case_dir: Path | None = None) -> dict:
    """把稿件归一成统一结构；支持内联文本、文本文件、PDF（侧车→自研提取）、DOCX。

    返回 dict：text / source / extraction_method / source_sha256 / text_sha256 /
    materials_audit（每次尝试的诚实记录）/ unparsed / n_pages / text_layers。
    """
    bases = []
    for b in (case_dir, case_dir / "input" if case_dir else None, work, work / "input" if work else None, Path("/input")):
        if b is not None and b not in bases:
            bases.append(b)
    out = {"text": "", "source": "", "extraction_method": "", "source_sha256": "",
           "text_sha256": "", "n_pages": None, "text_layers": None,
           "materials_audit": [], "unparsed": [], "declared_total_chars": None}

    def finish(text: str, source: str, method: str, sha: str = "", n_pages=None, layers=None):
        out.update({"text": text, "source": source, "extraction_method": method,
                    "source_sha256": sha, "text_sha256": sha256_text(text),
                    "n_pages": n_pages, "text_layers": layers})
        declared = first_present(case, "original_total_chars", "declared_total_chars", "full_text_chars")
        if isinstance(declared, (int, float)) and declared > 0:
            out["declared_total_chars"] = int(declared)
        return out

    # 1) 内联文本（含 sections/paragraphs 结构）
    for key in INLINE_KEYS:
        val = first_present(case, key, default=None)
        if val is None:
            continue
        if isinstance(val, str) and val.strip():
            return finish(val, f"case.{key}", "inline_text")
        if isinstance(val, dict):
            for k in ("text", "content", "body", "full_text"):
                if isinstance(val.get(k), str) and val[k].strip():
                    return finish(val[k], f"case.{key}.{k}", "inline_text")
            paras = val.get("paragraphs") or val.get("lines")
            if isinstance(paras, list) and paras:
                return finish("\n\n".join(str(p) for p in paras), f"case.{key}.paragraphs", "inline_text")

    # 2) 文件引用
    cands = []
    for key in PATH_KEYS:
        v = first_present(case, key)
        if isinstance(v, str) and v.strip():
            cands.append((key, v, {}))
    for mat in as_list(first_present(case, "materials", "files", "attachments", default=[])):
        if isinstance(mat, dict):
            p = first_present(mat, "path", "file", "filepath", "location")
            if isinstance(p, str) and p.strip():
                cands.append(("materials", p, mat))
        elif isinstance(mat, str):
            cands.append(("materials", mat, {}))

    for origin, ref, meta in cands:
        p, tried = _resolve_ref(ref, bases)
        audit = {"origin": origin, "ref": ref, "path": str(p), "tried": [str(t) for t in tried]}
        out["materials_audit"].append(audit)
        if not p.exists():
            audit.update({"outcome": "missing", "reason": "文件不存在（尝试路径见 tried）"})
            out["unparsed"].append({"path": str(p), "reason": f"文件不存在；尝试过：{'；'.join(str(t) for t in tried)}"})
            continue
        suffix = p.suffix.lower()
        sha = hashlib.sha256(_read_binary(p)).hexdigest() if suffix in (".pdf", ".docx") else ""
        audit["sha256"] = sha
        if suffix in (".txt", ".md", ".tex", ".text", ".ocr.txt"):
            body = read_text(p)
            if body.strip():
                audit["outcome"] = "read"
                return finish(body, str(p), "text_file", n_pages=None, layers=True)
            audit.update({"outcome": "empty", "reason": "文本为空"})
            out["unparsed"].append({"path": str(p), "reason": "文本为空或编码不可读"})
            continue
        if suffix == ".pdf":
            # 2a) 平台侧车文本层优先（转写保真），缺失或为空才走自研提取
            sidecar_used = ""
            for sc in _sidecar_candidates(p):
                if sc.exists() and read_text(sc).strip():
                    sidecar_used = str(sc)
                    break
            if sidecar_used:
                audit.update({"outcome": "read", "sidecar": sidecar_used})
                return finish(read_text(Path(sidecar_used)), str(p), "sidecar_txt", sha=sha,
                              n_pages=None, layers=True)
            info = extract_pdf_text(_read_binary(p))
            audit.update({"outcome": "read" if info["text_layers"] else "no_text_layer",
                          "pdf_streams": info["streams_total"], "pdf_decompressed": info["streams_decompressed"],
                          "pdf_pages": info["n_pages"]})
            if info["text_layers"]:
                return finish(info["text"], str(p), "pdf_stdlib_flate", sha=info["sha256"],
                              n_pages=info["n_pages"], layers=True)
            out["text_layers"] = False
            out["n_pages"] = info["n_pages"]
            out["unparsed"].append({"path": str(p),
                                    "reason": "PDF 未提取到文本层（可能为扫描件/加密/非 Flate 压缩），需平台 pdf-text-tool 或 OCR"})
            continue
        if suffix == ".docx":
            info = extract_docx_text(_read_binary(p))
            audit["outcome"] = "read" if info["text_layers"] else "no_text_layer"
            if info["text_layers"]:
                return finish(info["text"], str(p), "docx_stdlib_xml", sha=info["sha256"], layers=True)
            out["unparsed"].append({"path": str(p), "reason": info.get("note") or "DOCX 无可提取文本"})
            continue
        if suffix == ".doc":
            audit.update({"outcome": "unsupported", "reason": "老版二进制 .doc 无标准库解析器，需先转 docx/txt"})
            out["unparsed"].append({"path": str(p), "reason": "老版二进制 .doc 不支持，请先转 docx/txt"})
            continue
        body = read_text(p)
        if body.strip():
            audit["outcome"] = "read"
            return finish(body, str(p), "text_file", layers=True)
        audit.update({"outcome": "empty", "reason": "文本为空"})
        out["unparsed"].append({"path": str(p), "reason": "文本为空或编码不可读"})
    return out


# --------------------------------------------------------------------------------------
# 章节切分 / 段落 / 句级索引（全部带 char_span，且文本切片可反查）
# --------------------------------------------------------------------------------------
def _is_heading(line: str) -> tuple:
    s = line.strip()
    if not s or len(s) > 95:
        return False, 0
    m = re.match(r"^(#{1,4})\s+\S", s)
    if m:
        return True, len(m.group(1))
    m = re.match(r"^(\d+(?:\.\d+){0,3})[\.\s、]\s*[A-Za-z\u4e00-\u9fff（(]", s)
    if m and len(s.split()) <= 14 and not s.rstrip().endswith((".", "。", ";", "；", ",", "，")):
        return True, m.group(1).count(".") + 1
    m = re.match(r"^([IVXivx]{1,5})[\.\)]\s+[A-Za-z]", s)
    if m and len(s.split()) <= 12:
        return True, 1
    m = re.match(r"^[一二三四五六七八九十]{1,3}[、\.]\s*\S", s)
    if m and len(s) <= 40:
        return True, 1
    m = re.match(r"^[（(][一二三四五六七八九十]{1,3}[)）]\s*\S", s)
    if m and len(s) <= 40:
        return True, 1
    low = s.lower().rstrip(":：.。 ")
    if re.fullmatch(r"[A-Z][A-Z \-&]{2,60}", s) and low in HEADING_KEYS:
        return True, 1
    # 词典标题必须是短行：换行后的正文行（如 "Background: Ambient noise ..."）不算标题
    if len(s) <= 60:
        for k in HEADING_KEYS:
            if low == k or low.startswith(k + " ") or low.startswith(k + ":") or low.startswith(k + "："):
                return True, 1
    return False, 0


def split_sections(text: str) -> list:
    lines = text.splitlines()
    if not lines:
        return []
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1
    sections, cur = [], {"heading": "(preamble)", "level": 0, "start": 0, "line_start": 1, "lines": []}
    for i, ln in enumerate(lines):
        ok, level = _is_heading(ln)
        if ok and (cur["lines"] or cur["heading"] != "(preamble)"):
            cur["end"] = offsets[i] - 1 if i > 0 else 0
            cur["line_end"] = i
            sections.append(cur)
            cur = {"heading": ln.strip(), "level": level, "start": offsets[i], "line_start": i + 1, "lines": []}
        elif ok and not cur["lines"] and cur["heading"] == "(preamble)":
            cur = {"heading": ln.strip(), "level": level, "start": offsets[i], "line_start": i + 1, "lines": []}
        else:
            cur["lines"].append(ln)
    cur["end"] = len(text)
    cur["line_end"] = len(lines)
    sections.append(cur)

    out = []
    for sec in sections:
        body = "\n".join(sec["lines"]).strip("\n")
        if not body.strip() and sec["heading"] == "(preamble)":
            continue
        out.append({
            "section_id": f"S{len(out) + 1}",
            "heading": sec["heading"],
            "level": sec["level"],
            "start": sec["start"],
            "end": sec["end"],
            "line_start": sec["line_start"],
            "line_end": sec["line_end"],
            "char_len": max(0, sec["end"] - sec["start"]),
            "text": body,
        })
    return out


def section_role(heading: str, cfg: dict) -> str:
    h = norm(heading)
    for role, keys in cfg.get("section_roles", {}).items():
        if any(norm(k) in h for k in keys):
            return role
    return "other"


def is_limitation_heading(heading: str, bank: CueBank) -> bool:
    return bool(bank.limitation_words.hits(heading))


_SENT_END = "。！？!?"


def iter_sentences(block: str, block_offset: int):
    """切句并给出 (sent, abs_start, abs_end)：保护小数点与 et al. 等缩写；句号必须后随空白/行尾/文本尾。"""
    n = len(block)
    start = 0
    i = 0
    while i < n:
        ch = block[i]
        if ch in _SENT_END or ch == ".":
            if ch == ".":
                nxt = block[i + 1] if i + 1 < n else "\n"
                if i > 0 and block[i - 1].isdigit() and nxt.isdigit():
                    i += 1
                    continue  # 小数点
                if nxt not in (" ", "\n", ""):
                    i += 1
                    continue  # 句中点号（缩写/URL 等）
                if ABBR_SUFFIX_RE.search(block[max(start, i - 12):i]):
                    i += 1
                    continue  # et al. / e.g. 等缩写
            sent = block[start:i + 1]
            yield sent, block_offset + start, block_offset + i + 1
            start = i + 1
        i += 1
    tail = block[start:]
    if tail.strip():
        yield tail, block_offset + start, block_offset + n


def build_paragraphs(sections: list, text: str) -> list:
    """段落 + 句级索引：每句带 sent_id、章节、段内序号、char_span（指向 doc.text 的绝对偏移）。"""
    paras, sentences = [], []
    for sec in sections:
        if REFERENCES_RE.search(sec["heading"]):
            continue
        blocks = re.split(r"\n\s*\n", sec["text"])
        pidx = 0
        cursor = sec["start"]
        for block in blocks:
            if not block.strip():
                cursor += len(block) + 2
                continue
            # 定位块在全文中的真实偏移（考虑分隔符长度差异，逐块向前查找）
            pos = text.find(block, max(0, cursor - 2))
            pos = pos if pos >= 0 else cursor
            cursor = pos + len(block)
            if len(block.strip()) < 8:
                continue
            pidx += 1
            chunked = False
            # 超长块（无空行的连续文本）按 ~1200 字在句边界拆成虚拟段，保持定位精度
            raw_pieces = []
            if len(block) > 1600:
                chunked = True
                step, buf_start = 1200, 0
                while buf_start < len(block):
                    end = min(len(block), buf_start + step)
                    if end < len(block):
                        m = None
                        for m2 in re.finditer(r"[。.!?]", block[buf_start:end]):
                            m = m2
                        if m:
                            end = buf_start + m.end()
                    raw_pieces.append((block[buf_start:end], buf_start))
                    buf_start = end
            else:
                raw_pieces = [(block, 0)]
            for piece, poff in raw_pieces:
                lead = len(piece) - len(piece.lstrip("\n "))
                core = piece.strip("\n ")
                if len(core) < 8:
                    continue
                base = pos + poff + lead
                para = {
                    "section_id": sec["section_id"],
                    "heading": sec["heading"],
                    "pidx": pidx,
                    "char_span": [base, base + len(core)],
                    "text": core,
                    "chunked": chunked,
                }
                paras.append(para)
                for sent, s0, s1 in iter_sentences(core, base):
                    st = sent.strip()
                    if len(st) < 10:
                        continue
                    sent_lead = len(sent) - len(sent.lstrip())
                    sentences.append({
                        "sent_id": f"s{len(sentences) + 1:04d}",
                        "section_id": sec["section_id"],
                        "heading": sec["heading"],
                        "para_idx": pidx,
                        "char_span": [s0 + sent_lead, s1],
                        "text": st,
                    })
    return paras, sentences


def collect_manuscript_doc(case: dict, work: Path, cfg: dict, bank: CueBank, case_dir: Path | None = None) -> dict:
    got = collect_manuscript(case, work, case_dir)
    text = got["text"]
    sections = []
    inline_sections = None
    for key in INLINE_KEYS:
        val = first_present(case, key, default=None)
        if isinstance(val, dict) and isinstance(val.get("sections"), list):
            inline_sections = val["sections"]
            break
    if inline_sections and not text:
        for i, s in enumerate(inline_sections):
            if isinstance(s, dict):
                heading = str(first_present(s, "heading", "title", "section", default=f"Section {i + 1}"))
                body = str(first_present(s, "text", "content", "body", default=""))
            else:
                heading, body = f"Section {i + 1}", str(s)
            sections.append({"section_id": f"S{i + 1}", "heading": heading, "level": 1, "start": 0, "end": 0,
                             "line_start": 1, "line_end": 1, "char_len": len(body), "text": body})
        text = "\n\n".join(s["text"] for s in sections)
        got["text"] = text
        got["text_sha256"] = sha256_text(text)
    if not sections:
        sections = split_sections(text)
    meta_res = [norm(k) for k in cfg.get("meta_sections", [])]
    keywords_res = ("keywords", "key words", "关键词")
    last_role = ""
    numbered = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]{1,3}|[IVXivx]{1,5})[\s\.、\)）]")
    for sec in sections:
        if sec["heading"] == "(preamble)":
            sec["role"] = "other"
        elif any(m in norm(sec["heading"]) for m in meta_res):
            sec["role"] = "meta"
        else:
            r = section_role(sec["heading"], cfg)
            head_low = norm(sec["heading"])
            is_keywords = any(head_low.startswith(kw) for kw in keywords_res)
            if r == "other" and last_role and not is_keywords and (numbered.match(sec["heading"]) or len(sec["heading"]) <= 60):
                r = last_role  # 编号/无名子章节从最近非 meta 章节继承角色；关键词行不继承
            sec["role"] = r
        if sec["role"] not in ("other", "meta"):
            last_role = sec["role"]
        sec["limitation"] = is_limitation_heading(sec["heading"], bank)
    paras, sentences = build_paragraphs(sections, text)
    for sent in sentences:
        sec = next((s for s in sections if s["section_id"] == sent["section_id"]), {})
        sent["section_role"] = sec.get("role", "other")
        sent["limitation"] = bool(sec.get("limitation"))
        sent["locator"] = (f"{_doc_id(got)}/{sent['section_id']}¶{sent['para_idx']}"
                           f"#{sent['sent_id']}@{sent['char_span'][0]}-{sent['char_span'][1]}")
    return {"got": got, "text": text, "sections": sections, "paragraphs": paras, "sentences": sentences}


def _doc_id(got: dict) -> str:
    return "MS-" + stable_id(got.get("source") or "inline", got.get("text_sha256") or sha256_text(got.get("text", "")), n=6)


def parse_references(case: dict, sections: list) -> list:
    refs = []
    raw = first_present(case, "references", "bibliography", "reference_list", "citations", default=None)
    for i, r in enumerate(as_list(raw)):
        if isinstance(r, dict):
            refs.append({"ref_index": i + 1,
                         "ref_id": str(first_present(r, "id", "ref_id", "key", default=f"REF{i + 1}")),
                         "raw": str(first_present(r, "raw", "text", "title", default=""))})
        elif isinstance(r, str) and r.strip():
            refs.append({"ref_index": i + 1, "ref_id": f"REF{i + 1}", "raw": r.strip()})
    if not refs:
        for sec in sections:
            if not REFERENCES_RE.search(sec["heading"]):
                continue
            for line in sec["text"].splitlines():
                s = line.strip()
                if len(s) < 12:
                    continue
                refs.append({"ref_index": len(refs) + 1, "ref_id": f"REF{len(refs) + 1}", "raw": s})
    return refs


# --------------------------------------------------------------------------------------
# 提取覆盖率 / 截断诚实
# --------------------------------------------------------------------------------------
def build_coverage(doc: dict, case: dict) -> dict:
    got = doc["got"]
    text = doc["text"]
    headings_norm = [norm(s["heading"]) for s in doc["sections"]]
    present, missing = {}, []
    for core, lex in CORE_LEXICON.items():
        hit = any(any(norm(k) in h for k in lex) for h in headings_norm)
        present[core] = hit
        if not hit:
            missing.append(core)
    flags = []
    sources = []
    for key in ("truncated", "text_truncated", "partial_text", "is_excerpt"):
        if case.get(key) is True:
            sources.append(f"case.{key}=true")
    for audit in got["materials_audit"]:
        if isinstance(audit, dict) and (audit.get("truncated") or audit.get("excerpt")):
            sources.append(f"materials[{audit.get('ref')}].truncated")
    marker_hits = []
    if text:
        for m in TRUNCATION_RE.finditer(text):
            ctx = text[max(0, m.start() - 40):m.end() + 60].replace("\n", " ")
            marker_hits.append(snippet(ctx, 0, 100))
        if marker_hits:
            sources.append("text_marker")
    declared = got.get("declared_total_chars")
    char_coverage = None
    if declared:
        char_coverage = rounded(min(1.0, len(text) / declared), 4)
    # 结构怀疑：引言与方法在、结果不在 → 很可能截断
    structural_suspicion = present.get("introduction") and present.get("methods") and not present.get("results")
    truncated = bool(sources) or structural_suspicion
    return {
        "extraction_method": got.get("extraction_method"),
        "source": got.get("source"),
        "source_sha256": got.get("source_sha256"),
        "text_sha256": got.get("text_sha256"),
        "total_chars": len(text),
        "declared_total_chars": declared,
        "char_coverage": char_coverage,
        "sentence_count": len(doc["sentences"]),
        "section_count": len(doc["sections"]),
        "sections_present": present,
        "core_sections_missing": missing,
        "truncated": truncated,
        "truncation_evidence": sources,
        "truncation_marker_examples": marker_hits[:3],
        "unparsed_materials": got.get("unparsed", []),
        "provenance": first_present(case, "provenance", "source_note", default=None),
        "note": "全文口径：truncated 或核心章节缺失时 status 不写 complete；char_coverage 只在平台声明总字符数时给出",
    }


# --------------------------------------------------------------------------------------
# facets 动态绑定
# --------------------------------------------------------------------------------------
def select_facets(case: dict, task_text: str, facets_cfg: dict) -> dict:
    """决定执行哪些审查维度：case.review_facets > 任务指令线索 > 默认全量。"""
    declared = first_present(case, "review_facets", "facets", "review_dimensions", default=None)
    order = list(facets_cfg.get("facets", {}).keys())
    if isinstance(declared, dict):
        declared = [k for k, v in declared.items() if v]
    if declared:
        wanted = []
        for raw in as_list(declared):
            f = norm(raw)
            for key in order:
                if f and (f == norm(key) or norm(facets_cfg["facets"][key]["label"]) in f or f in norm(key)):
                    if key not in wanted:
                        wanted.append(key)
        unknown = [str(raw) for raw in as_list(declared)
                   if norm(raw) and not any(norm(raw) == norm(k) or norm(facets_cfg["facets"][k]["label"]) in norm(raw) or norm(raw) in norm(k) for k in order)]
        return {"requested": wanted, "mode": "case_declared", "unknown_requests": unknown, "matched_by": "case.review_facets"}
    low = norm(task_text)
    all_markers = [norm(m) for m in facets_cfg.get("all_markers", [])]
    if any(m in low for m in all_markers):
        return {"requested": list(order), "mode": "explicit_all", "unknown_requests": [], "matched_by": "task_all_marker"}
    wanted = []
    matched = {}
    for key in order:
        cues = [norm(c) for c in facets_cfg["facets"][key].get("task_cues", [])]
        hit = [c for c in cues if c in low]
        if hit:
            wanted.append(key)
            matched[key] = hit[:6]
    if wanted:
        return {"requested": wanted, "mode": "task_cues", "unknown_requests": [], "matched_by": matched}
    return {"requested": list(order), "mode": "default_all", "unknown_requests": [], "matched_by": "任务未点名任何维度，默认全量并如实记录"}


# --------------------------------------------------------------------------------------
# 主张抽取 + 来源三分类 + 论证图（与 v1 同口径）
# --------------------------------------------------------------------------------------
def classify_source(sent: str, bank: CueBank) -> dict:
    asm = bank.assumption_cues.hits(sent)
    rep = bank.reported_cues.hits(sent)
    if asm:
        return {"source_type": "assumption", "cues": asm}
    if rep:
        return {"source_type": "reported", "cues": rep}
    return {"source_type": "author", "cues": []}


def extract_claims(doc: dict, cfg: dict, bank: CueBank, max_nodes: int) -> list:
    claims = []
    for sent in doc["sentences"]:
        if len(claims) >= max_nodes:
            break
        if sent["limitation"] or sent["section_role"] not in ("interpretation", "evidence", "introduction"):
            continue
        lown = norm(sent["text"])
        hit = bank.claim_cues.hits(sent["text"])
        if bank.aim_cues.hits(sent["text"]) or any(k in lown for k in BACKGROUND_MARKERS):
            continue
        has_result_marker = bool(re.search(r"p\s*[<>=]|β|\br\s*=|准确率|显著|相关|差异|confidence interval|\bci\b|odds", lown))
        role = sent["section_role"]
        if role in ("evidence", "interpretation") and not (hit or has_result_marker):
            continue
        if role == "introduction" and not hit:
            continue
        src = classify_source(sent["text"], bank)
        claims.append({
            "claim_id": f"A{len(claims) + 1}",
            "sent_id": sent["sent_id"],
            "text": sent["text"],
            "section_id": sent["section_id"],
            "section": sent["heading"],
            "section_role": role,
            "char_span": sent["char_span"],
            "locator": sent["locator"],
            "claim_cues": hit,
            "source_type": src["source_type"],
            "source_cues": src["cues"],
            "has_citation": bool(re.search(r"\[\d|et al|（[A-Z][A-Za-z\-]+ et al,? \d{4}）|\([A-Z][A-Za-z\-]+,? \d{4}\)|等\[", sent["text"])),
            "is_premise_like": bool(bank.premise_cues.hits(sent["text"])),
        })
    return claims


def build_graph(claims: list, cfg: dict) -> dict:
    gcfg = cfg.get("graph", {})
    sup_min = float(gcfg.get("support_min_overlap", 0.25))
    attack_min = float(gcfg.get("attack_overlap_min", 0.15))
    max_edges = int(gcfg.get("max_edges_per_node", 3))
    contrast = [norm(c) for c in cfg.get("contrast_cues", [])]
    ordered = list(claims)
    tok = {c["claim_id"]: set(tokens(c["text"])) for c in claims}

    def overlap(a, b):
        return len(a & b) / len(a) if a else 0.0

    edges = []
    for j, cj in enumerate(ordered):
        ct = tok[cj["claim_id"]]
        cand = []
        for i, ci in enumerate(ordered):
            if ci["claim_id"] == cj["claim_id"]:
                continue
            ok = (ci["section_role"] == "evidence" and cj["section_role"] == "interpretation") or \
                 (ci["section_role"] == cj["section_role"] and i < j)
            if ci["section_role"] == "interpretation" and cj["section_role"] == "interpretation" and i > j:
                ov = overlap(ct, tok[ci["claim_id"]])
                if ov >= float(gcfg.get("strong_support_overlap", 0.45)):
                    cand.append({"from": ci["claim_id"], "to": cj["claim_id"], "type": "supports",
                                 "overlap": rounded(ov), "from_locator": ci["locator"],
                                 "from_section": ci["section"], "from_source_type": ci["source_type"],
                                 "note": "解释章节内部强重合（可能构成循环论证）"})
                continue
            if not ok:
                continue
            ov = overlap(ct, tok[ci["claim_id"]])
            if ov >= sup_min:
                kind = "attacks" if any(k in norm(cj["text"]) or k in norm(ci["text"]) for k in contrast) and ov < float(gcfg.get("strong_support_overlap", 0.45)) else "supports"
                cand.append({"from": ci["claim_id"], "to": cj["claim_id"], "type": kind,
                             "overlap": rounded(ov), "from_locator": ci["locator"],
                             "from_section": ci["section"], "from_source_type": ci["source_type"]})
            elif ov >= attack_min and any(k in norm(ci["text"]) for k in contrast):
                cand.append({"from": ci["claim_id"], "to": cj["claim_id"], "type": "attacks",
                             "overlap": rounded(ov), "from_locator": ci["locator"],
                             "from_section": ci["section"], "from_source_type": ci["source_type"]})
        cand.sort(key=lambda e: -e["overlap"])
        edges.extend(cand[:max_edges])
    return {"nodes": ordered, "edges": edges,
            "params": {"support_min_overlap": sup_min, "strong_support_overlap": float(gcfg.get("strong_support_overlap", 0.45)),
                       "attack_overlap_min": attack_min, "max_edges_per_node": max_edges,
                       "edge_rule": "overlap=|claim∩premise|/|claim|；证据章节→解释章节，或同章节前文→后文"}}


def find_cycles(edges: list) -> list:
    adj = {}
    for e in edges:
        if e["type"] == "supports":
            adj.setdefault(e["from"], []).append(e["to"])
    cycles, seen = [], set()

    def dfs(start, node, path):
        if len(path) > 6:
            return
        for nxt in adj.get(node, []):
            if nxt == start and len(path) >= 2:
                key = tuple(sorted(path))
                if key not in seen:
                    seen.add(key)
                    cycles.append(list(path) + [start])
            elif nxt not in path:
                dfs(start, nxt, path + [nxt])

    for n in list(adj):
        dfs(n, n, [n])
    return cycles[:5]


# --------------------------------------------------------------------------------------
# 跨章节检查
# --------------------------------------------------------------------------------------
EN_STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "from", "as", "at",
           "is", "are", "was", "were", "be", "been", "being", "this", "that", "these", "those", "we", "our",
           "their", "its", "it", "than", "then", "so", "between", "among", "into", "about", "over", "under",
           "can", "could", "may", "might", "will", "would", "should", "have", "has", "had", "do", "does",
           "not", "no", "but", "if", "when", "which", "who", "whom", "whose", "what", "whether"}
CJK_PARTICLES = "的了与和并及对在是从把将或而其该此这之着们吗呢吧啊"


def content_tokens(text: str) -> set:
    """承诺-交付覆盖用的内容 token：滤英文虚词与含中文语素助词的二元组，防止虚词稀释覆盖率。"""
    keep = set()
    for t in tokens(text):
        if re.fullmatch(r"[a-z0-9.\-]+", t):
            if t not in EN_STOP and not t.isdigit() and len(t) > 1:
                keep.add(t)
        elif not any(ch in CJK_PARTICLES for ch in t):
            keep.add(t)
    return keep


def promise_matrix(doc: dict, cfg: dict, bank: CueBank, threshold: float) -> dict:
    """引言（及摘要目标句）承诺 → 结果章节交付 对照表。

    交付语料只取「结果」类章节（题面口径：引言承诺是否在结果章节交付）；
    方法章节提到测量工具不算交付。"""
    aims = []
    delivery_parts = []
    for sent in doc["sentences"]:
        if sent["section_role"] == "evidence" and _core_kind(sent["heading"]) == "results":
            delivery_parts.append(sent["text"])
    delivery_tokens = set()
    for t in delivery_parts:
        delivery_tokens |= content_tokens(t)
    for sent in doc["sentences"]:
        role = sent["section_role"]
        if role not in ("introduction", "interpretation"):
            continue
        sent_has_aim = bool(bank.aim_cues.hits(sent["text"]))
        if not sent_has_aim:
            continue
        for clause in re.split(r"[，,；;]", sent["text"]):
            cl = clause.strip()
            # 目标句按子句拆分：连接词碎片（Specifically / 以及）跳过，防止稀释与垃圾目标
            core = cl.rstrip('。.!?！？；;')
            if len(core) < 10 or len(set(tokens(core))) <= 2:
                continue
            if not (bank.aim_cues.hits(cl) or sent_has_aim):
                continue
            # 去掉目标句式前缀（we aim to / 本研究旨在）后再算覆盖，避免句式词稀释
            cl_clean = norm(core)
            for _, pat in bank.aim_cues.items:
                cl_clean = pat.sub(" ", cl_clean)
            tk = content_tokens(cl_clean)
            if not tk:
                continue
            hit = sorted(tk & delivery_tokens)
            ratio = rounded(len(hit) / max(1, len(tk)), 4)
            # 交付判定：覆盖率过阈值且命中内容词 ≥3 个（短子句靠 1-2 个巧合重合不算交付）
            delivered = ratio >= threshold and len(hit) >= 3
            aims.append({"aim": cl[:200], "sent_id": sent["sent_id"], "locator": sent["locator"],
                         "section": sent["heading"], "matched_tokens": hit[:20],
                         "coverage": ratio,
                         "status": ("delivered" if delivered else ("partial" if hit else "undelivered"))})
    undelivered = [a for a in aims if a["status"] != "delivered"]
    return {"aims": aims, "undelivered": undelivered, "coverage_threshold": threshold,
            "delivery_corpus": "结果章节（Results/结果）全部句子", "role_gate": "目标句只从引言/摘要收集",
            "note": "交付判定=目标子句内容词在结果章节中的覆盖率（≥阈值且命中≥3 个内容词）；低覆盖也可能是措辞差异，需人工确认"}


def methods_results_terms(doc: dict, bank: CueBank) -> list:
    """方法声明的指标/信度，结果章节是否真的报告。"""
    methods_text = " ".join(s["text"] for s in doc["sentences"] if s["section_role"] == "evidence"
                            and _is_methods_like(s["heading"]))
    results_text = " ".join(s["text"] for s in doc["sentences"] if s["section_role"] == "evidence"
                            and not _is_methods_like(s["heading"]))
    if not methods_text or not results_text:
        return []
    out = []
    for term, pat in bank.metrics.items:
        if pat.search(norm(methods_text)) and not pat.search(norm(results_text)):
            out.append({"metric": term, "declared_in": "methods", "reported_in_results": False})
    return out


def _is_methods_like(heading: str) -> bool:
    h = norm(heading)
    return any(k in h for k in ("method", "materials and methods", "study design", "participants", "measures",
                                "data collection", "statistical analysis", "方法", "材料与方法", "研究设计", "研究对象", "测量指标", "统计分析"))


def strength_direction(abstract_text: str, conclusion_text: str, bank: CueBank) -> dict:
    """摘要 vs 结论的强度口径比对（限定词词典外置 rules）。"""
    a_strong = bank.strong_claims.hits(abstract_text)
    a_hedge = bank.hedges.hits(abstract_text)
    c_strong = bank.strong_claims.hits(conclusion_text)
    c_hedge = bank.hedges.hits(conclusion_text)
    direction = ""
    if a_strong and c_hedge and not c_strong:
        direction = "abstract_stronger"
    elif c_strong and a_hedge and not a_strong:
        direction = "conclusion_stronger"
    return {"direction": direction,
            "abstract_strong_terms": a_strong, "abstract_hedge_terms": a_hedge,
            "conclusion_strong_terms": c_strong, "conclusion_hedge_terms": c_hedge}


FIG_LABEL_RE = re.compile(r"\b(figure|fig\.?|table|图|表)\s*\.?\s*(\d{1,2})", re.I)


def figure_table_integrity(doc: dict, case: dict, text: str) -> dict:
    """图表引用存在性：正文引用的编号 vs 声明/题注里实际存在的编号。"""
    declared, cited, caption_lines = set(), {}, []
    declared_meta = set()
    for f in as_list(first_present(case, "figures", "images", default=[])):
        label = str(first_present(f, "label", "id", "name", default="")) if isinstance(f, dict) else str(f)
        declared_meta.add(("figure", _label_num(label)))
    for t in as_list(first_present(case, "tables", default=[])):
        label = str(first_present(t, "label", "id", "name", default="")) if isinstance(t, dict) else str(t)
        declared_meta.add(("table", _label_num(label)))
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^\s*(figure|fig\.?|table|图|表)\s*\.?\s*(\d{1,2})[\.\:：\s]", s, re.I)
        if m and len(s) < 300:
            kind = "figure" if m.group(1).lower()[:3] in ("fig", "图") else "table"
            declared.add((kind, m.group(2)))
            caption_lines.append(s[:160])
    for kind, num in declared_meta:
        if num:
            declared.add((kind, str(num)))
    for sent in doc["sentences"]:
        for m in FIG_LABEL_RE.finditer(sent["text"]):
            low = m.group(1).lower()
            kind = "figure" if low[:3] in ("fig", "图") else "table"
            key = (kind, m.group(2))
            cited.setdefault(key, []).append(sent["sent_id"])
    cited_missing = sorted(k for k in cited if k not in declared)
    declared_uncited = sorted(k for k in declared if k not in cited)
    return {"declared": sorted(f"{k[0]} {k[1]}" for k in declared),
            "cited": sorted(f"{k[0]} {k[1]}" for k in cited),
            "cited_missing": [f"{k[0]} {k[1]}" for k in cited_missing],
            "cited_missing_locations": {f"{k[0]} {k[1]}": cited[k] for k in cited_missing},
            "declared_uncited": [f"{k[0]} {k[1]}" for k in declared_uncited],
            "captions_found": caption_lines[:10],
            "note": "declared 来自题注行（Figure N. / 表N：）与 case figures/tables 声明；cited 来自正文句级扫描"}


def _label_num(label: str) -> str:
    m = re.search(r"(\d{1,2})", norm(label))
    return m.group(1) if m else ""


# --------------------------------------------------------------------------------------
# 分型发现（typed findings）+ 白名单抑制
# --------------------------------------------------------------------------------------
NUM_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％)?")


def detect_metric_values(doc: dict, bank: CueBank) -> list:
    """指标名（metric_terms）+ 邻近数值 → 跨章节取值清单（用于 T7）。

    数值取指标名前后 26 字符窗口内**离指标名最近**的一个：兼容
    「response rate 为 82.0%」与「82.0% response rate」两种语序。"""
    occ = []
    num_re = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％)?")
    for sent in doc["sentences"]:
        low = norm(sent["text"])
        for term, pat in bank.metrics.items:
            for m in pat.finditer(low):
                before = low[max(0, m.start() - 26):m.start()]
                after = low[m.end():m.end() + 26]
                na = num_re.search(after)
                nb = None
                for nb_m in num_re.finditer(before):
                    nb = nb_m  # 前置窗口取最后一个（离指标名最近）
                dist_a = na.start() if na else 10 ** 6
                dist_b = (len(before) - nb.end()) if nb else 10 ** 6
                near = na if dist_a <= dist_b else nb
                if near is None:
                    continue
                val = float(near.group(1))
                if near.group(2):
                    val = rounded(val / 100.0, 6)
                occ.append({"metric": term, "value": val, "percent": bool(near.group(2)),
                            "sent_id": sent["sent_id"], "section": sent["heading"],
                            "section_role": sent["section_role"], "locator": sent["locator"],
                            "char_span": sent["char_span"], "text": sent["text"]})
    return occ


COEF_RE = re.compile(r"(?:β|beta|coefficient|系数)\s*=?\s*([+\-−]?\d+(?:\.\d+)?)", re.I)


def detect_coefficients(doc: dict) -> list:
    out = []
    for sent in doc["sentences"]:
        for m in COEF_RE.finditer(sent["text"]):
            raw = m.group(1).replace("−", "-")
            try:
                val = float(raw)
            except ValueError:
                continue
            if val == 0:
                continue
            out.append({"value": val, "sign": 1 if val > 0 else -1, "sent_id": sent["sent_id"],
                        "locator": sent["locator"], "char_span": sent["char_span"], "text": sent["text"]})
    return out


def candidate(detector: str, sent: dict, evidence: dict, extra_loc: dict | None = None) -> dict:
    loc = {"doc_id": None, "section_id": sent["section_id"], "heading": sent["heading"],
           "section_role": sent["section_role"], "para_idx": sent["para_idx"],
           "sent_id": sent["sent_id"], "char_span": sent["char_span"], "locator": sent["locator"]}
    if extra_loc:
        loc.update(extra_loc)
    return {"detector": detector, "sent": sent, "location": loc, "evidence": evidence}


def push_candidate(cands: list, cand_obj: dict) -> None:
    """同句同型去重：不同 detector 可同时命中一句（不同类型发现），同型只记一条。"""
    key = (cand_obj["detector"], cand_obj["location"]["sent_id"])
    if any((c["detector"], c["location"]["sent_id"]) == key for c in cands):
        return
    cands.append(cand_obj)


def guard_for(cand: dict, doc: dict, cfg: dict, bank: CueBank) -> tuple:
    """返回 (guard_id, reason) 或 (None, None)。对照 rules 的 applies_to。"""
    det = cand["detector"]
    rules = (cfg.get("false_positive_guards") or {}).get("rules", [])
    applies = {r["id"]: r.get("applies_to", ["ALL"]) for r in rules}

    def allowed(gid: str) -> bool:
        targets = applies.get(gid, ["ALL"])
        return "ALL" in targets or det in targets

    sent = cand["sent"]
    if allowed("G_LIMITATION_SECTION"):
        if sent.get("limitation"):
            return "G_LIMITATION_SECTION", "问题位于局限性/不足段落，作者已自述该限制"
        sl = bank.sent_limit.hits(sent["text"])
        if sl:
            return "G_LIMITATION_SECTION", f"句内自述局限（命中：{', '.join(sl[:3])}），不按作者缺陷判"
    src = cand.get("source_type")
    cue_note = f"（命中线索：{', '.join(cand.get('source_cues', [])[:4])}）" if cand.get("source_cues") else ""
    pair_src = cand["evidence"].get("pair_source_type")
    if allowed("G_EXPLICIT_ASSUMPTION") and (src == "assumption" or pair_src == "assumption"):
        return "G_EXPLICIT_ASSUMPTION", "句为作者显式标注的假设情形（hypothesize/assume/推测），不按作者结论判缺陷" + cue_note
    if allowed("G_REPORTED_VIEW") and (src == "reported" or pair_src == "reported"):
        return "G_REPORTED_VIEW", "句为转述他人观点（et al/reported that/文献报道），不按作者主张判缺陷" + cue_note
    if allowed("G_INTENDED_CONTROL"):
        ctrl = bank.control_cues.hits(sent["text"]) or (
            cand["evidence"].get("pair_text") and bank.control_cues.hits(cand["evidence"]["pair_text"]))
        if ctrl:
            return "G_INTENDED_CONTROL", "句子带有意对照/敏感性分析标记（negative control/对照/符合预期等），属研究设计而非自相矛盾"
    return None, None


def severity_of(issue_meta: dict, sev_cfg: dict, override: dict | None = None) -> tuple:
    """severity + 规则化判据字符串：影响主结论→major；局部表述→minor；纯建议→suggestion。"""
    ans = issue_meta.get("severity_answers", {})
    m, r = bool(ans.get("affects_main_conclusion")), bool(ans.get("affects_reproducibility"))
    matrix = sev_cfg.get("matrix", {})
    if override:
        return override["severity"], f"override: {override['reason']}（severity.json.override_rules）"
    if ans.get("advisory"):
        return matrix.get("advisory", "suggestion"), (f"affects_main_conclusion=false; affects_reproducibility=false; "
                                                      f"advisory=true → suggestion（severity.json.matrix.advisory）")
    if m and r:
        sev = matrix.get("major_and_repro", "major")
        basis = f"affects_main_conclusion=true; affects_reproducibility=true → {sev}"
    elif m:
        sev = matrix.get("major_only", "major")
        basis = f"affects_main_conclusion=true → {sev}"
    elif r:
        sev = matrix.get("repro_only", "moderate")
        basis = f"affects_main_conclusion=false; affects_reproducibility=true → {sev}"
    else:
        sev = matrix.get("neither", "minor")
        basis = f"affects_main_conclusion=false; affects_reproducibility=false → {sev}（局部表述）"
    return sev, basis + f"（severity.json.matrix；fixable={bool(ans.get('fixable'))}）"


def revision_for(detector: str, issue_meta: dict, cand: dict) -> dict:
    """句级可执行建议：改哪句 / 补什么证据 / 把哪个结论降档到什么强度。"""
    sent = cand["sent"]
    quote = snippet(sent["text"], 0, 120)
    det = detector
    if det == "T5_STRENGTH_MISMATCH":
        return {"action": "downgrade_claim", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": f"把该句强断言词（{cand['evidence'].get('strong_terms', [])}）降为与结果一致的表述（『与…相关』『提示』），"
                          f"或补充可支持该强度的设计证据",
                "downgrade_to": "关联/限定表述（与结果 hedge 口径一致）"}
    if det == "T6_CAUSAL_IN_CORRELATIONAL":
        return {"action": "rewrite_sentence", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": "把该句改为关联表述，或在方法中补因果识别设计（随机分配/纵向随访/工具变量）并重写本句",
                "downgrade_to": "关联表述（『与…相关』）"}
    if det == "T4_PROMISE_UNDELIVERED":
        return {"action": "reconcile_scope", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": "在结果章节补该目标的对应分析，或把引言目标改写为实际交付的范围（删除未交付子句）",
                "evidence_needed": cand["evidence"].get("aim")}
    if det == "T7_NUM_CROSS_SECTION":
        return {"action": "reconcile_numbers", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": f"统一指标 {cand['evidence'].get('metric')} 的取值（{cand['evidence'].get('values')}），"
                          f"每一处注明唯一来源（表号/图号/数据版本）",
                "values": cand["evidence"].get("values")}
    if det == "T9_METHOD_ANSWER_GAP":
        return {"action": "realign_design", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": "把该目标降为关联假设并同步修改结论口径，或补可回答该问题的设计（随机/纵向/准实验）",
                "evidence_needed": "因果识别设计或改写后的目标句"}
    if det == "T10_STATS_REPORTING":
        return {"action": "complete_reporting", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": "补齐统计报告要素：效应量 + 95%CI + 检验统计量与精确 p 值",
                "missing": cand["evidence"].get("missing")}
    if det == "T11_FIGTAB_MISSING":
        return {"action": "add_material_or_fix_reference", "target_sent_id": (cand.get("location") or {}).get("sent_id"),
                "target_quote": quote,
                "detail": f"补上 {cand['evidence'].get('label')} 的图/表本体，或删除/改正正文对该编号的引用"}
    if det == "T12_SIGN_CONFLICT":
        return {"action": "verify_and_explain", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": "核对两处系数口径（变量/模型/样本）后统一，或在文中说明方向差异的原因（对照/亚组）"}
    if det == "T13_STRENGTH_DIRECTION_MISMATCH":
        return {"action": "harmonize_strength", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": "把摘要与结论统一到与结果一致的强度口径（较强的一方降档）",
                "direction": cand["evidence"].get("direction")}
    if det == "T14_METHODS_RESULTS_TERMS":
        return {"action": "report_metric", "target_sent_id": sent["sent_id"], "target_quote": quote,
                "detail": f"在结果章节补报 {cand['evidence'].get('metric')} 数值，或从方法中删除未执行的声明"}
    return {"action": "add_evidence", "target_sent_id": sent["sent_id"], "target_quote": quote,
            "detail": issue_meta.get("fix_template", "补充证据或改写该句")}


# --------------------------------------------------------------------------------------
# 契约绑定（带 facet 裁剪）
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
                if target in ("evidence", "uncertainty", "status"):
                    hit = ("virtual", target, rule.get("facet"))
                    break
                if target in payload:
                    hit = ("payload", target, rule.get("facet"))
                    break
        if not hit:
            continue
        kind, target, facet = hit
        if kind == "payload":
            value = payload[target]
            if facet and target == "findings":
                picked = [f for f in value if facet in f.get("facets", [])]
                value = picked or [{"status": "no_findings", "facet": facet,
                                    "note": "该维度已按要求执行，未发现可复核问题；非 not_produced"}]
            res.set(key, value)
        elif target == "evidence":
            res.set(key, res.evidence_view())
        elif target == "uncertainty":
            res.set(key, res.uncertainty)
        elif target == "status":
            res.set(key, res.status)
        routed.append({"contract_key": key, "source": target, "how": "routing", "facet": facet})
    return routed


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def run(case: dict, task_text: str, args) -> Result:
    rules = load_rules()
    cfg, sev_cfg, codes, routing, facets_cfg = rules["arg"], rules["severity"], rules["codes"], rules["routing"], rules["facets"]
    bank = CueBank(cfg)
    res = Result(case, skill=SKILL, clock=Clock(getattr(args, "budget", 900.0)))
    res.log("load_case", case_id=res.case_id, contract_keys=sorted(res.contract))
    work = Path(getattr(args, "work", ".") or ".")
    case_dir = Path(getattr(args, "case") or "").parent if getattr(args, "case", None) else None

    doc = collect_manuscript_doc(case, work, cfg, bank, case_dir=case_dir)
    got = doc["got"]
    res.log("extract", method=got.get("extraction_method"), chars=len(doc["text"]),
            sections=len(doc["sections"]), sentences=len(doc["sentences"]),
            unparsed=len(got.get("unparsed", [])))
    if len(doc["text"].strip()) < 80:
        res.set("研究产物", {"status": "not_produced",
                             "reason": f"未读到可审查正文（extraction_method={got.get('extraction_method') or 'none'}）",
                             "unparsed": got.get("unparsed", [])})
        res.status["overall"] = "empty"
        res.unmet("论文正文", codes.get("R_NO_TEXT_LAYER", "无可提取文本层，未做内容审查，不编造结论"))
        res.evidence["extraction"] = {k: got.get(k) for k in
                                      ("extraction_method", "source", "source_sha256", "materials_audit",
                                       "unparsed", "n_pages", "text_layers", "declared_total_chars")}
        res.done("全文获取（诚实失败）")
        return res

    coverage = build_coverage(doc, case)
    res.evidence["coverage"] = coverage
    if got.get("extraction_method") == "pdf_stdlib_flate":
        res.unsure("PDF 文本重建", "自研 stdlib 提取为 best-effort：无字距/列版面还原，定位以重建文本的 char_span 为准")
        res.note(codes.get("R_EXTRACTION_FALLBACK", ""))
    if coverage["truncated"]:
        res.unmet("全文覆盖", codes.get("R_TRUNCATED", "稿件被截断或章节缺失：只审查了已获文本") +
                  f"；缺失章节={coverage['core_sections_missing']}；证据={coverage['truncation_evidence']}")

    # ---- facets 选择 ----
    sel = select_facets(case, task_text or "", facets_cfg)
    requested = sel["requested"]
    facet_meta = facets_cfg.get("facets", {})
    detectors_to_run = []
    for f in requested:
        for d in facet_meta.get(f, {}).get("detectors", []):
            if d not in detectors_to_run:
                detectors_to_run.append(d)
    res.log("facets", requested=requested, detectors=detectors_to_run, mode=sel["mode"])

    claims = extract_claims(doc, cfg, bank, int(cfg.get("graph", {}).get("max_nodes", 60)))
    graph = build_graph(claims, cfg)
    cycles = find_cycles(graph["edges"])
    nodes_by_id = {c["claim_id"]: c for c in claims}
    incoming = {}
    for e in graph["edges"]:
        incoming.setdefault((e["to"], e["type"]), []).append(e)
    if len(claims) >= int(cfg.get("graph", {}).get("max_nodes", 60)):
        res.note(codes.get("R_TRUNCATED", "主张数达预算上限，其后句子未入图（coverage 已记 sentence_count）"))

    # 设计口径（方法无法回答问题 / 相关当因果 共用）
    methods_text = " ".join(s["text"] for s in doc["sentences"] if _is_methods_like(s["heading"]))
    design_obs = bank.design_obs.hits(methods_text)
    design_causal = bank.design_causal.hits(methods_text)
    if design_causal:
        design_kind = "causal_design"
    elif design_obs:
        design_kind = "observational"
    else:
        design_kind = "unknown"

    ev_hedges = []
    ev_strong = []
    for s in doc["sentences"]:
        if s["section_role"] == "evidence":
            ev_hedges += [h for h in bank.hedges.hits(s["text"]) if h not in ev_hedges]
            ev_strong += [h for h in bank.strong_claims.hits(s["text"]) if h not in ev_strong]

    cands: list[dict] = []

    # T1/T2/T8：图结构断裂点（按 facets 门控）
    graph_detectors = {"T1_UNSUPPORTED_CLAIM", "T2_ASSUMPTION_ONLY", "T8_CONTRADICTORY_EDGE"}
    run_graph_breaks = bool(graph_detectors & set(detectors_to_run))
    for c in claims:
        if not run_graph_breaks:
            break
        if c["section_role"] != "interpretation":
            continue
        sup = incoming.get((c["claim_id"], "supports"), [])
        att = incoming.get((c["claim_id"], "attacks"), [])
        if not sup:
            push_candidate(cands, candidate("T1_UNSUPPORTED_CLAIM", _sent_by_id(doc, c["sent_id"]),
                                   {"incoming_support": 0, "incoming_attack": len(att), "claim_id": c["claim_id"]}))
        elif all(nodes_by_id[e["from"]]["source_type"] == "assumption" for e in sup):
            push_candidate(cands, candidate("T2_ASSUMPTION_ONLY", _sent_by_id(doc, c["sent_id"]),
                                   {"support_from": [e["from"] for e in sup], "claim_id": c["claim_id"]}))
        if att and not any(k in norm(c["text"]) for k in cfg.get("contrast_cues", []) + cfg.get("hedges", [])):
            push_candidate(cands, candidate("T8_CONTRADICTORY_EDGE", _sent_by_id(doc, c["sent_id"]),
                                   {"attack_edges": [{"from": e["from"], "overlap": e["overlap"]} for e in att]}))

    # T3：循环论证
    if cycles and "T3_CIRCULAR" in detectors_to_run:
        ids = cycles[0]
        c = next((x for x in claims if x["claim_id"] == ids[0]), None)
        if c is not None:
            push_candidate(cands, candidate("T3_CIRCULAR", _sent_by_id(doc, c["sent_id"]), {"cycle": ids}))

    # T4：引言承诺未交付
    promise = promise_matrix(doc, cfg, bank, float(cfg.get("graph", {}).get("support_min_overlap", 0.25)))
    for row in promise["undelivered"]:
        sent = next((s for s in doc["sentences"] if s["sent_id"] == row["sent_id"]), None)
        if sent is None or "T4_PROMISE_UNDELIVERED" not in detectors_to_run:
            continue
        push_candidate(cands, candidate("T4_PROMISE_UNDELIVERED", sent,
                               {"aim": row["aim"], "coverage": row["coverage"],
                                "matched_tokens": row["matched_tokens"], "delivery_corpus": promise["delivery_corpus"]}))

    # T5：解释章节强断言 vs 证据章节 hedge
    if "T5_STRENGTH_MISMATCH" in detectors_to_run and ev_hedges:
        for s in doc["sentences"]:
            if s["section_role"] != "interpretation" or s["limitation"]:
                continue
            strong_hits = bank.strong_claims.hits(s["text"])
            if strong_hits:
                push_candidate(cands, candidate("T5_STRENGTH_MISMATCH", s,
                                       {"strong_terms": strong_hits, "results_hedges": ev_hedges[:8]}))

    # T6：因果表述 vs 观察性设计（解释章节的因果结论 + 引言中的因果假设句；
    # 显式假设/转述/有意对照由白名单抑制，目标句交给 T9）
    if "T6_CAUSAL_IN_CORRELATIONAL" in detectors_to_run:
        for s in doc["sentences"]:
            if s["section_role"] not in ("interpretation", "introduction") or s["limitation"]:
                continue
            if bank.aim_cues.hits(s["text"]):
                continue
            ca = bank.causal_terms.hits(s["text"])
            if not ca:
                continue
            co = bank.correlational_terms.hits(s["text"])
            if design_kind == "observational" or (design_kind == "unknown" and co):
                push_candidate(cands, candidate("T6_CAUSAL_IN_CORRELATIONAL", s,
                                       {"causal_terms": ca, "correlational_terms": co,
                                        "design_kind": design_kind, "design_markers_obs": design_obs[:6],
                                        "design_markers_causal": design_causal[:6]}))

    # T7：指标数值跨章节不一致
    metric_occ = detect_metric_values(doc, bank)
    by_metric = {}
    for o in metric_occ:
        by_metric.setdefault(o["metric"], []).append(o)
    for metric, occs in sorted(by_metric.items()):
        values = sorted({o["value"] for o in occs})
        if len(values) > 1 and "T7_NUM_CROSS_SECTION" in detectors_to_run:
            first = occs[0]
            sent = _sent_by_id(doc, first["sent_id"])
            push_candidate(cands, candidate("T7_NUM_CROSS_SECTION", sent,
                                   {"metric": metric, "values": values,
                                    "occurrences": [{"value": o["value"], "section": o["section"],
                                                     "sent_id": o["sent_id"], "locator": o["locator"]} for o in occs]}))

    # T9：方法无法回答研究问题
    if "T9_METHOD_ANSWER_GAP" in detectors_to_run and design_kind == "observational":
        for row in promise["aims"]:
            sent = next((s for s in doc["sentences"] if s["sent_id"] == row["sent_id"]), None)
            if sent is None:
                continue
            mech = bank.causal_terms.hits(row["aim"]) or bool(re.search(r"mechanis|mediat|作用机制|中介", norm(row["aim"])))
            if mech:
                push_candidate(cands, candidate("T9_METHOD_ANSWER_GAP", sent,
                                       {"aim": row["aim"], "design_kind": design_kind,
                                        "design_markers_obs": design_obs[:6], "design_markers_causal": design_causal[:6]}))

    # T10：统计报告要素
    if "T10_STATS_REPORTING" in detectors_to_run:
        effect_tokens = re.compile(
            r"β|beta|odds ratio|hazard ratio|risk ratio|effect size|95%|\bci\b|效应量|均值差|mean difference|率|百分比|相关系数|\br\s*[=:]|\bb\s*[=:]|%", re.I)
        for s in doc["sentences"]:
            if s["section_role"] not in ("evidence", "interpretation"):
                continue
            low = norm(s["text"])
            has_p = re.search(r"\bp\s*[<>=]\s*\d", low)
            if has_p and not effect_tokens.search(low):
                push_candidate(cands, candidate("T10_STATS_REPORTING", s,
                                       {"rule": "p_without_effect_size", "missing": ["效应量/95%CI"], "has": ["p 值"]}))
                continue
            sig = re.search(r"significant|显著", low)
            exact_p = re.search(r"\bp\s*=\s*\d", low)
            test_name = re.search(r"chi|t-test|anova|regression|\bz\s*=|\bt\s*=|\bf\s*=|卡方|回归|检验统计", low)
            if sig and not exact_p and not test_name:
                push_candidate(cands, candidate("T10_STATS_REPORTING", s,
                                       {"rule": "significant_without_exact_p_or_test", "missing": ["精确 p 值", "检验统计量"],
                                        "has": ["significance wording"]}))

    # T11：图表引用存在性
    figtab = figure_table_integrity(doc, case, doc["text"])
    if "T11_FIGTAB_MISSING" in detectors_to_run:
        for label in figtab["cited_missing"]:
            sid = (figtab["cited_missing_locations"].get(label) or [""])[0]
            sent = _sent_by_id(doc, sid) or {"section_id": "S0", "heading": "全文", "section_role": "other",
                                             "para_idx": 0, "sent_id": sid or "s0000",
                                             "char_span": [0, 0], "locator": f"{doc['got'].get('source', 'inline')}/WHOLE",
                                             "text": f"正文引用了 {label}", "limitation": False}
            push_candidate(cands, candidate("T11_FIGTAB_MISSING", sent,
                                   {"label": label, "kind": "cited_but_missing",
                                    "declared": figtab["declared"], "captions_found": figtab["captions_found"]}))
        for label in figtab["declared_uncited"]:
            push_candidate(cands, candidate("T11_FIGTAB_MISSING",
                                   {"section_id": "S0", "heading": "图表清单", "section_role": "other", "para_idx": 0,
                                    "sent_id": "s0000", "char_span": [0, 0], "locator": "DECLARED", "text": f"已呈现但未引用：{label}",
                                    "limitation": False},
                                   {"label": label, "kind": "declared_but_uncited", "declared": figtab["declared"]}))

    # T12：同暴露系数方向冲突
    coefs = detect_coefficients(doc)
    if "T12_SIGN_CONFLICT" in detectors_to_run:
        for i, a in enumerate(coefs):
            for b in coefs[i + 1:]:
                if a["sent_id"] == b["sent_id"] or a["sign"] == b["sign"]:
                    continue
                shared = set(tokens(a["text"])) & set(tokens(b["text"]))
                if len(shared) < 3:
                    continue
                sent = _sent_by_id(doc, b["sent_id"])
                push_candidate(cands, candidate("T12_SIGN_CONFLICT", sent,
                                       {"signs": [a["sign"], b["sign"]],
                                        "values": [a["value"], b["value"]],
                                        "pair_sent_id": a["sent_id"], "pair_locator": a["locator"],
                                        "pair_text": a["text"], "shared_tokens": sorted(shared)[:8]}))

    # T13：摘要 vs 结论强度方向
    if "T13_STRENGTH_DIRECTION_MISMATCH" in detectors_to_run:
        def _core_sents(kind):
            out = []
            for s in doc["sentences"]:
                if _core_kind(s["heading"]) == kind and not any(norm(s["heading"]).startswith(kw) for kw in ("keywords", "key words", "关键词")):
                    out.append(s)
            return out
        abs_sents = _core_sents("abstract")
        con_sents = _core_sents("conclusion")
        if abs_sents and con_sents:
            sd = strength_direction(" ".join(s["text"] for s in abs_sents),
                                    " ".join(s["text"] for s in con_sents), bank)
            if sd["direction"]:
                if sd["direction"] == "conclusion_stronger":
                    anchored = [s for s in con_sents if bank.strong_claims.hits(s["text"])] or con_sents
                    anchor = anchored[0]
                else:
                    anchored = [s for s in abs_sents if bank.strong_claims.hits(s["text"])] or abs_sents
                    anchor = anchored[-1]
                push_candidate(cands, candidate("T13_STRENGTH_DIRECTION_MISMATCH", anchor, sd))

    # T14：方法声明的指标结果未报告
    if "T14_METHODS_RESULTS_TERMS" in detectors_to_run:
        methods_sents = [s for s in doc["sentences"] if s["section_role"] == "evidence" and _is_methods_like(s["heading"])]
        for gap in methods_results_terms(doc, bank):
            anchor = methods_sents[0] if methods_sents else doc["sentences"][0]
            push_candidate(cands, candidate("T14_METHODS_RESULTS_TERMS", anchor,
                                   {"metric": gap["metric"], "declared_in": "methods", "reported_in_results": False}))

    # ---- 白名单抑制（系统化：候选先过 guard，命中进 suppressed，不出 findings）----
    findings, suppressed = [], []
    for cand_obj in cands:
        src = classify_source(cand_obj["sent"]["text"], bank)
        cand_obj["source_type"] = src["source_type"]
        cand_obj["source_cues"] = src["cues"]
        if cand_obj["evidence"].get("pair_text") and "pair_source_type" not in cand_obj["evidence"]:
            cand_obj["evidence"]["pair_source_type"] = classify_source(cand_obj["evidence"]["pair_text"], bank)["source_type"]
        gid, reason = guard_for(cand_obj, doc, cfg, bank)
        sent = cand_obj["sent"]
        quote = sent.get("text", "")
        if gid:
            suppressed.append({
                "suppressed_id": f"S{len(suppressed) + 1:03d}",
                "guard_id": gid,
                "detector": cand_obj["detector"],
                "reason": reason,
                "location": cand_obj["location"],
                "quote": snippet(quote, 0, 160),
                "quote_verified": bool(quote) and quote in doc["text"],
            })
            continue
        meta = (cfg.get("issue_types") or {}).get(cand_obj["detector"], {})
        facets_of = meta.get("facets", [])
        override = None
        if cand_obj["detector"] == "T11_FIGTAB_MISSING" and cand_obj["evidence"].get("kind") == "declared_but_uncited":
            ov = next((o for o in sev_cfg.get("override_rules", []) if "declared_but_uncited" in o.get("when", "")), None)
            if ov:
                override = {"severity": ov["severity"], "reason": ov["reason"]}
        sev, basis = severity_of(meta, sev_cfg, override)
        raw_quote = sent.get("text", "")
        findings.append({
            "detector": cand_obj["detector"],
            "issue_label": meta.get("label", cand_obj["detector"]),
            "category": meta.get("category", ""),
            "category_label": meta.get("category_label", ""),
            "facet": facets_of[0] if facets_of else "",
            "facets": facets_of,
            "severity": sev,
            "severity_basis": basis,
            "severity_answers": meta.get("severity_answers", {}),
            "location": {**cand_obj["location"], "doc_id": _doc_id(doc["got"])},
            "quote": snippet(raw_quote, 0, 200),
            "quote_verified": bool(raw_quote) and raw_quote in doc["text"],
            "source_type": cand_obj.get("source_type"),
            "why": meta.get("why", ""),
            "evidence": cand_obj["evidence"],
            "revision": revision_for(cand_obj["detector"], meta, cand_obj),
        })

    order = sev_cfg.get("order", {"major": 0, "moderate": 1, "minor": 2, "suggestion": 3, "editorial": 4})
    findings.sort(key=lambda x: (order.get(x["severity"], 9), x["detector"], x["location"]["sent_id"]))
    suppressed.sort(key=lambda x: (x["guard_id"], x["detector"], x["location"]["sent_id"]))
    for i, f in enumerate(findings, start=1):
        f["finding_id"] = f"F{i:03d}"
    for i, s in enumerate(suppressed, start=1):
        s["suppressed_id"] = f"S{i:03d}"

    # ---- facets_status ----
    facets_status = []
    for f, meta in facet_meta.items():
        if f in requested:
            facets_status.append({"facet": f, "label": meta.get("label", f), "status": "executed",
                                  "detectors": meta.get("detectors", []),
                                  "findings": sum(1 for x in findings if f in x["facets"]),
                                  "suppressed": sum(1 for x in suppressed if x["detector"] in meta.get("detectors", [])),
                                  "boundary": meta.get("boundary", "")})
        else:
            facets_status.append({"facet": f, "label": meta.get("label", f), "status": "unchecked",
                                  "reason": sel.get("matched_by") if sel["mode"] in ("task_cues", "case_declared")
                                  else "任务指令未点名该维度",
                                  "detectors": meta.get("detectors", []),
                                  "note": codes.get("R_FACET_UNCHECKED", "未点名的维度未执行，不冒充全查")})
    if sel.get("unknown_requests"):
        res.unmet("审查维度", f"任务点名的维度无法识别：{sel['unknown_requests']}；已按可识别维度执行")

    # ---- 论证图完整性 / 不确定性 ----
    if len(claims) < 2:
        res.unmet("主张抽取", codes.get("R_FEW_CLAIMS", "可抽取主张过少，论证图不足以判定结构"))
    elif len(claims) < 3:
        res.note("可抽取主张较少（2 条），论证图结构判定覆盖有限，已如实声明")
    if not any(s["role"] == "interpretation" for s in doc["sections"]):
        res.unmet("章节角色", codes.get("R_NO_SECTION_ROLE", "未识别到摘要/讨论/结论章节，跨章节判定已降级"))
        res.unsure("跨章节一致性", "缺解释章节标题时，承诺-交付矩阵只能按词覆盖估计")
    if not graph["edges"]:
        res.unmet("论证图", "未建立任何支持/攻击边：重合度均低于阈值，无法判断断裂点")
    res.unsure("论证边判定", "边由 token 重合度阈值决定：同义改写会漏建边，『无支撑』类发现需人工确认（未建边 ≠ 无支撑）")
    res.unsure("意图判断", "作者意图不可知：显式假设/转述他人/有意对照已按白名单抑制并记录 suppressed，其余按作者主张处理")
    res.unsure("跨章节数值", "只比对 rules 里 metric_terms 可抽取的指标数值；图内/表格内数值不可读")
    if design_kind == "unknown" and "T9_METHOD_ANSWER_GAP" in detectors_to_run:
        res.unsure("研究设计口径", "方法章节未识别到观察性或因果设计标记，T9 未执行")
    if "citation_support" in requested:
        res.unsure("引用支持", "离线无法读取被引文献原文：仅能检查文内相反证据处理情况，『所引文献是否支持该句』为待复核")
    if coverage["truncated"] and not coverage["truncation_evidence"]:
        res.unsure("截断判定", "无法确定平台是否截断：未发现显式标记，结构怀疑已记入 coverage")

    # ---- 状态 ----
    requested_executed = all(any(fs["facet"] == f and fs["status"] == "executed" for fs in facets_status) for f in requested)
    core_ok = (coverage["sections_present"].get("introduction") and coverage["sections_present"].get("methods")
               and coverage["sections_present"].get("results"))
    overall = "complete"
    if coverage["truncated"] or not core_ok:
        overall = "partial"
    graph_needed = any(f in ("argument_chain", "section_coherence", "citation_support") for f in requested)
    if graph_needed and (len(claims) < 2 or not graph["edges"]
                         or not any(s["role"] == "interpretation" for s in doc["sections"])):
        overall = "partial"
    if not requested_executed:
        overall = "partial"
    res.status["overall"] = overall
    res.done("全文获取与覆盖率登记", "章节切分与句级索引", "审查维度动态绑定",
             "主张抽取与来源三分类", "论证图构建与断裂点检测", "跨章节一致性检查",
             "分型发现与严重度分级", "白名单抑制（suppressed）")
    if coverage["truncated"]:
        res.done("截断诚实降级（partial）")

    deliverable = {
        "coverage": coverage,
        "facets_status": facets_status,
        "claims": claims,
        "sentence_index": [{"sent_id": s["sent_id"], "section_id": s["section_id"], "para_idx": s["para_idx"],
                            "char_span": s["char_span"], "section_role": s["section_role"],
                            "heading": s["heading"], "text": s["text"]}
                           for s in doc["sentences"]],
        "argument_graph": {
            "nodes": [{"claim_id": c["claim_id"], "sent_id": c["sent_id"], "text": c["text"][:200],
                       "section_id": c["section_id"], "section": c["section"], "section_role": c["section_role"],
                       "source_type": c["source_type"], "locator": c["locator"]} for c in claims],
            "edges": graph["edges"], "cycles": cycles, "params": graph["params"],
            "breakage_summary": {
                "unsupported": sum(1 for f in findings if f["detector"] == "T1_UNSUPPORTED_CLAIM"),
                "assumption_only": sum(1 for f in findings if f["detector"] == "T2_ASSUMPTION_ONLY"),
                "contradictory": sum(1 for f in findings if f["detector"] == "T8_CONTRADICTORY_EDGE"),
                "cycles": len(cycles)},
        },
        "cross_section_matrix": promise,
        "coherence": {
            "methods_vs_results_terms": methods_results_terms(doc, bank),
            "strength_direction": _strength_direction_view(doc, bank),
            "figure_table_integrity": figtab,
        },
        "findings": findings,
        "suppressed": suppressed,
        "references": parse_references(case, doc["sections"]),
        "summary": {
            "claims": len(claims), "edges": len(graph["edges"]), "cycles": len(cycles),
            "sentences": len(doc["sentences"]), "sections": len(doc["sections"]),
            "findings": len(findings),
            "by_detector": _count(findings, "detector"),
            "by_severity": _count(findings, "severity"),
            "by_category": _count(findings, "category"),
            "by_facet": _count(findings, "facet"),
            "source_type_counts": {k: sum(1 for c in claims if c["source_type"] == k)
                                   for k in ("author", "reported", "assumption")},
            "suppressed": len(suppressed),
            "by_guard": _count(suppressed, "guard_id"),
            "design_kind": design_kind,
        },
        "fix_plan": [{"finding_id": f["finding_id"], "severity": f["severity"], "detector": f["detector"],
                      "locator": f["location"]["locator"], "action": f["revision"]["detail"]}
                     for f in findings],
    }
    payload = {"研究产物": deliverable, "claims": claims, "sentence_index": deliverable["sentence_index"],
               "argument_graph": deliverable["argument_graph"], "cross_section_matrix": promise,
               "coherence": deliverable["coherence"], "findings": findings, "suppressed": suppressed,
               "coverage": coverage, "facets_status": facets_status, "summary": deliverable["summary"]}
    res.evidence.update({
        "extraction": {k: got.get(k) for k in ("extraction_method", "source", "source_sha256", "text_sha256",
                                               "n_pages", "text_layers", "materials_audit", "unparsed",
                                               "declared_total_chars")},
        "coverage": coverage,
        "facets_selection": sel,
        "facets_status": facets_status,
        "graph_params": graph["params"],
        "edges": graph["edges"],
        "guards": {"suppressed": suppressed, "rules": (cfg.get("false_positive_guards") or {}).get("rules", []),
                   "note": "命中白名单的候选不出现在 findings，可在 suppressed 里核对『该报未报』质疑"},
        "design_kind": design_kind,
        "rules_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(RULES.glob("*.json"))},
        "scope": {"claims": len(claims), "edges": len(graph["edges"]),
                  "detectors_run": detectors_to_run,
                  "note": "边由重合度阈值决定；未建边不等于无支撑（见 uncertainty）"},
    })
    routed = route_contract(res, payload, routing)
    for k, v in payload.items():
        if k not in res.fields:
            res.set(k, v)
    res.evidence["contract_routing"] = routed
    for key in res.contract:
        if key not in res.fields:
            res.unmet(key, "该合同字段名未命中 rules/contract_routing.json，未以推测值填充")
    return res


def _sent_by_id(doc: dict, sent_id: str) -> dict | None:
    return next((s for s in doc["sentences"] if s["sent_id"] == sent_id), None)


def _strength_direction_view(doc: dict, bank: CueBank) -> dict:
    abs_sents = [s["text"] for s in doc["sentences"] if _core_kind(s["heading"]) == "abstract"]
    con_sents = [s["text"] for s in doc["sentences"] if _core_kind(s["heading"]) == "conclusion"]
    if abs_sents and con_sents:
        return strength_direction(" ".join(abs_sents), " ".join(con_sents), bank)
    return {"direction": "", "note": "缺摘要或结论章节，未做强度方向比对"}


def _core_kind(heading: str) -> str:
    h = norm(heading)
    for core, lex in CORE_LEXICON.items():
        if any(norm(k) in h for k in lex):
            return core
    return ""


def _count(items: list, key: str) -> dict:
    out = {}
    for it in items:
        k = it.get(key) or "(none)"
        out[k] = out.get(k, 0) + 1
    return out


def _write_input_error(args, exc: Exception) -> int:
    payload = {
        "case_id": "UNKNOWN-CASE", "skill": {"name": SKILL, "version": "0.1.0"},
        "研究产物": {"status": "not_produced", "reason": str(exc)},
        "evidence": {"error": f"{type(exc).__name__}: {exc}"},
        "status": {"overall": "input_error", "completed": [], "unmet": [{"item": "case.json", "reason": str(exc)}],
                   "notes": ["输入不可读时立即失败并保留错误原文"]},
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
        print(json.dumps({"case_id": payload["case_id"], "status": payload["status"]["overall"]}, ensure_ascii=False))
        return 0
    path = res.emit(args.out)
    print(json.dumps({"ok": True, "result": str(path), "status": payload["status"]["overall"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
