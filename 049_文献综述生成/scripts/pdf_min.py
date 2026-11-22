#!/usr/bin/env python3
"""pdf_min.py —— 048 最小纯标准库 PDF 文本提取器（自研，离线）。

与 review/048_realtest/build_cases.py 里的最小 PDF writer 配套（zlib FlateDecode +
BT/ET 文本块 + Tj/TJ 操作符），保证同一份合成 PDF 可被本模块无损往返提取，
为「来源正文获取」兜底，不依赖任何第三方库。

支持范围（刻意保持最小）：
  * 未加密 PDF 的常规对象与流对象（含 /FlateDecode，未压缩流原样解析）；
  * BT..ET 文本块；Tj、TJ、' 、" 四种文本显示操作符；
  * PDF 字面串转义：\\n \\r \\t \\b \\f \\( \\) \\\\ 与 \\ddd 八进制；
  * 每个文本显示操作符后补一个换行（配套 writer 每个 Tj 写一行）。
不支持（命中即诚实返回缺口，不猜测内容）：
  * CMap/ToUnicode 自定义编码、ObjStm 压缩对象流、加密（RC4/AES）、扫描图像；
  * 解析失败时返回 error 字段说明原因，text 为空串，绝不伪造正文。
"""
from __future__ import annotations

import zlib

_ESCAPES = {
    0x6E: b"\n",   # \n
    0x72: b"\r",   # \r
    0x74: b"\t",   # \t
    0x62: b"\b",   # \b
    0x66: b"\f",   # \f
    0x28: b"(",    # \(
    0x29: b")",    # \)
    0x5C: b"\\",   # \\
}


def unescape_pdf_string(raw: bytes) -> bytes:
    """把 PDF 字面串的转义序列还原为原始字节（含 \\ddd 八进制）。"""
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c != 0x5C:  # 反斜杠
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            out.append(c)
            break
        nxt = raw[i + 1]
        if nxt in _ESCAPES:
            out += _ESCAPES[nxt]
            i += 2
            continue
        if 0x30 <= nxt <= 0x37:  # 八进制 \ddd，最多 3 位
            j = i + 1
            digits = bytearray()
            while j < n and 0x30 <= raw[j] <= 0x37 and len(digits) < 3:
                digits.append(raw[j])
                j += 1
            out.append(int(digits.decode("ascii"), 8) & 0xFF)
            i = j
            continue
        # \后跟其它字符：PDF 规范按该字符本身处理（换行续行场景略）
        out.append(nxt)
        i += 2
    return bytes(out)


def _iter_streams(data: bytes) -> list:
    """按字节扫描 stream..endstream 原始负载（不依赖对象表）。"""
    streams, pos = [], 0
    while True:
        head = data.find(b"stream", pos)
        if head < 0:
            break
        body = head + 6
        if data[body:body + 2] == b"\r\n":
            body += 2
        elif data[body:body + 1] in (b"\n", b"\r"):
            body += 1
        tail = data.find(b"endstream", body)
        if tail < 0:
            break
        streams.append(data[body:tail].rstrip(b"\r\n"))
        pos = tail + 9
    return streams


def _tokens(content: bytes) -> list:
    """把内容流切成 ("str", bytes) / ("op", str) 词元；括号串按转义与深度配平。"""
    toks, i, n = [], 0, len(content)
    op = bytearray()
    while i < n:
        c = content[i]
        if c == 0x28:  # (
            if op:
                toks.append(("op", op.decode("latin-1")))
                op = bytearray()
            i += 1
            depth, buf = 1, bytearray()
            while i < n and depth:
                ch = content[i]
                if ch == 0x5C and i + 1 < n:  # 转义：连后一起带走（八进制再吃 2 位）
                    buf.append(ch)
                    nxt = content[i + 1]
                    buf.append(nxt)
                    if 0x30 <= nxt <= 0x37:
                        j = i + 2
                        while j < n and 0x30 <= content[j] <= 0x37 and j < i + 4:
                            buf.append(content[j])
                            j += 1
                            i += 1
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                buf.append(ch)
                i += 1
            toks.append(("str", bytes(buf)))
            continue
        if 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A:  # 操作符字母
            op.append(c)
            i += 1
            continue
        if c in (0x27, 0x22):  # ' 和 " 操作符
            if op:
                toks.append(("op", op.decode("latin-1")))
                op = bytearray()
            toks.append(("op", chr(c)))
            i += 1
            continue
        if op:
            toks.append(("op", op.decode("latin-1")))
            op = bytearray()
        i += 1
    if op:
        toks.append(("op", op.decode("latin-1")))
    return toks


_SHOW_OPS = {"Tj", "TJ", "'", '"'}


def extract_pdf_bytes(data: bytes) -> dict:
    """从 PDF 字节流提取文本；返回 {text, n_streams, n_show_ops, error}。"""
    parts, n_streams, n_ops, errors = [], 0, 0, []
    if not data.startswith(b"%PDF"):
        errors.append("head_not_pdf")
    for raw in _iter_streams(data):
        n_streams += 1
        try:
            content = zlib.decompress(raw)
        except zlib.error:
            content = raw  # 未压缩流原样解析
        if b"BT" not in content:
            continue
        pending = None
        for kind, val in _tokens(content):
            if kind == "str":
                pending = val
            elif kind == "op":
                if val in _SHOW_OPS and pending is not None:
                    parts.append(unescape_pdf_string(pending).decode("utf-8", errors="replace"))
                    parts.append("\n")  # 配套 writer 每个显示操作符一行
                    n_ops += 1
                    pending = None
    text = "".join(parts).strip()
    error = ""
    if not text:
        error = "; ".join(errors + (["no_text_extracted"] if n_streams else ["no_stream_found"]))
    return {"text": text, "n_streams": n_streams, "n_show_ops": n_ops, "error": error}


def extract_pdf_file(path) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    return extract_pdf_bytes(data)


# --------------------------------------------------------------------------------------
# 仅 --self-test 使用的最小 writer：与 review/048_realtest/build_cases.py 同构，
# 用于验证「写入 → 提取」往返一致；不作为正式产物写手。
# --------------------------------------------------------------------------------------
def _pdf_escape(line: str) -> bytes:
    out = bytearray()
    for b in line.encode("utf-8"):
        if b in (0x28, 0x29, 0x5C):
            out += b"\\" + bytes([b])
        else:
            out.append(b)
    return bytes(out)


def write_min_pdf_bytes(pages) -> bytes:
    """pages: [[line, ...], ...] → 最小单文件 PDF 字节流（FlateDecode + Tj）。"""
    objs = {}  # obj_num -> bytes

    def content_stream(lines):
        body = b"BT\n/F1 10 Tf\n14 TL\n"
        for line in lines:
            body += b"(" + _pdf_escape(line) + b") Tj\nT*\n"
        body += b"ET"
        comp = zlib.compress(body)
        return b"<< /Length " + str(len(comp)).encode() + b" /Filter /FlateDecode >>\nstream\n" + comp + b"\nendstream"

    page_ids = []
    next_id = 3
    for lines in pages:
        pid, cid = next_id, next_id + 1
        next_id += 2
        page_ids.append(pid)
        objs[pid] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                     f"/Resources << /Font << /F1 {next_id} 0 R >> >> /Contents {cid} 0 R >>").encode()
        objs[cid] = content_stream(lines)
    font_id = next_id
    objs[font_id] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref_at = len(out)
    count = max(objs) + 1
    out += f"xref\n0 {count}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, count):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def self_test() -> int:
    import json

    lines = [
        "合成来源手册（synthetic source handbook）",
        "Retrosynthesis accuracy: 52.3% on USPTO-50k.",
        "括号转义与反斜杠 \\ (ok) (nested (deeper)) 端",
        "八进制 \101\102 与中文：石墨负极首次库仑效率 91.5%。",
    ]
    pdf = write_min_pdf_bytes([lines, ["second page: DTD 89.2%."]])
    got = extract_pdf_bytes(pdf)
    expect = "\n".join(lines) + "\nsecond page: DTD 89.2%."
    ok = got["text"] == expect and not got["error"]
    print(json.dumps({"self_test": "PASS" if ok else "FAIL",
                      "extracted_chars": len(got["text"]), "error": got["error"]}, ensure_ascii=False))
    return 0 if ok else 1


def main(argv=None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "--self-test":
        return self_test()
    got = extract_pdf_file(argv[0])
    import json

    print(json.dumps({"chars": len(got["text"]), "ops": got["n_show_ops"], "error": got["error"],
                      "preview": got["text"][:200]}, ensure_ascii=False))
    return 0 if got["text"] else 2


if __name__ == "__main__":
    import sys

    sys.exit(main())
