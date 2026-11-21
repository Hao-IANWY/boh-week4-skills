#!/usr/bin/env python3
"""pdf_min.py —— 011 的最小 PDF 工具（纯标准库，离线）。

只做两件事，不引入任何第三方依赖：
1. `build_pdf(pages)`：手写一个最小但合法的 PDF（zlib Flate 压缩的 BT/ET/Tj 文本流），
   供示例与真实仿真（review/011_realtest/build_cases.py）生成"影印件"输入。
2. `extract_pdf_text(path)`：对不依赖对象树的轻量解析——逐个扫描 `stream...endstream`，
   尝试 zlib 解压，抽取 BT...ET 里的 `(string) Tj` 与 `[...] TJ` 文本，按出现顺序还原每页文本。
   这不是通用 PDF 解析器：扫描件（图像）抽不出文本时返回空 pages，调用方必须如实记 gap，
   不许编造正文。

设计边界（有意不做的）：
- 不解析 xref/对象树/字体编码表，只按流出现顺序映射页码；官方传真件若用非标准编码或
  非文本流，本提取器会失败——这正是 `extraction_method=failed` 的诚实路径。
- 不做 OCR。OCR 文本只能来自侧车文件（<pdf>.ocr.txt），pipeline 据此标 ocr_boundary。

自检：`python3 -B scripts/pdf_min.py` 跑一次写入→读回的 round-trip。
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

# --------------------------------------------------------------------------------------
# 写入端：最小合法 PDF（页面 612x792pt，Courier 10pt，行距 14pt）
# --------------------------------------------------------------------------------------
_PAGE_W, _PAGE_H = 612, 792
_LEADING = 14
_MAX_LINES = 48  # 792/14 ≈ 56，留边距取 48 行/页，超出行由调用方分页


def escape_pdf_string(text: str) -> str:
    """PDF 字面字符串转义：\\ ( ) 与控制符；超出 latin-1 的字符替换为 '?'，不静默丢页。"""
    out = []
    for ch in text:
        if ch in {"\\", "(", ")"}:
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) <= 0x1F:
            out.append(" ")
        else:
            try:
                ch.encode("latin-1")
                out.append(ch)
            except UnicodeEncodeError:
                out.append("?")
    return "".join(out)


def _content_stream(page_text: str) -> str:
    lines = page_text.split("\n")[:_MAX_LINES]
    parts = ["BT", "/F1 10 Tf", f"{_LEADING} TL", f"72 {_PAGE_H - 72} Td"]
    for line in lines:
        parts.append(f"({escape_pdf_string(line)}) Tj T*")
    parts.append("ET")
    return "\n".join(parts)


def build_pdf(pages: list) -> bytes:
    """把每页文本写成单页 PDF。pages 为字符串列表（页文本，\n 分行）。"""
    page_texts = [str(p) for p in (pages or [""])]
    kids, bodies = [], {}  # obj_num -> body bytes
    # obj1 Catalog / obj2 Pages / obj3 Font；每页占两个对象号（页面字典 + 内容流）
    first_page_obj = 4
    for i, text in enumerate(page_texts):
        page_obj = first_page_obj + 2 * i
        content_obj = page_obj + 1
        kids.append(f"{page_obj} 0 R")
        stream = _content_stream(text).encode("latin-1", "replace")
        bodies[content_obj] = f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream"
        bodies[page_obj] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_obj} 0 R >>"
        ).encode("latin-1")
    bodies[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids_s = " ".join(kids)
    bodies[2] = f"<< /Type /Pages /Kids [{kids_s}] /Count {len(page_texts)} >>".encode("latin-1")
    bodies[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"

    ordered = [1, 2, 3] + [n for n in sorted(bodies) if n >= 4]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for num in ordered:
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode("latin-1") + bodies[num] + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(ordered) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(ordered) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def write_pdf_file(path, pages: list) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(build_pdf(pages))
    return p


# --------------------------------------------------------------------------------------
# 读取端：流扫描 + zlib 解压 + BT/ET 文本抽取（不做通用 PDF 解析）
# --------------------------------------------------------------------------------------
_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
_TJ_RE = re.compile(r"\(((?:\\.|[^\\()])*)\)\s*Tj|\[((?:\\.|[^\]])*)\]\s*TJ")
_STR_IN_ARRAY_RE = re.compile(r"\(((?:\\.|[^\\()])*)\)")


def _unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        ch = s[i]
        if ch != "\\" or i + 1 >= len(s):
            out.append(ch)
            i += 1
            continue
        nxt = s[i + 1]
        simple = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "(": "(", ")": ")", "\\": "\\"}
        if nxt in simple:
            out.append(simple[nxt])
            i += 2
        elif nxt.isdigit():
            j = i + 1
            oct_digits = ""
            while j < len(s) and len(oct_digits) < 3 and s[j] in "01234567":
                oct_digits += s[j]
                j += 1
            out.append(chr(int(oct_digits, 8)))
            i = j
        else:
            out.append(nxt)
            i += 2
    return "".join(out)


def _text_from_stream(content: str) -> list:
    lines = []
    for m in _TJ_RE.finditer(content):
        if m.group(1) is not None:
            lines.append(_unescape(m.group(1)))
        else:
            seg = "".join(_unescape(x) for x in _STR_IN_ARRAY_RE.findall(m.group(2)))
            if seg:
                lines.append(seg)
    return lines


def extract_pdf_text(path) -> dict:
    """尽力抽取文本；扫描件/非文本流返回 pages=[]。调用方对空结果必须如实记 gap。"""
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "reason": "file_missing", "pages": [], "char_count": 0}
    data = p.read_bytes()
    pages = []
    for m in _STREAM_RE.finditer(data):
        raw = m.group(1)
        content = None
        try:
            content = zlib.decompress(raw).decode("latin-1", "replace")
        except zlib.error:
            if b"BT" in raw and b"ET" in raw:  # 未压缩流兜底
                content = raw.decode("latin-1", "replace")
        if not content or "BT" not in content:
            continue
        lines = _text_from_stream(content)
        if lines:
            pages.append("\n".join(lines))
    text = "\n".join(pages)
    return {"ok": bool(pages), "reason": "" if pages else "no_text_stream_found",
            "pages": pages, "char_count": len(text)}


if __name__ == "__main__":
    sample = [
        "USPTO Patent Full Text (SYNTHETIC EXAMPLE)\nPatent No.: US 10,123,456 B2\nClaims:\n"
        "1. A solid electrolyte comprising a garnet-type oxide layer.\n2. The electrolyte of claim 1.",
        "Page 2: (escaped) parens \\ and octal \101 test — ligature ﬁ → ?",
    ]
    tmp = Path("/tmp/pdf_min_roundtrip.pdf")
    write_pdf_file(tmp, sample)
    got = extract_pdf_text(tmp)
    want0 = [l for l in sample[0].split("\n")]
    assert got["ok"], got
    assert got["pages"][0].split("\n") == want0, (got["pages"][0], want0)
    assert "A solid electrolyte comprising a garnet-type oxide layer." in got["pages"][0]
    assert "\\ and octal A test" in got["pages"][1], got["pages"][1]
    assert "?" in got["pages"][1]  # latin-1 外字符不静默丢页
    print(f"pdf_min round-trip OK: {len(got['pages'])} pages, {got['char_count']} chars")
