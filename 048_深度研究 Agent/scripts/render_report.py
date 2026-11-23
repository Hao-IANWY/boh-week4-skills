#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 result.json 渲染成人类可读的 report.md（纯标准库、确定性）。

定位：result.json 是机器契约与证据台账；report.md 是同一批结构化字段的
可读投影，供评审或科研人员直接阅读。报告不新增任何未经证据支持的主张，
所有小节只重排 result.json 中已有的结论、证据行、冲突与缺口。

用法：
    python3 -B scripts/render_report.py --result /app/output/result.json \
        --out /app/output/report.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


# --------------------------------------------------------------------------------------
# 基础格式化
# --------------------------------------------------------------------------------------
def s(value, default=""):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def clip(text, n=240):
    text = " ".join(s(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def cell(text):
    return clip(text, 320).replace("|", "\\|")


def table(headers, rows):
    if not rows:
        return "_（无）_\n"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(cell(x) for x in row) + " |")
    return "\n".join(out) + "\n"


def ids(value):
    if not value:
        return "—"
    if isinstance(value, str):
        return value
    return ", ".join(s(x) for x in value)


def get(d, *keys, default=None):
    cur = d
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def status_line(payload):
    st = payload.get("status") or payload.get("状态说明") or {}
    overall = s(st.get("overall") or st.get("状态"), "unknown")
    return overall, st


# --------------------------------------------------------------------------------------
# 通用段落
# --------------------------------------------------------------------------------------
def header(payload, title):
    skill = payload.get("skill") or {}
    overall, st = status_line(payload)
    lines = [
        f"# {title}",
        "",
        f"- **案例标识**：`{s(payload.get('case_id'), 'UNKNOWN-CASE')}`",
        f"- **Skill**：`{s(skill.get('name'), 'unknown')}` v{s(skill.get('version'), '0.0.0')}",
        f"- **运行状态**：`{overall}`",
        f"- **生成时间**：{s(payload.get('generated_at'), '—')}",
        "",
        "> 本报告由 Skill 的确定性管线从 `result.json` 投影生成；结论、引文与定位均取自同一批结构化字段，不在此处新增主张。",
        "",
    ]
    return "\n".join(lines)


def screening_block(payload):
    scr = payload.get("screening") or payload.get("screening_log") or {}
    if not scr:
        return ""
    counts = scr.get("counts") or {}
    lines = ["## 2. 检索与筛选", ""]
    proto = s(scr.get("protocol_status"), "unknown")
    lines.append(f"- **协议状态**：`{proto}`" + (f"（协议编号 `{s(scr.get('protocol_id'))}`）" if scr.get("protocol_id") else ""))
    if scr.get("order_note"):
        lines.append(f"- **执行顺序**：{s(scr.get('order_note'))}")
    if scr.get("policy"):
        lines.append(f"- **判定口径**：{s(scr.get('policy'))}")
    if counts:
        lines.append("")
        rows = [[k, s(v)] for k, v in counts.items()]
        lines.append(table(["计数项", "值"], rows))
    if scr.get("criteria"):
        lines.append("### 纳排条款")
        lines.append("")
        lines.append(table(["条款", "阶段", "判据", "标签"],
                           [[s(c.get("id")), s(c.get("stage")), s(c.get("type")), s(c.get("label"))] for c in scr.get("criteria") or []]))
    rows = []
    if scr.get("records"):
        for rec in (scr.get("records") or [])[:60]:
            rows.append([
                s(rec.get("record_id")),
                s(rec.get("title")),
                s(rec.get("year")),
                s(rec.get("outcome")),
                s(rec.get("reason") or rec.get("reason_code")),
            ])
        if rows:
            lines.append("### 逐条筛选记录（前 60 条）")
            lines.append("")
            lines.append(table(["记录", "题名", "年份", "结论", "理由"], rows))
    elif scr.get("rows"):
        for rec in (scr.get("rows") or [])[:60]:
            decisions = rec.get("stage_decisions") or []
            summary = "；".join(f"{d.get('stage')}:{d.get('decision')}" for d in decisions)
            rows.append([s(rec.get("record_id")), s(rec.get("title")), s(rec.get("year")), summary])
        if rows:
            lines.append("### 逐条两轮筛选（前 60 条）")
            lines.append("")
            lines.append(table(["记录", "题名", "年份", "阶段判定"], rows))
    return "\n".join(lines) + "\n"


def evidence_block(payload, limit=40):
    rows_in = payload.get("evidence_table") or payload.get("evidence_matrix") or []
    if not rows_in:
        return ""
    lines = ["## 6. 证据台账（节选）", "",
             "每条证据行保留记录标识、来源文件、定位与逐字引文；完整台账见 `result.json`。", ""]
    rows = []
    for e in rows_in[:limit]:
        span = e.get("char_span") or {}
        loc = ""
        if isinstance(span, dict) and span:
            loc = f"{s(span.get('start'))}:{s(span.get('end'))}"
        elif e.get("page"):
            loc = f"p.{s(e.get('page'))}"
        rows.append([
            s(e.get("evidence_id") or e.get("id")),
            s(e.get("record_id")),
            s(e.get("year")),
            s(e.get("source_file") or e.get("source")),
            loc or s(e.get("location")),
            clip(e.get("quote") or e.get("text") or "", 180),
        ])
    lines.append(table(["证据", "记录", "年份", "来源", "定位", "原文引文"], rows))
    if len(rows_in) > limit:
        lines.append(f"_台账共 {len(rows_in)} 行，此处节选前 {limit} 行。_")
        lines.append("")
    return "\n".join(lines) + "\n"


def conflict_block(payload):
    conf = payload.get("conflicts")
    items = conf.get("items") if isinstance(conf, dict) else conf
    items = items or []
    lines = ["## 4. 冲突证据（双列呈现）", ""]
    if not items:
        lines.append("本轮未检出同主题方向或数值冲突；该结论仅表示在给定材料与口径下未检出，不表示不存在冲突。")
        lines.append("")
        return "\n".join(lines)
    rows = []
    for c in items:
        a = c.get("side_a") or {}
        b = c.get("side_b") or {}
        rows.append([
            s(c.get("conflict_id")),
            s(c.get("type")),
            f"{s(a.get('evidence_id'))}：{clip(a.get('quote'), 140)}",
            f"{s(b.get('evidence_id'))}：{clip(b.get('quote'), 140)}",
            s(c.get("resolution") or get(conf, "resolution") if isinstance(conf, dict) else ""),
        ])
    lines.append(table(["编号", "类型", "一侧证据", "另一侧证据", "处置"], rows))
    lines.append("冲突不取一方、不合并为平滑叙事；两侧原文与定位均保留在证据台账中。")
    lines.append("")
    return "\n".join(lines)


def gap_block(payload):
    gaps = payload.get("research_gaps")
    items = gaps.get("items") if isinstance(gaps, dict) else gaps
    items = items or []
    lines = ["## 5. 研究空白", ""]
    if not items:
        lines.append("给定材料与检索范围内未形成零证据或弱证据空白条目；该结论不外推到材料之外的文献空间。")
        lines.append("")
        return "\n".join(lines)
    rows = []
    for g in items:
        rows.append([
            s(g.get("subquestion_id") or g.get("topic_id") or g.get("gap_id")),
            s(g.get("code") or g.get("type")),
            s(g.get("missing_evidence") or g.get("detail") or g.get("statement")),
            s(g.get("plan_id")),
        ])
    lines.append(table(["子问题/主题", "类型", "缺口内容", "对应检索计划"], rows))
    lines.append("空白只从“零证据/仅弱证据”的检索缺口推导，不把“未检索到”写成“不存在”。")
    lines.append("")
    return "\n".join(lines)


def status_block(payload):
    overall, st = status_line(payload)
    lines = ["## 7. 状态、局限与不确定性", "", f"- **总体状态**：`{overall}`"]
    completed = st.get("completed") or []
    unmet = st.get("unmet") or []
    if completed:
        lines.append("- **已完成**：" + "；".join(clip(x, 160) for x in completed))
    if unmet:
        lines.append("- **未完成/缺口**：")
        for x in unmet:
            if isinstance(x, dict):
                lines.append(f"  - {s(x.get('item'))}：{s(x.get('reason'))}")
            else:
                lines.append(f"  - {s(x)}")
    notes = st.get("notes") or []
    for n in notes:
        lines.append(f"- {s(n)}")
    unc = payload.get("uncertainty") or payload.get("不确定性") or []
    if unc:
        lines.append("")
        lines.append("**不确定性登记**")
        lines.append("")
        rows = []
        for u in unc:
            if isinstance(u, dict):
                rows.append([s(u.get("item") or u.get("id")), s(u.get("reason") or u.get("detail"))])
            else:
                rows.append(["—", s(u)])
        lines.append(table(["条目", "说明"], rows))
    lines.append("")
    return "\n".join(lines)


def reproduce_block(payload):
    ev = payload.get("evidence") or {}
    n_tools = len(get(ev, "execution", "tool_calls", default=[]) or [])
    n_sources = len(ev.get("sources_manifest") or [])
    backlink = ev.get("backlink") or {}
    lines = [
        "## 8. 复现信息",
        "",
        f"- 来源文件清单：{n_sources} 项；工具调用留痕：{n_tools} 条。",
        f"- 回链覆盖率：{s(backlink.get('coverage'), '见 result.json')}；未解析引用：{ids(backlink.get('unresolved_ids'))}。",
        "- 计划与协议：`plan.json`（先于筛选落盘，可由第三方按 mtime 与内容复核）。",
        "- 机器可读结果：`result.json`（含全部契约字段、证据定位与状态）。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# 各题主结果
# --------------------------------------------------------------------------------------
def body_048(payload):
    plan = payload.get("research_plan") or {}
    lines = ["## 1. 研究问题与拆解", ""]
    q = plan.get("question") or {}
    lines.append(f"- **研究问题**：{s(q.get('text') if isinstance(q, dict) else q)}")
    lines.append(f"- **拆解来源**：`{s(plan.get('decomposition_source'))}`")
    lines.append("")
    rows = []
    for sq in plan.get("subquestions") or []:
        rows.append([s(sq.get("subquestion_id") or sq.get("id")), s(sq.get("text")), ids(sq.get("depends_on")), ids(sq.get("keys"))])
    lines.append(table(["子问题", "内容", "依赖", "检索键"], rows))
    lines.append("## 3. 结论与证据")
    lines.append("")
    for c in payload.get("conclusions") or []:
        lines.append(f"### {s(c.get('conclusion_id'))}（{s(c.get('subquestion_id'))}）")
        lines.append("")
        lines.append(s(c.get("statement")))
        lines.append("")
        lines.append(f"- 支持强度：`{s(c.get('support_level'))}`；证据：{ids(c.get('evidence_ids'))}；冲突：{ids(c.get('conflict_ids'))}")
        if c.get("boundary"):
            lines.append(f"- 适用边界：{s(c.get('boundary'))}")
        lines.append("")
    props = payload.get("proposals") or {}
    items = props.get("items") if isinstance(props, dict) else props
    if items:
        lines.append("## 6. 受约束的研究建议")
        lines.append("")
        for p in items:
            lines.append(f"- **{s(p.get('proposal_id'))}**（{s(p.get('kind'))}）：{s(p.get('statement'))}")
        lines.append("")
    return "\n".join(lines)


def body_049(payload):
    lines = ["## 1. 综述范围与主题", ""]
    draft = payload.get("draft") or {}
    topics = payload.get("topics") or []
    rows = []
    for t in topics:
        rows.append([s(t.get("topic_id")), s(t.get("label")), s(t.get("support_count")), s(t.get("support_level")), "是" if t.get("thin") else "否"])
    lines.append(table(["主题", "标签", "支撑记录", "证据强度", "证据薄弱"], rows))
    lines.append("## 3. 综述草稿")
    lines.append("")
    body = s(draft.get("body"))
    if body.strip():
        lines.append(body.strip())
    else:
        for sec in draft.get("sections") or []:
            lines.append(f"### {s(sec.get('title'))}")
            lines.append("")
            lines.append(s(sec.get("section_text")))
            lines.append("")
    lines.append("")
    cov = payload.get("coverage") or {}
    if cov.get("statement_text"):
        lines.append("### 覆盖边界")
        lines.append("")
        lines.append(s(cov.get("statement_text")))
        lines.append("")
    return "\n".join(lines)


def _levels_text(value):
    if isinstance(value, dict):
        parts = [f"{k} {v}" for k, v in value.items() if v not in (None, "")]
        return "；".join(parts) if parts else "—"
    return s(value, "—")


def _certainty_text(value):
    if not isinstance(value, dict):
        return s(value, "—")
    bits = []
    if value.get("backing_records") is not None:
        bits.append(f"支撑记录 {value.get('backing_records')} 条")
    if value.get("quality_levels"):
        bits.append("质量分布 " + _levels_text(value.get("quality_levels")))
    if value.get("not_assessable_records"):
        bits.append("无法分级 " + ids(value.get("not_assessable_records")))
    return "；".join(bits) if bits else "见 result.json"


def body_050(payload):
    lines = ["## 1. 检索式与研究问题", ""]
    ss = payload.get("search_strategy") or {}
    q = ss.get("question")
    lines.append(f"- **研究问题**：{s(q.get('text') if isinstance(q, dict) else q)}")
    lines.append(f"- **检索式**：`{s(ss.get('expression'))}`")
    lines.append(f"- **数据库/来源**：{ids(ss.get('databases'))}；限制：{s(ss.get('limits'))}")
    lines.append("")
    pf = payload.get("prisma_flow") or {}
    fc = pf.get("flow_counts") or {}
    if fc:
        lines.append("### PRISMA 计数链")
        lines.append("")
        lines.append(table(["阶段", "计数"], [[k, s(v)] for k, v in fc.items()]))
        cons = pf.get("consistency") or {}
        checks = cons.get("checks") or []
        n_ok = sum(1 for c in checks if c.get("ok"))
        closed = cons.get("all_closed")
        if closed is None:
            closed = cons.get("ok")
        if closed is True:
            lines.append(f"- 恒等式自检：全部通过（{n_ok}/{len(checks)} 条）。")
        elif closed is False:
            lines.append(f"- 恒等式自检：未全部通过（{n_ok}/{len(checks)} 条），见 `result.json`。")
        else:
            lines.append(f"- 恒等式自检：{n_ok}/{len(checks)} 条通过，见 `result.json`。")
        lines.append("")
    lines.append("## 3. 综合结论")
    lines.append("")
    for k in payload.get("synthesis") or []:
        lines.append(f"### {s(k.get('conclusion_id'))}（主题 {s(k.get('theme_id'))}）")
        lines.append("")
        lines.append(s(k.get("statement")))
        lines.append("")
        lines.append(f"- 支持强度：`{s(k.get('support_level'))}`；确定性：{_certainty_text(k.get('certainty'))}；证据：{ids(k.get('evidence_ids'))}")
        lines.append("")
    qa = payload.get("quality_appraisal") or {}
    if qa.get("overall"):
        lines.append("### 质量评价")
        lines.append("")
        lines.append("纳入研究的总体质量分布：" + _levels_text(qa.get("overall")) + "。未报告条目不计入低质量，已在各结论的确定性说明中列明。")
        lines.append("")
    return "\n".join(lines)


def body_051(payload):
    lines = ["## 1. 时间趋势", ""]
    tt = payload.get("temporal_trends") or {}
    window = tt.get("window") or {}
    lines.append(f"- **统计窗口**：{s(window.get('start'))}–{s(window.get('end'))}；计数单位：{s(tt.get('counting_unit'))}")
    series = tt.get("primary_series") or []
    if series:
        lines.append("")
        lines.append(table(["年份", "产出量"], [[s(x.get("year")), s(x.get("count"))] for x in series]))
    lines.append("## 3. 前沿与主题演化")
    lines.append("")
    for c in (payload.get("frontier_trends") or {}).get("claims") or []:
        lines.append(f"- **{s(c.get('claim_id'))}｜{s(c.get('topic_label') or c.get('topic'))}**：`{s(c.get('verdict'))}`。{s(c.get('claim'))}")
    lines.append("")
    reps = payload.get("representative_works") or {}
    if reps.get("representatives"):
        lines.append("### 各阶段代表性工作")
        lines.append("")
        rows = []
        for r in reps.get("representatives") or []:
            rows.append([s(r.get("period") or r.get("period_id")), s(r.get("title")), s(r.get("year")), s(r.get("criteria") or r.get("selection_basis"))])
        lines.append(table(["阶段", "题名", "年份", "入选口径"], rows))
    return "\n".join(lines)


def body_052(payload):
    lines = ["## 1. 候选方向与可检验问题", ""]
    for c in payload.get("candidate_assessments") or []:
        ps = c.get("problem_statement") or {}
        lines.append(f"### {s(c.get('direction_id'))}｜{s(c.get('title'))}")
        lines.append("")
        lines.append(f"- 对象：{s(ps.get('object'))}；关键变量：{ids(ps.get('key_variables'))}")
        lines.append(f"- 比较基线：{s(ps.get('baseline'))}；预期结论：{s(ps.get('expected_conclusion'))}")
        lines.append(f"- 可证伪条件：{s(ps.get('falsification_condition'))}")
        lines.append("")
    lines.append("## 3. 创新性、可行性与风险")
    lines.append("")
    for c in payload.get("candidate_assessments") or []:
        nov = c.get("novelty") or {}
        fea = c.get("feasibility") or {}
        val = c.get("value") or {}
        risk = c.get("risk") or {}
        lines.append(f"### {s(c.get('direction_id'))}｜{s(c.get('title'))}")
        lines.append("")
        nov_verdict = nov.get("verdict")
        if not nov_verdict and isinstance(nov.get("verdict_counts"), dict):
            nov_verdict = "；".join(f"{k} {v}" for k, v in nov["verdict_counts"].items())
        label = "判定分布" if isinstance(nov.get("verdict_counts"), dict) else "判定"
        lines.append(f"- **新颖性**：范围 `{s(nov.get('scope'))}`；{label} {s(nov_verdict if nov_verdict is not None else nov.get('score'))}。{s(nov.get('scope_statement'))}")
        premises = fea.get("premises") or []
        if premises:
            supported = [p for p in premises if p.get("supported")]
            unsupported = [p for p in premises if not p.get("supported")]
            lines.append(f"- **可行性**：{len(supported)}/{len(premises)} 项前提有来源支持"
                         + (f"；未满足前提：{ids([p.get('label') for p in unsupported])}" if unsupported else "；未发现未满足前提"))
        else:
            lines.append(f"- **可行性**：{s(fea.get('verdict') or fea.get('conclusion') or fea.get('statement'), '见 result.json')}")
        if isinstance(val, dict) and val.get("score") is not None:
            lines.append(f"- **价值**：{s(val.get('score'))}（{s(val.get('formula'), '口径见 result.json')}）")
        else:
            lines.append(f"- **价值**：{s(val.get('verdict') or val.get('statement') or val.get('score'), '见 result.json')}")
        items = risk.get("items") or []
        if isinstance(risk, dict) and risk.get("score") is not None:
            lines.append(f"- **风险**：{s(risk.get('score'))}；主要条目：" + ("；".join(clip(x.get('risk'), 90) for x in items[:2]) if items else "无登记条目"))
        else:
            lines.append(f"- **风险**：{s(risk.get('verdict') or risk.get('statement'), '见 result.json')}")
        lines.append("")
    return "\n".join(lines)


BODIES = {
    "deep-research-agent": ("深度研究报告", body_048),
    "literature-review-writer": ("文献综述", body_049),
    "systematic-review-prisma": ("系统性文献综述报告", body_050),
    "research-frontier-trend-analysis": ("研究前沿与趋势分析报告", body_051),
    "research-idea-novelty-assessment": ("科研选题与创新性评估报告", body_052),
}


def _stmt(x):
    if isinstance(x, dict):
        return x.get("statement") or x.get("claim") or x.get("text") or ""
    return str(x) if x is not None else ""


def _first_sentence(x):
    text = _stmt(x).strip()
    for sep in ("。", "！", "？", ". "):
        i = text.find(sep)
        if 0 < i < 220:
            return text[: i + len(sep)]
    return text


def _direction_counts(rows):
    out = {"+1": 0, "-1": 0, "0": 0, "mixed": 0, "unknown": 0}
    for e in rows:
        d = e.get("direction")
        v = d.get("value") if isinstance(d, dict) else d
        v = str(v) if v is not None else ""
        if v in ("+1", "1", "positive"):
            out["+1"] += 1
        elif v in ("-1", "negative"):
            out["-1"] += 1
        elif v in ("0", "zero"):
            out["0"] += 1
        elif v in ("mixed", "±1"):
            out["mixed"] += 1
        else:
            out["unknown"] += 1
    return out


def _year_span(rows):
    years = []
    for e in rows:
        y = e.get("year")
        try:
            y = int(y)
        except (TypeError, ValueError):
            continue
        if 1900 <= y <= 2100:
            years.append(y)
    return (min(years), max(years)) if years else (None, None)


def summary_block(payload):
    """摘要：只重排 result.json 的计数、方向、年份与判定，不新增证据外结论。"""
    skill = (payload.get("skill") or {}).get("name", "")
    counts = (payload.get("screening") or {}).get("counts") or {}
    rows = payload.get("evidence_table") or payload.get("evidence_matrix") or []
    n_ev = len(rows)
    conf = payload.get("conflicts")
    n_conf = conf.get("count") if isinstance(conf, dict) else len(conf or [])
    gaps = payload.get("research_gaps")
    n_gap = gaps.get("count") if isinstance(gaps, dict) else len(gaps or [])
    lines = ["## 摘要", ""]
    if counts:
        lines.append(f"- 材料与筛选：筛查 {s(counts.get('screened'))} 条，纳入 {s(counts.get('included'))} 条，"
                     f"排除 {s(counts.get('excluded'))} 条。")
    if n_ev:
        verified = sum(1 for e in rows if e.get("quote_verified", True))
        y0, y1 = _year_span(rows)
        span = f"，年份跨度 {y0}–{y1}" if y0 else ""
        lines.append(f"- 证据台账：{n_ev} 条带定位的逐字证据（校验通过 {verified}/{n_ev}{span}）。")
        d = _direction_counts(rows)
        lines.append(f"- 方向分布：正向 {d['+1']} 条、负向 {d['-1']} 条、零效应 {d['0']} 条、"
                     f"混合 {d['mixed']} 条、未判定 {d['unknown']} 条。")
    lines.append(f"- 冲突证据：{s(n_conf, '0')} 组（双列呈现）；研究空白：{s(n_gap, '0')} 项。")
    if skill == "deep-research-agent":
        subs = (payload.get("research_plan") or {}).get("subquestions") or []
        if subs:
            lines.append(f"- 问题拆解：{len(subs)} 个子问题；检索计划 {len((payload.get('search_plan') or {}).get('steps') or [])} 步。")
        key = [_first_sentence(c) for c in (payload.get("conclusions") or [])[:3]]
    elif skill == "literature-review-writer":
        topics = payload.get("topics") or []
        labels = [t.get("label") for t in topics[:5]]
        if labels:
            lines.append(f"- 主题覆盖：{len(topics)} 个主题；" + "；".join(s(x) for x in labels))
        thin = [t.get("label") for t in topics if t.get("thin")]
        if thin:
            lines.append("- 证据薄弱主题：" + "；".join(s(x) for x in thin))
        sl = (payload.get("screening") or {}).get("search_log") or {}
        qs = sl.get("queries") or []
        if qs:
            main = next((q for q in qs if q.get("query") != "*"), qs[0])
            lines.append(f"- 检索覆盖：{len(qs)} 条检索式；主检索式 `{clip(main.get('query'), 80)}` "
                         f"命中 {s(main.get('hit_count'))} 条，池内全量 {s(sl.get('pool_size'))} 条（未联网）。")
        key = []
    elif skill == "systematic-review-prisma":
        pf = payload.get("prisma_flow") or {}
        ident = (pf.get("identification") or {}).get("records_identified")
        incl = (pf.get("included") or {}).get("records_included")
        closed = (pf.get("consistency") or {}).get("all_closed")
        if ident is not None or incl is not None:
            lines.append(f"- PRISMA 计数链：识别 {s(ident)} → 纳入 {s(incl)}；恒等式自检 {s(closed)}。")
        qa = payload.get("quality_appraisal")
        if isinstance(qa, dict):
            qa = qa.get("rows") or []
        qa = qa or []
        if qa:
            levels = {}
            for q in qa:
                lv = s((q.get("level") or q.get("quality_level")), "未评") if isinstance(q, dict) else "未评"
                levels[lv] = levels.get(lv, 0) + 1
            lines.append("- 质量分布：" + "；".join(f"{k} {v} 条" for k, v in sorted(levels.items())))
        key = []
    elif skill == "research-frontier-trend-analysis":
        tt = payload.get("temporal_trends") or {}
        win = tt.get("window") or {}
        if win:
            lines.append(f"- 时间窗口：{s(win.get('start'))}–{s(win.get('end'))}；"
                         f"计数单位：{clip(tt.get('counting_unit'), 80)}")
        claims = (payload.get("frontier_trends") or {}).get("claims") or []
        if claims:
            verdicts = {}
            for c in claims:
                v = s(c.get("verdict"), "unknown")
                verdicts[v] = verdicts.get(v, 0) + 1
            lines.append(f"- 主题判定：{len(claims)} 个主题；" + "；".join(f"{k} {v}" for k, v in sorted(verdicts.items())))
        key = [_first_sentence(c) for c in claims[:2]]
    elif skill == "research-idea-novelty-assessment":
        cas = payload.get("candidate_assessments") or []
        if cas:
            lines.append(f"- 候选方向：{len(cas)} 个。")
            verdicts = {}
            for c in cas:
                for cl in ((c.get("novelty") or {}).get("claims") or []):
                    v = s(cl.get("verdict"), "unknown")
                    verdicts[v] = verdicts.get(v, 0) + 1
            if verdicts:
                lines.append("- 新颖性判定：" + "；".join(f"{k} {v} 条" for k, v in sorted(verdicts.items())))
        key = []
    for k in key:
        if k:
            lines.append(f"- {clip(k, 220)}")
    lines.append("")
    lines.append("> 本摘要只重排 `result.json` 的计数、方向、年份与判定；证据与边界见后续各节，不在此处新增结论。")
    lines.append("")
    return "\n".join(lines)


def render(payload):
    skill = (payload.get("skill") or {}).get("name", "")
    title, body_fn = BODIES.get(skill, ("研究报告", None))
    parts = [header(payload, f"{title}｜{s(payload.get('case_id'), 'UNKNOWN-CASE')}")]
    parts.append(summary_block(payload))
    intro, findings = "", ""
    if body_fn:
        body = body_fn(payload)
        if "\n## 3." in body:
            intro, findings = body.split("\n## 3.", 1)
            findings = "## 3." + findings
        else:
            intro = body
    parts.append(intro)
    parts.append(screening_block(payload))
    parts.append(findings)
    parts.append(conflict_block(payload))
    parts.append(gap_block(payload))
    parts.append(evidence_block(payload))
    parts.append(status_block(payload))
    parts.append(reproduce_block(payload))
    text = "\n".join(p for p in parts if p)
    # 折叠多余空行
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.rstrip() + "\n"


def render_file(result_path, out_path=None):
    payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
    target = Path(out_path) if out_path else Path(result_path).with_name("report.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(payload), encoding="utf-8")
    return target


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render result.json to report.md")
    ap.add_argument("--result", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    path = render_file(args.result, args.out)
    print(json.dumps({"ok": True, "report": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
