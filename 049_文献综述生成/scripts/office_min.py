"""office_min.py —— 纯标准库写最小可用的 OOXML（.pptx / .docx / .xlsx）。

用途：评测环境不一定装 python-pptx / python-docx，但题面要求"可编辑 Office 产物"。
本模块用 zipfile + 手写 PresentationML / WordprocessingML / SpreadsheetML，
产出**结构合法、可被 Office / LibreOffice 打开编辑**的最小文件；若运行环境已装
python-pptx / python-docx / openpyxl，脚本可优先走官方库（本模块作为降级路径）。

约定：
    write_pptx(path, slides)   slides = [{"title": str, "bullets": [str], "notes": str}]
    write_docx(path, blocks)   blocks = [{"type": "h1|h2|p|bullet|table", ...}]
    write_xlsx(path, sheets)   sheets = [{"name": str, "rows": [[cell, ...], ...]}]
    read_pptx_text(path)       读回文本，用于自检（不依赖第三方库）

全部输出为确定性内容（不含时间戳/随机 ID），便于回归比对。
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
EMU_PER_INCH = 914400


def _write_zip(path, parts: dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(parts):
            zf.writestr(name, parts[name])
    return p


def _rels(entries) -> str:
    body = "".join(
        f'<Relationship Id="{rid}" Type="{typ}" Target="{target}"/>' for rid, typ, target in entries
    )
    return (
        XML_HEAD
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + body
        + "</Relationships>"
    )


# --------------------------------------------------------------------------------------
# PPTX
# --------------------------------------------------------------------------------------
_THEME = f"""{XML_HEAD}<a:theme xmlns:a="{NS_A}" name="SkillTheme">
<a:themeElements>
<a:clrScheme name="Skill"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
<a:dk2><a:srgbClr val="1F3864"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>
<a:accent1><a:srgbClr val="2E74B5"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2>
<a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4>
<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6>
<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>
<a:fontScheme name="Skill"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>
<a:fmtScheme name="Skill"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>
<a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>
<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>
<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>
<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>
<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>
<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>
</a:themeElements></a:theme>"""


def _tx_body(lines, size_pt: int = 20, bold: bool = False, indent: bool = False) -> str:
    paras = []
    for line in lines or [""]:
        ppr = '<a:pPr marL="285750" lvl="0"><a:buChar char="•"/></a:pPr>' if indent else "<a:pPr/>"
        run = (
            f'<a:r><a:rPr lang="zh-CN" sz="{int(size_pt * 100)}" b="{1 if bold else 0}" dirty="0"/>'
            f"<a:t>{escape(str(line))}</a:t></a:r>"
        )
        paras.append(f"<a:p>{ppr}{run}</a:p>")
    return (
        '<p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720">'
        "</a:bodyPr><a:lstStyle/>" + "".join(paras) + "</p:txBody>"
    )


def _sp(shape_id: int, name: str, x, y, cx, cy, body: str) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/><p:cNvSpPr txBox="1"/>'
        f"<p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x=\"{int(x)}\" y=\"{int(y)}\"/>"
        f'<a:ext cx="{int(cx)}" cy="{int(cy)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f"<a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>{body}</p:sp>"
    )


def _slide_xml(slide: dict) -> str:
    title = str(slide.get("title", "") or "")
    bullets = [str(b) for b in (slide.get("bullets") or [])]
    notes = str(slide.get("notes", "") or "")
    shapes = [
        _sp(2, "Title", 838200, 365125, 10515600, 1325563, _tx_body([title], size_pt=32, bold=True)),
        _sp(3, "Body", 838200, 1825625, 10515600, 4351338, _tx_body(bullets or [""], size_pt=20, indent=bool(bullets))),
    ]
    xml = (
        XML_HEAD
        + f'<p:sld xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"><p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
        '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "".join(shapes)
        + "</p:spTree></p:cSld>"
        + ('<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>')
        + "</p:sld>"
    )
    return xml, notes


def write_pptx(path, slides, title: str = "Generated deck") -> Path:
    slides = list(slides or [])
    if not slides:
        slides = [{"title": title, "bullets": ["（无内容）"]}]
    parts = {}
    n = len(slides)

    overrides = "".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, n + 1)
    )
    parts["[Content_Types].xml"] = (
        XML_HEAD
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        + '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
        + '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
        + '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
        + '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>'
        + '<Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>'
        + '<Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>'
        + overrides
        + "</Types>"
    )
    parts["_rels/.rels"] = _rels(
        [
            ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", "ppt/presentation.xml"),
        ]
    )
    parts["ppt/presentation.xml"] = (
        XML_HEAD
        + f'<p:presentation xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}" saveSubsetFonts="1">'
        + '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        + "<p:sldIdLst>"
        + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, n + 1))
        + "</p:sldIdLst>"
        + f'<p:sldSz cx="{int(10 * EMU_PER_INCH)}" cy="{int(7.5 * EMU_PER_INCH)}"/>'
        + f'<p:notesSz cx="{int(6.858 * EMU_PER_INCH)}" cy="{int(9.144 * EMU_PER_INCH)}"/>'
        + "</p:presentation>"
    )
    pres_rels = [
        ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster", "slideMasters/slideMaster1.xml"),
    ]
    for i in range(1, n + 1):
        pres_rels.append(("rId%d" % (i + 1), "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", f"slides/slide{i}.xml"))
    pres_rels += [
        (f"rId{n + 2}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps", "presProps.xml"),
        (f"rId{n + 3}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/viewProps", "viewProps.xml"),
        (f"rId{n + 4}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles", "tableStyles.xml"),
        (f"rId{n + 5}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme", "theme/theme1.xml"),
    ]
    parts["ppt/_rels/presentation.xml.rels"] = _rels(pres_rels)

    parts["ppt/presProps.xml"] = XML_HEAD + f'<p:presentationPr xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"/>'
    parts["ppt/viewProps.xml"] = XML_HEAD + f'<p:viewPr xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"/>'
    parts["ppt/tableStyles.xml"] = (
        XML_HEAD
        + f'<a:tblStyleLst xmlns:a="{NS_A}" def="{{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}}"/>'
    )
    parts["ppt/theme/theme1.xml"] = _THEME

    parts["ppt/slideMasters/slideMaster1.xml"] = (
        XML_HEAD
        + f'<p:sldMaster xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"><p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        "</p:spTree></p:cSld>"
        + '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3"'
        + ' accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        + '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
        + "<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz=\"3200\" b=\"1\"/></a:lvl1pPr></p:titleStyle>"
        + '<p:bodyStyle><a:lvl1pPr><a:defRPr sz="2000"/></a:lvl1pPr></p:bodyStyle>'
        + '<p:otherStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:otherStyle></p:txStyles>'
        + "</p:sldMaster>"
    )
    parts["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _rels(
        [
            ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme", "../theme/theme1.xml"),
        ]
    )
    parts["ppt/slideLayouts/slideLayout1.xml"] = (
        XML_HEAD
        + f'<p:sldLayout xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}" type="blank" preserve="1">'
        + "<p:cSld name=\"Blank\"><p:spTree>"
        + '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        + '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        + "</p:spTree></p:cSld>"
        + "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
    )
    parts["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _rels(
        [("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster", "../slideMasters/slideMaster1.xml")]
    )

    for i, slide in enumerate(slides, start=1):
        xml, _notes = _slide_xml(slide)
        parts[f"ppt/slides/slide{i}.xml"] = xml
        parts[f"ppt/slides/_rels/slide{i}.xml.rels"] = _rels(
            [("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout", "../slideLayouts/slideLayout1.xml")]
        )
    return _write_zip(path, parts)


def read_pptx_text(path) -> list:
    """不依赖第三方库读回每页文本，用于自检。"""
    out = []
    with zipfile.ZipFile(path) as zf:
        names = sorted(
            (n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
            key=lambda s: int(re.search(r"(\d+)", s).group(1)),
        )
        for n in names:
            xml = zf.read(n).decode("utf-8")
            out.append([escape_text(t) for t in re.findall(r"<a:t>(.*?)</a:t>", xml, flags=re.S)])
    return out


def escape_text(s: str) -> str:
    return (
        s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")
    )


# --------------------------------------------------------------------------------------
# DOCX
# --------------------------------------------------------------------------------------
def _w_run(text: str, bold: bool = False, size_half_pt: int = 24) -> str:
    rpr = f'<w:rPr>{"<w:b/>" if bold else ""}<w:sz w:val="{size_half_pt}"/><w:szCs w:val="{size_half_pt}"/></w:rPr>'
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(str(text))}</w:t></w:r>'


def _w_para(text: str, style: str | None = None, bold: bool = False, bullet: bool = False, size: int = 24) -> str:
    ppr = "<w:pPr>"
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if bullet:
        ppr += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
    ppr += f'<w:spacing w:after="120"/></w:pPr>'
    return f"<w:p>{ppr}{_w_run(text, bold=bold, size_half_pt=size)}</w:p>"


def _w_table(rows) -> str:
    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>']
    for r in rows:
        out.append("<w:tr>" + "".join(f"<w:tc><w:tcPr/>{_w_para(str(c))}</w:tc>" for c in r) + "</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def write_docx(path, blocks, title: str = "") -> Path:
    body = []
    if title:
        body.append(_w_para(title, style="Heading1", bold=True, size=36))
    for b in blocks or []:
        t = (b.get("type") or "p").lower()
        if t in ("h1", "h2", "h3"):
            body.append(_w_para(b.get("text", ""), style=f"Heading{int(t[1])}", bold=True, size=32 if t == "h1" else 28))
        elif t == "bullet":
            body.append(_w_para(b.get("text", ""), bullet=True))
        elif t == "table":
            body.append(_w_table(b.get("rows") or []))
            body.append(_w_para(""))
        else:
            body.append(_w_para(b.get("text", "")))
    parts = {}
    parts["[Content_Types].xml"] = (
        XML_HEAD
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        + '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        + '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        + "</Types>"
    )
    parts["_rels/.rels"] = _rels(
        [("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", "word/document.xml")]
    )
    parts["word/document.xml"] = (
        XML_HEAD
        + f'<w:document xmlns:w="{NS_W}" xmlns:r="{NS_R}"><w:body>'
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>'
        + "</w:body></w:document>"
    )
    parts["word/_rels/document.xml.rels"] = _rels(
        [
            ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles", "styles.xml"),
            ("rId2", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering", "numbering.xml"),
        ]
    )
    styles = [f'{XML_HEAD}<w:styles xmlns:w="{NS_W}">']
    for i, name in ((1, "heading 1"), (2, "heading 2"), (3, "heading 3")):
        styles.append(
            f'<w:style w:type="paragraph" w:styleId="Heading{i}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
            f'<w:pPr><w:outlineLvl w:val="{i - 1}"/></w:pPr><w:rPr><w:b/><w:sz w:val="{36 - i * 4}"/></w:rPr></w:style>'
        )
    styles.append(
        '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:sz w:val="24"/></w:rPr></w:style>'
        '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
        '<w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/>'
        '<w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/>'
        "</w:tblBorders></w:tblPr></w:style>"
    )
    styles.append("</w:styles>")
    parts["word/styles.xml"] = "".join(styles)
    parts["word/numbering.xml"] = (
        XML_HEAD
        + f'<w:numbering xmlns:w="{NS_W}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0">'
        + '<w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
        + '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
        + '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    )
    return _write_zip(path, parts)


def read_docx_text(path) -> list:
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    return [escape_text(t) for t in re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, flags=re.S)]


# --------------------------------------------------------------------------------------
# XLSX
# --------------------------------------------------------------------------------------
def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, sheets) -> Path:
    sheets = list(sheets or [])
    if not sheets:
        sheets = [{"name": "Sheet1", "rows": []}]
    parts = {}
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1)
    )
    parts["[Content_Types].xml"] = (
        XML_HEAD
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        + overrides
        + "</Types>"
    )
    parts["_rels/.rels"] = _rels(
        [("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", "xl/workbook.xml")]
    )
    strings, index, ss_rows = [], {}, []
    for sh in sheets:
        for row in sh.get("rows") or []:
            for cell in row:
                key = str(cell)
                if key not in index:
                    index[key] = len(strings)
                    strings.append(key)
    for i, sh in enumerate(sheets, start=1):
        rows_xml = []
        for r_i, row in enumerate(sh.get("rows") or [], start=1):
            cells = []
            for c_i, cell in enumerate(row):
                v = str(cell)
                if re.fullmatch(r"-?\d+(\.\d+)?", v):
                    cells.append(f'<c r="{_col(c_i)}{r_i}"><v>{v}</v></c>')
                else:
                    cells.append(f'<c r="{_col(c_i)}{r_i}" t="s"><v>{index[v]}</v></c>')
            rows_xml.append(f'<row r="{r_i}">' + "".join(cells) + "</row>")
        parts[f"xl/worksheets/sheet{i}.xml"] = (
            XML_HEAD
            + f'<worksheet xmlns="{NS_S}"><sheetData>' + "".join(rows_xml) + "</sheetData></worksheet>"
        )
    parts["xl/workbook.xml"] = (
        XML_HEAD
        + f'<workbook xmlns="{NS_S}" xmlns:r="{NS_R}"><sheets>'
        + "".join(
            f'<sheet name="{escape(sh.get("name", f"Sheet{i}"))}" sheetId="{i}" r:id="rId{i}"/>'
            for i, sh in enumerate(sheets, start=1)
        )
        + "</sheets></workbook>"
    )
    parts["xl/_rels/workbook.xml.rels"] = _rels(
        [(f"rId{i}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", f"worksheets/sheet{i}.xml")
         for i in range(1, len(sheets) + 1)]
        + [(f"rId{len(sheets) + 1}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings", "sharedStrings.xml")]
    )
    parts["xl/sharedStrings.xml"] = (
        XML_HEAD
        + f'<sst xmlns="{NS_S}" count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in strings)
        + "</sst>"
    )
    return _write_zip(path, parts)


def self_check(tmpdir="/tmp/office_min_check") -> int:
    import json

    d = Path(tmpdir)
    d.mkdir(parents=True, exist_ok=True)
    pptx = write_pptx(d / "t.pptx", [{"title": "标题一", "bullets": ["要点 A", "要点 B"]}, {"title": "标题二", "bullets": []}])
    docx = write_docx(d / "t.docx", [{"type": "h1", "text": "一级"}, {"type": "p", "text": "正文"},
                                     {"type": "table", "rows": [["a", "b"], ["1", "2"]]}])
    xlsx = write_xlsx(d / "t.xlsx", [{"name": "Sheet1", "rows": [["指标", "值"], ["acc", 0.9]]}])
    ppt_text = read_pptx_text(pptx)
    doc_text = read_docx_text(docx)
    assert "标题一" in ppt_text[0] and "要点 B" in ppt_text[0], ppt_text
    assert "一级" in doc_text and "正文" in doc_text, doc_text
    for f in (pptx, docx, xlsx):
        with zipfile.ZipFile(f) as zf:
            assert zf.testzip() is None
    print(json.dumps({"pptx": str(pptx), "docx": str(docx), "xlsx": str(xlsx), "ok": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(self_check())
