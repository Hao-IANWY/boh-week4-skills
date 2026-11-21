"""kit.py —— 77 题通用 Skill 运行时内核（纯标准库，可整文件复制进任意题目录的 scripts/）。

设计目标（与赛制契约一一对应）：
1. 输入只认 `/input/case.json` + `task.md`，且允许 CLI 覆盖（便于本地复现与出示例）。
2. 输出必须落 `/app/output/result.json`，并**原样保留 `output_contract` 的全部字段 + `case_id`**。
3. 三段式：主结果（contract 字段）+ 证据记录（evidence）+ 状态说明（status/uncertainty）。
4. 不许编造：拿不到的字段一律显式落 `not_produced`，并把缺口写进 `status.unmet`，而不是填空字符串或猜值。

用法（每个题目的 scripts/pipeline.py 里）：
    from kit import Result, load_case, parse_args, tokens, stable_id

    args = parse_args()
    case = load_case(args.case)
    res = Result(case, skill="literature-search-planner")
    res.set("candidates", [...])
    res.log("dedupe", groups=3)
    res.evidence["sources"] = [...]
    res.status["overall"] = "complete"
    res.emit(args.out)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

SKILL_VERSION = "0.1.0"
DEFAULT_CASE = "/input/case.json"
DEFAULT_WORK = "/app/work"
DEFAULT_OUT = "/app/output"
DEFAULT_BUDGET_SECONDS = 900.0

STATUS_ENUM = ("complete", "partial", "empty", "input_error")
TODO_MARKERS = ("TODO", "FIXME", "待补充", "占位", "PLACEHOLDER", "TBD")

# contract 字段名 → 领域产物里的规范键名（各题可在 Result.bind 的 aliases 里覆盖）
_DEFAULT_ALIASES = {
    "evidence_map": "evidence",
    "证据记录": "evidence",
    "evidence": "evidence",
    "status": "status",
    "状态说明": "status",
    "change_log": "change_log",
    "candidates": "candidates",
    "检索结果": "candidates",
    "deliverable": "deliverable",
    "主结果": "deliverable",
    "preserved_facts": "preserved_facts",
    "uncertainty": "uncertainty",
}

# 关键词 → payload 里的规范键名候选（按顺序命中第一个存在的）
_KEYWORD_TABLE = (
    (("evidence", "证据", "来源定位", "溯源", "provenance", "trace"), ("evidence", "evidence_view")),
    (("uncertain", "不确定", "风险", "未知"), ("uncertainty",)),
    (("change", "变更", "修改记录", "changelog", "动作"), ("change_log",)),
    (("status", "状态"), ("status",)),
    (("preserve", "保全", "不改", "保真"), ("preserved_facts",)),
    (("candidate", "候选", "检索结果", "文献列表", "records"), ("candidates",)),
    (("claim", "assert", "论断", "结论"), ("claims",)),
    (("slide", "页"), ("slides",)),
    (("deck", "演示"), ("deck",)),
    (("figure", "图"), ("figures",)),
    (("table", "表"), ("tables",)),
    (("diagram", "示意图"), ("diagrams",)),
    (("deliver", "产物", "交付", "draft", "文稿"), ("deliverable",)),
)


def _heuristic_match(key: str, payload: dict):
    kn = norm(key).replace("_", " ").strip()
    for kws, cands in _KEYWORD_TABLE:
        # “页 / 图 / 表”等单字只能精确匹配。否则“页面说明”“图表映射”
        # 一类契约会被误绑到 slides/figures，静默覆盖真正的主结果。
        if any((kw == kn) if len(kw) < 2 else (kw in kn) for kw in kws):
            for c in cands:
                if c in payload:
                    return c
    for pk in payload:
        pn = norm(pk).replace("_", " ").strip()
        if pn and (pn in kn or kn in pn):
            return pk
    return None


# --------------------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------------------
class ContractError(RuntimeError):
    """契约不满足（缺 case_id / 缺 output_contract 字段等）。"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_json_reference(value, base_dir="."):
    """读取内联 JSON 值或 case 引用的 JSON 文件；dict/list 原样返回。"""
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return {}
    path = Path(str(value))
    if not path.is_absolute():
        path = Path(base_dir) / path
    return load_json(path, default={})


def sqlite_table_names(path) -> list:
    """只读列出 SQLite 用户表，供真实数据库快照适配使用。"""
    con = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    try:
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    finally:
        con.close()


def resolve_table_ref(ref, base_dir="."):
    """把数据表引用解析为实际文件路径。

    相对路径优先按 case 所在目录解析；case 被 Agent 迁移到 /app/work 等目录时，
    依序回退查找各级祖先目录下的 input/、当前工作目录与 /input（Harbor 布局）。
    返回 (首个存在的候选, 全部候选)；全部不存在时返回首个候选供错误信息使用。
    """
    raw = Path(str(ref))
    if raw.is_absolute():
        return raw, [raw]
    bases: list[Path] = []
    if base_dir:
        base = Path(base_dir)
        bases.append(base)
        bases.extend(ancestor / "input" for ancestor in base.parents)
    bases.append(Path.cwd())
    bases.append(Path("/input"))
    candidates, seen = [], set()
    for base in bases:
        candidate = base / raw
        text = str(candidate)
        if text in seen:
            continue
        seen.add(text)
        candidates.append(candidate)
    for candidate in candidates:
        if candidate.exists():
            return candidate, candidates
    return candidates[0], candidates


def read_table_snapshot(value, base_dir=".", table_name="") -> dict:
    """把内联表或 CSV/TSV/JSON/SQLite 文件归一为 columns/rows。

    SQLite 始终以只读 URI 打开；未指定表名时取第一个用户表。返回 source_path
    供证据记录使用，不把文件标签当作数据内容。
    """
    if isinstance(value, dict) and not first_present(value, "path", "file", "csv_path", "sqlite_path", "database_path"):
        rows = as_list(first_present(value, "rows", "records", "data", default=[]))
        raw_columns = as_list(first_present(value, "columns", "header", default=[]))
        columns = [str(first_present(c, "name", "field", default="")) if isinstance(c, dict) else str(c)
                   for c in raw_columns]
        if not columns:
            for row in rows:
                if isinstance(row, dict):
                    columns = [str(k) for k in row]
                    break
        normalized = []
        for row in rows:
            if isinstance(row, dict):
                normalized.append({c: row.get(c) for c in columns})
            elif isinstance(row, (list, tuple)):
                normalized.append({c: row[i] if i < len(row) else None for i, c in enumerate(columns)})
        return {"name": str(first_present(value, "name", "dataset_id", default="dataset")),
                "columns": columns, "rows": normalized, "source_path": None}
    ref = value if isinstance(value, (str, Path)) else first_present(
        value, "path", "file", "csv_path", "sqlite_path", "database_path", default="")
    path, candidates = resolve_table_ref(ref, base_dir)
    if not path.is_file():
        tried = "；".join(str(candidate) for candidate in candidates)
        raise ContractError(f"数据表文件不存在：{ref}（尝试过：{tried}）")
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t" if suffix == ".tsv" else ",")
            rows = list(reader)
            columns = list(reader.fieldnames or [])
    elif suffix == ".json":
        return {**read_table_snapshot(load_json(path), path.parent, table_name),
                "source_path": str(path)}
    elif suffix in (".db", ".sqlite", ".sqlite3"):
        table = table_name or (sqlite_table_names(path) or [""])[0]
        if not table:
            raise ContractError(f"SQLite 无用户表：{path}")
        con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            quoted = table.replace('"', '""')
            rows = [dict(r) for r in con.execute(f'SELECT * FROM "{quoted}"')]
            columns = [d[0] for d in con.execute(f'SELECT * FROM "{quoted}" LIMIT 0').description]
        finally:
            con.close()
        return {"name": table, "columns": columns, "rows": rows, "source_path": str(path)}
    else:
        raise ContractError(f"不支持的数据表文件：{path}")
    return {"name": path.stem, "columns": columns, "rows": rows, "source_path": str(path)}


def write_json(path, obj) -> Path:
    p = Path(path)
    ensure_dir(p.parent)
    with p.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")
    return p


def read_text(path) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="replace")


def stable_id(*parts, n: int = 10) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:n]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def norm(text) -> str:
    """全角→半角、去多余空白、casefold，用于匹配与去重。"""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def norm_title(text) -> str:
    """标题去重用的强归一：只留字母数字与 CJK。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", norm(text))


_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9\-_\.\+]*")


def tokens(text, unique: bool = True) -> list:
    """英文按词、中文按二元组切分。

    默认返回去重后的有序列表，供重合度与查询词使用。BM25 的文档侧必须传
    ``unique=False`` 保留词频和真实文档长度，否则 tf 恒为 1，排序会失真。
    """
    s = norm(text)
    out, seen = [], set()

    def push(tok):
        if tok and len(tok) > 1 and (not unique or tok not in seen):
            seen.add(tok)
            out.append(tok)

    for m in _ASCII_WORD.finditer(s):
        push(m.group(0))
    for run in _CJK.findall(s):
        pass
    for run in re.findall(r"[\u4e00-\u9fff]+", s):
        if len(run) == 1:
            push(run)
        for i in range(len(run) - 1):
            push(run[i : i + 2])
    return out


def jaccard(a, b) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def snippet(text: str, start: int = 0, end: int = 0, width: int = 80) -> str:
    """截取并加省略号的引文片段，用于证据记录。"""
    if not text:
        return ""
    start = max(0, min(start, len(text)))
    end = max(start, min(end or start + width, len(text)))
    frag = text[start:end].strip()
    if start > 0:
        frag = "…" + frag
    if end < len(text):
        frag = frag + "…"
    return frag


def clamp(x, lo=0.0, hi=1.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        v = lo
    if math.isnan(v) or math.isinf(v):
        v = lo
    return max(lo, min(hi, v))


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return [value]


def deep_get(obj, *keys, default=None):
    """按路径取字段，任一环节缺失就返回 default。"""
    cur = obj
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        elif isinstance(cur, list) and isinstance(k, int) and 0 <= k < len(cur):
            cur = cur[k]
        else:
            return default
    return cur


def first_present(obj, *keys, default=None):
    """在 dict 里按候选键名依次尝试（题面字段命名不统一时用）。"""
    if not isinstance(obj, dict):
        return default
    for k in keys:
        if k in obj and obj[k] not in (None, "", [], {}):
            return obj[k]
    return default


def flatten_text(value, limit: int = 200000) -> str:
    """把任意材料（str / list / dict / 带 text 字段的对象）压平成文本。"""
    chunks = []

    def walk(v):
        if len("".join(chunks)) > limit:
            return
        if v is None:
            return
        if isinstance(v, str):
            chunks.append(v)
        elif isinstance(v, (int, float)):
            chunks.append(str(v))
        elif isinstance(v, dict):
            for key in ("text", "content", "body", "markdown", "value", "sections", "paragraphs", "lines"):
                if key in v:
                    walk(v[key])
                    return
            for vv in v.values():
                walk(vv)
        elif isinstance(v, (list, tuple)):
            for vv in v:
                walk(vv)

    walk(value)
    return "\n".join(chunks)[:limit]


# --------------------------------------------------------------------------------------
# 输入加载
# --------------------------------------------------------------------------------------
def parse_args(argv=None, *, case: str = DEFAULT_CASE, work: str = DEFAULT_WORK, out: str = DEFAULT_OUT):
    ap = argparse.ArgumentParser(description="Skill pipeline runner")
    ap.add_argument("--case", default=os.environ.get("SKILL_CASE", case), help="case.json 路径")
    ap.add_argument("--work", default=os.environ.get("SKILL_WORK", work), help="工作区（放中间件）")
    ap.add_argument("--out", default=os.environ.get("SKILL_OUT", out), help="产物目录（写 result.json）")
    ap.add_argument("--task", default=None, help="task.md 路径（可省略，默认取 case 同目录）")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_SECONDS, help="时间预算（秒）")
    ap.add_argument("--dry-run", action="store_true", help="只算不落盘，打印摘要")
    ap.add_argument("--self-check", action="store_true", help="跑内置自检后退出")
    return ap.parse_args(argv)


def load_case(path) -> dict:
    """读 case.json，容忍两种外形：裸 case，或 {"case": {...}}。缺文件抛 ContractError。"""
    p = Path(path)
    if not p.exists():
        raise ContractError(f"case.json 不存在：{p}")
    data = load_json(p, default=None)
    if data is None:
        raise ContractError(f"case.json 无法解析：{p}")
    if isinstance(data, dict) and isinstance(data.get("case"), dict):
        data = data["case"]
    if not isinstance(data, dict):
        raise ContractError("case.json 顶层不是对象")
    return data


def contract_of(case: dict) -> dict:
    """取 output_contract：key=结果字段名，value=该字段的要求（原样保留 Requirement 文本）。"""
    raw = case.get("output_contract") or case.get("outputContract") or {}
    if isinstance(raw, list):
        raw = {str(k): "" for k in raw}
    if not isinstance(raw, dict):
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in raw.items()}


def case_id_of(case: dict) -> str:
    cid = first_present(case, "case_id", "caseId", "id")
    return str(cid) if cid not in (None, "") else "UNKNOWN-CASE"


def load_task_text(args, case: dict) -> str:
    cands = []
    if getattr(args, "task", None):
        cands.append(args.task)
    case_path = Path(args.case)
    cands += [case_path.parent / "task.md", case_path.parent / "TASK.md"]
    for c in cands:
        txt = read_text(c)
        if txt.strip():
            return txt
    return ""


def resolve_material(case: dict, work, *keys):
    """把 case 里的材料引用解析为本地路径；支持字符串路径 / {"path":...} / 列表。"""
    base = Path(work or ".")
    found = []
    for k in keys:
        val = case.get(k)
        for item in as_list(val):
            if isinstance(item, str):
                cand = Path(item)
                found.append(cand if cand.is_absolute() else (base / cand))
            elif isinstance(item, dict):
                for kk in ("path", "file", "filepath", "url", "location"):
                    if isinstance(item.get(kk), str):
                        cand = Path(item[kk])
                        found.append(cand if cand.is_absolute() else (base / cand))
                        break
    return found


# --------------------------------------------------------------------------------------
# 预算 / 结果构造
# --------------------------------------------------------------------------------------
class Clock:
    def __init__(self, budget_seconds: float = DEFAULT_BUDGET_SECONDS):
        self.budget = float(budget_seconds)
        self.t0 = time.time()

    def elapsed(self) -> float:
        return time.time() - self.t0

    def remaining(self) -> float:
        return max(0.0, self.budget - self.elapsed())

    def out_of_budget(self) -> bool:
        return self.remaining() <= 0.0


class Result:
    """契约驱动的结果容器：填 contract 字段 + 证据 + 状态，最后统一落盘。"""

    def __init__(self, case: dict, skill: str, clock: Clock | None = None):
        self.case = case or {}
        self.skill = skill
        self.case_id = case_id_of(self.case)
        self.contract = contract_of(self.case)
        self._contract_orig = dict(self.contract)
        self.clock = clock or Clock()
        self.fields: dict = {}
        self.evidence: dict = {}
        self.status: dict = {"overall": "partial", "completed": [], "unmet": [], "notes": []}
        self.uncertainty: list = []
        self.run_log: list = []

    # --- 写入 ---
    def set(self, key: str, value):
        self.fields[key] = value
        if key in self.contract:
            self.contract.pop(key, None)
        return self

    def log(self, step: str, **kv):
        # 只记步骤与结构化参数，不记耗时：保证同一输入重复运行结果逐字节一致
        entry = {"step": step, "seq": len(self.run_log) + 1}
        entry.update({k: v for k, v in kv.items()})
        self.run_log.append(entry)
        return self

    def done(self, *items):
        for it in items:
            if it not in self.status["completed"]:
                self.status["completed"].append(it)
        return self

    def unmet(self, item: str, reason: str):
        self.status["unmet"].append({"item": item, "reason": reason})
        if self.status["overall"] == "complete":
            self.status["overall"] = "partial"
        return self

    def note(self, text: str):
        self.status["notes"].append(text)
        return self

    def unsure(self, item: str, why: str):
        self.uncertainty.append({"item": item, "reason": why})
        return self

    def bind(self, payload: dict, aliases: dict | None = None):
        """把领域产物绑定到 output_contract 字段。

        匹配顺序：精确同名 → 显式别名 → 关键词启发（字段名互相包含）。
        允许把证据/状态类虚拟键（evidence / evidence_view / status / uncertainty / change_log）
        直接映射到本容器自带的三段式内容。未被 contract 消费的 payload 会整体挂在 `result` 下，
        保证信息不丢。
        """
        alias = {**_DEFAULT_ALIASES, **{k: v for k, v in (aliases or {}).items()}}
        virtual = ("evidence", "evidence_view", "status", "uncertainty", "change_log")
        consumed = set()

        def _value(name):
            if name in payload:
                consumed.add(name)
                return payload[name]
            if name in ("evidence", "evidence_view"):
                return self.evidence_view()
            if name == "status":
                return self.status
            if name == "uncertainty":
                return self.uncertainty
            if name == "change_log":
                return self.status.get("change_log", [])
            return None

        for key in list(self.contract.keys()):
            if key in payload:
                consumed.add(key)
                self.set(key, payload[key])
                continue
            mapped = alias.get(key)
            if mapped and (mapped in payload or mapped in virtual):
                self.set(key, _value(mapped))
                continue
            hit = _heuristic_match(key, payload)
            if hit is not None:
                self.set(key, _value(hit))
        rest = {k: v for k, v in payload.items() if k not in consumed and k not in self.contract}
        if rest:
            self.fields.setdefault("result", rest)
        return self

    def evidence_view(self) -> dict:
        """给 contract 里的"证据/来源"类字段用的可复核视图。"""
        return {k: v for k, v in self.evidence.items() if k != "run_log"}

    # --- 收尾 ---
    def missing_contract(self) -> list:
        return [k for k in self.contract.keys() if k not in self.fields]

    def _fill_missing(self):
        for key, requirement in list(self.contract.items()):
            if key in self.fields:
                continue
            self.fields[key] = {
                "status": "not_produced",
                "requirement": requirement,
                "reason": "本题流程未产出该字段，缺口已记入 status.unmet，未以推测值填充",
            }
            self.unmet(key, "流程未产出，按契约显式声明缺口")

    def build(self) -> dict:
        self._fill_missing()
        self.evidence.setdefault("contract", dict(self._contract_orig))
        self.evidence.setdefault("run_log", self.run_log)
        self.evidence.setdefault("cases_seen", [self.case_id])
        overall = self.status.get("overall", "partial")
        if overall not in STATUS_ENUM:
            overall = "partial"
        # 最终状态以完整结果为准，而不是以某次赋值顺序为准：pipeline 可能
        # 先记 unmet、后写 complete，也可能在 build() 时才补齐缺失契约。
        if overall == "complete" and self.status.get("unmet"):
            overall = "partial"
            self.note("存在未完成项，最终状态由 complete 校正为 partial")
        self.status["overall"] = overall
        out = {
            "case_id": self.case_id,
            "skill": {"name": self.skill, "version": SKILL_VERSION},
            "generated_at": now_iso(),
        }
        out.update(self.fields)
        out["evidence"] = self.evidence
        out["status"] = self.status
        out["uncertainty"] = self.uncertainty
        return out

    def emit(self, out_dir=DEFAULT_OUT, filename: str = "result.json"):
        payload = self.build()
        path = Path(out_dir) / filename
        write_json(path, payload)
        return path


# --------------------------------------------------------------------------------------
# 通用打分与排序（多个题目复用：文献排序、方案比较、段落定位）
# --------------------------------------------------------------------------------------
def bm25_score(query_tokens, doc_tokens, corpus_docs, k1: float = 1.2, b: float = 0.75) -> float:
    """单文档 BM25，语料统计从传入的 token 列表集合里算，保证可复算。"""
    q = list(dict.fromkeys(query_tokens))
    d = list(doc_tokens)
    if not q or not d:
        return 0.0
    n_docs = max(1, len(corpus_docs))
    avgdl = sum(len(x) for x in corpus_docs) / n_docs if corpus_docs else len(d)
    avgdl = avgdl or 1.0
    dl = len(d)
    tf = {}
    for t in d:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for term in q:
        if term not in tf:
            continue
        df = sum(1 for x in corpus_docs if term in x) if corpus_docs else 1
        idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
        f = tf[term]
        score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
    return round(score, 4)


def top_k(items, k: int, key=lambda x: x[0]):
    return sorted(items, key=key, reverse=True)[: max(0, int(k))]


def rounded(x, nd: int = 4) -> float:
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------------------
def self_check() -> int:
    case = {
        "case_id": "SELFTEST-1",
        "output_contract": {"deliverable": "产物", "evidence_map": "映射"},
        "materials": [{"path": "a.md", "text": "Graph neural network for retrosynthesis."}],
    }
    res = Result(case, skill="kit-selftest")
    res.set("deliverable", {"ok": True})
    res.log("selftest")
    res.status["overall"] = "complete"  # build() 必须根据缺失契约校正，而非相信赋值顺序
    payload = res.build()
    assert payload["case_id"] == "SELFTEST-1"
    assert payload["deliverable"] == {"ok": True}
    assert payload["evidence_map"]["status"] == "not_produced", payload["evidence_map"]
    assert any(u["item"] == "evidence_map" for u in payload["status"]["unmet"])
    assert payload["status"]["overall"] == "partial"
    assert _heuristic_match("图表映射", {"figures": [1], "tables": [2]}) is None
    assert _heuristic_match("图", {"figures": [1]}) == "figures"
    assert "case_id" in payload and "evidence" in payload and "status" in payload
    toks = tokens("Graph Neural Network 逆合成 预测")
    assert "graph" in toks and "逆合" in toks, toks
    assert tokens("graph graph", unique=False) == ["graph", "graph"]
    repeated = bm25_score(["graph"], ["graph", "graph", "net"], [["graph", "net"], ["x"]])
    single = bm25_score(["graph"], ["graph", "net", "net"], [["graph", "net"], ["x"]])
    assert repeated > single > 0  # 相同 doc/token 集下，更高 tf 不应被去重抹掉
    assert norm_title("A Study of GNNs!") == "astudyofgnns"
    assert snippet("abcdef", 2, 4) == "…cd…"
    assert clamp(float("nan")) == 0.0
    print("kit self-check OK")
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.self_check:
        return self_check()
    print(json.dumps({"kit": SKILL_VERSION, "case": args.case, "out": args.out}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
