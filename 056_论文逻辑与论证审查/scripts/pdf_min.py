#!/usr/bin/env python3
"""pdf_min.py —— 纯标准库 PDF/DOCX 文本层提取（无 PyPDF/pdfminer 时的自研回退）。

能力边界（如实声明，不冒充完整解析器）：
- PDF：扫描原始字节里的 stream 对象，FlateDecode 用 zlib 解压，未压缩流直接扫描；
  只提取 BT…ET 文本块中的 `(...) Tj` 与 `[...] TJ`，跨行/跨列顺序按流内出现顺序，
  不做字距、列版面或 CMap/ToUnicode 还原 —— 加密、非 Flate 压缩、CID 字体都可能拿不到字，
  此时返回 text_layers=False，由调用方诚实降级，绝不编造正文。
- DOCX：zipfile 读 word/document.xml，按 <w:p> 段落拼接 <w:t> 文本。
- 输出文本是**重建文本**：char_span/句索引以这份重建文本的偏移为准，evidence 里记录 sha256。
"""
from __future__ import annotations

import hashlib
import re
import zlib

# 一个 PDF 字符串体里允许的内容（含常见转义）
_PDF_STRING = rb"\((?:\\.|[^()\\])*\)"
# TJ 数组用非嵌套的保守匹配（数组内含 `]` 字符串的场景不支持，属 best-effort 边界）
_TJ_ARRAY = re.compile(rb"\[[^\]]{0,4000}\]\s*TJ\*?")
_TJ_SIMPLE = re.compile(_PDF_STRING + rb"\s*Tj")
_BT_ET = re.compile(rb"BT(.*?)ET", re.S)
_STREAM = re.compile(rb"stream(\r\n|\r|\n)")
_LENGTH = re.compile(rb"/Length\s+(\d+)")
_N_PAGES = re.compile(rb"/Type\s*/Page[^s]")
_ESCAPES = {
    b"(": b"(", b")": b")", b"\\": b"\\", b"n": b"\n", b"r": b"\r",
    b"t": b"\t", b"b": b"\b", b"f": b"\f",
}


def _unescape_pdf_string(raw: bytes) -> str:
    r"""还原 PDF 字面字符串：处理 \( \) \\ 与 \nnn 八进制；非 ASCII 按 latin-1 保字节。"""
    out = bytearray()
    i = 1  # 跳过开头的 (
    end = len(raw) - 1  # 跳过结尾的 )
    while i < end:
        ch = raw[i:i + 1]
        if ch == b"\\":
            nxt = raw[i + 1:i + 2]
            if nxt in _ESCAPES:
                out += _ESCAPES[nxt]
                i += 2
                continue
            m = re.match(rb"\\([0-7]{1,3})", raw[i:i + 4])
            if m:
                out.append(int(m.group(1), 8) & 0xFF)
                i += 1 + len(m.group(1))
                continue
            i += 2
            continue
        out += raw[i:i + 1]
        i += 1
    return out.decode("latin-1", errors="replace")


def _collect_text_ops(content: bytes) -> list[str]:
    """按出现顺序收集一个内容流里的文本操作数（Tj / TJ / TJ*）。"""
    pieces: list[str] = []
    for block in _BT_ET.finditer(content):
        body = block.group(1)
        events = [(m.start(), "s", m) for m in _TJ_SIMPLE.finditer(body)]
        events += [(m.start(), "a", m) for m in _TJ_ARRAY.finditer(body)]
        events.sort(key=lambda e: e[0])
        for _, kind, m in events:
            raw = m.group(0)
            if kind == "s":
                sm = re.match(_PDF_STRING, raw)
                if sm:
                    pieces.append(_unescape_pdf_string(sm.group(0)) + "\n")  # 每个 Tj 视作一行
            else:
                for sm in re.finditer(_PDF_STRING, raw):
                    pieces.append(_unescape_pdf_string(sm.group(0)))
                pieces.append("\n")  # TJ 数组一般对应一行
        pieces.append("\n")
    return pieces


def _iter_stream_bodies(data: bytes):
    """按 /Length 精确切出内容流；/Length 缺失或解压失败时回退到 endstream 边界（去除尾部 EOL）。"""
    pos = 0
    while True:
        m = _STREAM.search(data, pos)
        if not m:
            return
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            return
        head = data[max(0, m.start() - 800):m.start()]
        lm = _LENGTH.search(head)
        candidates = []
        if lm:
            n = int(lm.group(1))
            if 0 < n <= end - start:
                candidates.append(data[start:start + n])
        fallback = data[start:end]
        fallback = fallback[:-2] if fallback.endswith(b"\r\n") else (fallback[:-1] if fallback.endswith((b"\n", b"\r")) else fallback)
        if fallback and fallback not in candidates:
            candidates.append(fallback)
        for body in candidates:
            yield body
        pos = end + len(b"endstream")


def extract_pdf_text(data: bytes) -> dict:
    """从 PDF 字节流里 best-effort 提取文本层。返回 {text, n_pages, text_layers, streams, method, sha256}。"""
    streams = list(_iter_stream_bodies(data))
    n_pages = len(_N_PAGES.findall(data))
    pieces: list[str] = []
    decompressed = 0
    for raw in streams:
        content = None
        try:
            content = zlib.decompress(raw)
            decompressed += 1
        except zlib.error:
            # 未压缩内容流：只有当它看起来确实含 BT 文本操作时才收
            if b"BT" in raw and b"Tj" in raw:
                content = raw
        if content is None:
            continue
        pieces.extend(_collect_text_ops(content))
    text = "".join(pieces)
    # 归一：行内空格折叠但**保留空行**（空行是重建文本里仅有的段落边界线索）
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(lines).strip("\n") + ("\n" if lines else "")
    return {
        "text": text,
        "n_pages": n_pages,
        "text_layers": bool(text.strip()),
        "streams_total": len(streams),
        "streams_decompressed": decompressed,
        "method": "stdlib-flate-scan",
        "sha256": hashlib.sha256(data).hexdigest(),
        "note": "best-effort 文本层扫描：不做字距/列版面/CID 还原；文本为重建文本，偏移以该文本为准",
    }


# ---------------------------------------------------------------- DOCX
_W_P = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_W_T = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)
_XML_ENTITIES = {"&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&amp;": "&"}


def _xml_unescape(s: str) -> str:
    for k, v in _XML_ENTITIES.items():
        s = s.replace(k, v)
    return s


def extract_docx_text(data: bytes) -> dict:
    """从 .docx 字节里提取段落文本（zipfile 标准库）。"""
    import io
    import zipfile

    paragraphs: list[str] = []
    note = ""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        return {"text": "", "paragraphs": [], "method": "stdlib-docx-xml", "sha256": hashlib.sha256(data).hexdigest(),
                "text_layers": False, "note": f"DOCX 打不开或无 document.xml：{exc}"}
    for pm in _W_P.finditer(xml):
        runs = [_xml_unescape(t) for t in _W_T.findall(pm.group(0))]
        para = "".join(runs).strip()
        if para:
            paragraphs.append(para)
    text = "\n\n".join(paragraphs)
    if not text.strip():
        note = "document.xml 里没有可提取文本（可能是扫描图片贴入）"
    return {"text": text, "paragraphs": paragraphs, "method": "stdlib-docx-xml",
            "sha256": hashlib.sha256(data).hexdigest(), "text_layers": bool(text.strip()), "note": note}


if __name__ == "__main__":  # 手工核查入口：python3 -B pdf_min.py file.pdf
    import sys
    from pathlib import Path

    src = Path(sys.argv[1])
    data = src.read_bytes()
    if src.suffix.lower() == ".docx":
        info = extract_docx_text(data)
    else:
        info = extract_pdf_text(data)
    print(info["method"], "pages=", info.get("n_pages"), "chars=", len(info["text"]), "layers=", info["text_layers"])
    print(info["text"][:400])
