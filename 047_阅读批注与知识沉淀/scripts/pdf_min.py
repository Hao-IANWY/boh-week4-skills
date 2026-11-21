#!/usr/bin/env python3
"""pdf_min.py —— 纯标准库最小 PDF 写入/文本层提取（047 真实文件库用）。

Writer：手写最小 PDF（zlib Flate 压缩内容流，BT/ET + Tm + Tj）。文本一律写成
UTF-16BE 十六进制字符串（合法 PDF 语法，绕开简易字体的内码表问题），因此本模块
写出的 PDF 是「文本层合成件」：提取器可完整 round-trip 读回，但不冒充出版级排版
（无真实字体嵌入/字形度量）。所有案例报告均标注 synthetic。

Reader：扫描全部 `stream ... endstream` → 逐个尝试 zlib 解压 → 解析 BT/ET 内
Tm/Td 定位与 <hex>/(lit)/[TJ] 文本操作 → 记录 (x, y, text) → 按列间距聚类恢复
双栏阅读顺序（并给 two_column 提示）。检测到 /Encrypt 直接拒绝提取（诚实跳过）；
流解压失败逐条记入 errors，绝不抛异常——坏文件也要给出可复核的结构化结果。

确定性：写入内容不含时间戳/随机 ID，同输入逐字节稳定，可做幂等回归。
"""

from __future__ import annotations

import re
import zlib
from pathlib import Path

_DEFAULT_W, _DEFAULT_H = 595.0, 842.0

# --------------------------------------------------------------------------------------
# 写入端
# --------------------------------------------------------------------------------------
def _fmt_num(v) -> str:
    v = float(v)
    return str(int(v)) if v == int(v) else ("%.2f" % v).rstrip("0").rstrip(".")


def _hex16(text: str) -> str:
    return "<" + str(text).encode("utf-16-be").hex().upper() + ">"


def _content_stream(lines) -> bytes:
    parts = []
    for x, y, text in lines:
        parts.append("BT 1 0 0 1 %s %s Tm %s Tj ET" % (_fmt_num(x), _fmt_num(y), _hex16(text)))
    return "\n".join(parts).encode("latin-1")


def layout_page(columns, width: float = _DEFAULT_W, height: float = _DEFAULT_H,
                top: float = 780.0, line_gap: float = 20.0, margin: float = 72.0) -> dict:
    """columns: [左栏行列表, 右栏行列表, ...] → 均分栏宽的行坐标布局。"""
    columns = [list(c) for c in columns]
    n = max(1, len(columns))
    col_w = (width - 2 * margin) / n
    lines = []
    for ci, col in enumerate(columns):
        x = round(margin + ci * col_w, 2)
        y = top
        for text in col:
            lines.append((x, round(y, 2), text))
            y -= line_gap
    return {"lines": lines, "width": width, "height": height}


def write_pdf(path, pages) -> Path:
    """pages: layout_page() 产物列表。输出确定性最小 PDF（无 Info/时间戳）。"""
    pages = list(pages or [])
    objs = {}
    page_nums = [4 + 2 * i for i in range(len(pages))]
    content_nums = [5 + 2 * i for i in range(len(pages))]
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = ("<< /Type /Pages /Kids [%s] /Count %d >>"
               % (" ".join("%d 0 R" % p for p in page_nums), len(pages))).encode("latin-1")
    objs[3] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
               b"/Encoding /WinAnsiEncoding >>")
    for i, pg in enumerate(pages):
        w, h = pg.get("width", _DEFAULT_W), pg.get("height", _DEFAULT_H)
        objs[page_nums[i]] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
                              "/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>"
                              % (_fmt_num(w), _fmt_num(h), content_nums[i])).encode("latin-1")
        raw = _content_stream(pg.get("lines") or [])
        stream = zlib.compress(raw, 6)
        objs[content_nums[i]] = (b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)) + stream + b"\nendstream"

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(buf)
        buf += ("%d 0 obj\n" % num).encode("latin-1") + objs[num] + b"\nendobj\n"
    xref_pos = len(buf)
    count = max(objs) + 1
    buf += ("xref\n0 %d\n" % count).encode("latin-1") + b"0000000000 65535 f \n"
    for num in range(1, count):
        buf += ("%010d 00000 n \n" % offsets[num]).encode("latin-1")
    buf += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (count, xref_pos)).encode("latin-1")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(bytes(buf))
    return p


def mark_encrypted(path, key_hex: str = "00" * 16) -> Path:
    """给已有最小 PDF 打上 /Encrypt 标记（模拟加密件；提取端必须诚实跳过）。"""
    p = Path(path)
    data = p.read_bytes()
    head, sep, tail = data.partition(b"trailer")
    if not sep:
        raise ValueError("mark_encrypted 只支持本模块写出的最小 PDF（缺 trailer）")
    injected = (b"trailer\n<< /Size 99 /Root 1 0 R /Encrypt 99 0 R >>\n"
                b"99 0 obj\n<< /Filter /Standard /V 1 /R 2 /O <" + key_hex.encode()
                + b"> /U <" + key_hex.encode() + b"> /P -1 >>\nendobj\n")
    p.write_bytes(head + injected + tail)
    return p


# --------------------------------------------------------------------------------------
# 读取端
# --------------------------------------------------------------------------------------
_LEN_STREAM = re.compile(rb"<<\s*/Length\s+(\d+)\s*>>\s*stream\r?\n", re.S)
_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\n?endstream", re.S)
_MEDIA = re.compile(rb"MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]")
_TM = re.compile(r"([-\d.]+)\s+([-\d.]+)\s+T[dm]")
_HEX = re.compile(r"<([0-9A-Fa-f\s]*)>\s*Tj")
_LIT = re.compile(r"\((?:\\.|[^\\()])*\)\s*Tj")
_TJ = re.compile(r"\[(.*?)\]\s*TJ", re.S)
_STRING = re.compile(r"<([0-9A-Fa-f\s]*)>|\((?:\\.|[^\\()])*\)")
_ESC = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "(": "(", ")": ")", "\\": "\\"}


def _unescape_lit(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt in _ESC:
                out.append(_ESC[nxt])
                i += 2
                continue
            if nxt.isdigit():
                oct_m = re.match(r"[0-7]{1,3}", raw[i + 1:i + 4])
                out.append(chr(int(oct_m.group(0), 8)))
                i += 1 + len(oct_m.group(0))
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _decode_string(raw: str) -> str:
    """单个 PDF 字符串 token → 文本。hex 按 UTF-16BE（合成件口径），字面量按 UTF-8/latin-1。"""
    if raw.startswith("<"):
        hx = re.sub(r"\s", "", raw[1:-1])
        if not hx:
            return ""
        try:
            return bytes.fromhex(hx).decode("utf-16-be", errors="replace")
        except ValueError:
            return ""
    body = _unescape_lit(raw[1:-1])
    try:
        return body.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return body


_SHOW_TAIL = re.compile(r"\s*(Tj|\]\s*TJ)")


def _parse_content(text: str) -> list:
    """按出现顺序解析文本操作 → [(x, y, text)]。"""
    events = []
    for m in _TM.finditer(text):
        events.append((m.start(), "tm", m.group(1), m.group(2)))
    for m in _STRING.finditer(text):
        if _SHOW_TAIL.match(text[m.end():]):
            events.append((m.start(), "show", _decode_string(m.group(0)), ""))
    events.sort(key=lambda e: e[0])
    runs, x, y = [], 0.0, 0.0
    for _, kind, a, b in events:
        if kind == "tm":
            x, y = float(a), float(b)
        else:
            if a:
                runs.append((x, y, a))
    return runs


def _order_lines(runs: list, width: float) -> tuple:
    """按列聚类恢复阅读顺序；返回 (有序行文本, layout 提示)。"""
    if not runs:
        return [], {"two_column": False}
    xs = sorted({round(r[0], 1) for r in runs})
    split = None
    for a, b in zip(xs, xs[1:]):
        if b - a > max(24.0, width * 0.18):
            split = (a + b) / 2.0
            break
    if split is None:
        ordered = sorted(runs, key=lambda r: (-r[1], r[0]))
        return [r[2] for r in ordered], {"two_column": False}
    left = sorted((r for r in runs if r[0] < split), key=lambda r: (-r[1], r[0]))
    right = sorted((r for r in runs if r[0] >= split), key=lambda r: (-r[1], r[0]))
    return ([r[2] for r in left] + [r[2] for r in right],
            {"two_column": True, "column_split_x": round(split, 2),
             "left_lines": len(left), "right_lines": len(right),
             "read_order": "left column top→bottom, then right column（按 x 间距聚类，启发式）"})


def read_pdf(path) -> dict:
    """提取 PDF 文本层。永不抛异常：坏件/加密件返回结构化 honest 结果。"""
    p = Path(path)
    out = {"ok": False, "encrypted": False, "method": "pdf_min", "confidence": 0.0,
           "pages": [], "errors": [], "warnings": [], "streams_failed": 0, "streams_total": 0}
    try:
        data = p.read_bytes()
    except OSError as exc:
        out["errors"].append(f"文件不可读：{exc}")
        return out
    if not data.startswith(b"%PDF"):
        out["errors"].append("缺 %PDF 头：不是可识别的 PDF（可能截断或换壳）")
        return out
    if b"/Encrypt" in data:
        out["encrypted"] = True
        out["errors"].append("检测到 /Encrypt（加密件）：本工具不做解密，诚实跳过文本提取")
        return out
    page_boxes = [[float(m.group(i)) for i in (1, 2, 3, 4)] for m in _MEDIA.finditer(data)]
    # 流提取优先按 /Length 精确取字节（压缩数据可能偶含 endstream/endobj 字节序列），
    # 无 Length 的外来文件回退到非贪婪正则。
    streams = []
    pos = 0
    while True:
        m = _LEN_STREAM.search(data, pos)
        if not m:
            break
        start = m.end()
        n = int(m.group(1))
        streams.append(data[start:start + n])
        pos = start + n
    if not streams:
        for m in _STREAM.finditer(data):
            streams.append(m.group(1))
    if not streams:
        out["errors"].append("未找到任何内容流：无可提取文本层")
        return out
    out["streams_total"] = len(streams)
    penalty = 0.0
    for si, raw in enumerate(streams):
        try:
            content = zlib.decompress(raw)
        except zlib.error:
            try:
                content = zlib.decompressobj(-15).decompress(raw)
            except zlib.error:
                out["errors"].append(f"内容流 {si + 1} 解压失败（可能损坏/截断），该流跳过")
                out["streams_failed"] += 1
                penalty += 0.25
                streams[si] = []
                continue
        runs = _parse_content(content.decode("latin-1"))
        if not runs:
            out["warnings"].append(f"内容流 {si + 1} 无文本操作（纯图形页）")
        streams[si] = runs
    boxes = page_boxes or [[0.0, 0.0, _DEFAULT_W, _DEFAULT_H]]
    n_pages = max(len([r for r in streams if r is not None]), len(boxes))
    for i in range(n_pages):
        runs = streams[i] if i < len(streams) and isinstance(streams[i], list) else []
        box = boxes[i] if i < len(boxes) else boxes[0]
        w = box[2] - box[0]
        lines, layout = _order_lines(runs, w)
        out["pages"].append({"page": i + 1, "width": round(w, 2),
                             "height": round(box[3] - box[1], 2),
                             "lines": len(lines), "layout": layout,
                             "text": "\n".join(lines)})
    good = [pg for pg in out["pages"] if pg["text"].strip()]
    out["ok"] = bool(good)
    out["confidence"] = round(max(0.05, 1.0 - penalty - 0.02 * len(out["warnings"])), 4)
    if not out["ok"]:
        out["errors"].append("全部内容流均无文本层：按无文本层材料处理（不冒充 OCR）")
    return out


def self_check(tmpdir="/tmp/pdf_min_check") -> int:
    """写入→读回 round-trip 自检（含双栏、中英混排、加密、损坏四种情形）。"""
    import json

    d = Path(tmpdir)
    d.mkdir(parents=True, exist_ok=True)
    zh_en = ["模板无关的方法 top-1 准确率 52.3%", "We propose a new representation (v2)"]
    p1 = layout_page([zh_en, ["Right column line one", "右栏第二行"]], width=612)
    p2 = layout_page([["Single column page 2 内容"]])
    pdf = write_pdf(d / "t.pdf", [p1, p2])
    got = read_pdf(pdf)
    assert got["ok"] and got["encrypted"] is False, got
    flat = "\n".join(pg["text"] for pg in got["pages"])
    for s in zh_en + ["Right column line one", "右栏第二行", "Single column page 2 内容"]:
        assert s in flat, (s, flat)
    assert got["pages"][0]["layout"]["two_column"] is True, got["pages"][0]["layout"]
    assert "模板无关" in got["pages"][0]["text"].split("\n")[0]  # 左栏先于右栏
    enc = mark_encrypted(write_pdf(d / "enc.pdf", [layout_page([["secret"]])]))
    got_enc = read_pdf(enc)
    assert got_enc["encrypted"] is True and not got_enc["pages"] and got_enc["ok"] is False
    raw = (d / "t.pdf").read_bytes()
    (d / "bad.pdf").write_bytes(raw[: int(len(raw) * 0.45)])
    got_bad = read_pdf(d / "bad.pdf")
    assert got_bad["errors"] and got_bad["confidence"] < 1.0, got_bad
    print(json.dumps({"roundtrip": True, "two_column": True, "encrypted_skip": True,
                      "corrupt_reported": True, "ok": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(self_check())
